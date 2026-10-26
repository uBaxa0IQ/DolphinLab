"""Resource-accounted durable jobs; no ML policy or automatic replay of unknown work."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

import psutil

from research_store import dumps, uid
from research_worker import atomic_json, kill_tree

TERMINAL = {"succeeded", "failed", "cancelled", "timed_out", "memory_exceeded", "interrupted"}


class Jobs:
    def __init__(self, config, store, notify):
        self.config, self.store, self.notify = config, store, notify
        self.tasks, self.processes = {}, {}
        self.used_cpu = self.used_ram = 0
        self.changed = asyncio.Event()
        self.stopping = False
        self.source_root = Path(__file__).resolve().parent

    def submit(self, owner, script, args, cpus=2, ram_mb=2048, timeout=600,
               kind="research", metadata=None, source_dir=None):
        if self.stopping or self.config.remaining < 1:
            raise ValueError("Execution is shutting down")
        if self.config.finalizing and kind == "research":
            raise ValueError("Finalization phase: use evaluation/finalization or discuss the reserve")
        if not 1 <= cpus <= self.config.cpus or not 64 <= ram_mb <= self.config.ram_mb:
            raise ValueError("Resource request exceeds task budget")
        if kind not in ("research", "evaluation", "finalization"):
            raise ValueError("Unknown job kind")
        ident = uid("job")
        folder = self.config.work_dir / "jobs" / ident
        code = folder / "code"
        code.mkdir(parents=True)
        script = Path(script).resolve()
        if source_dir:
            source_dir = Path(source_dir).resolve()
            relative = script.relative_to(source_dir)
            for path in source_dir.rglob("*.py"):
                target = code / path.relative_to(source_dir)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
            command = code / relative
        else:
            command = code / script.name
            shutil.copy2(script, command)
        if not command.is_file():
            raise ValueError("Python script does not exist")
        fingerprint = hashlib.sha256()
        # A repeated entry point is only a signal: dependencies, data and stochastic states may differ.
        # Do not let random names of inline scripts hide that the same source was submitted again.
        fingerprint.update(command.read_bytes())
        fingerprint.update(dumps({"args": args, "protocol_id": (metadata or {}).get("protocol_id")}).encode())
        job = {"id": ident, "token": uid("worker"), "owner": owner, "state": "queued", "kind": kind,
               "cpus": cpus, "ram_mb": ram_mb, "timeout": min(max(.1, timeout), self.config.remaining),
               "script": str(command), "args": [str(a) for a in args], "folder": str(folder),
               "metadata": metadata or {}, "submitted": time.time(), "source_fingerprint": fingerprint.hexdigest()}
        self.store.put("job", ident, job)
        self.store.event("job_queued", job)
        self.tasks[ident] = asyncio.create_task(self._run(ident))
        self.changed.set()
        return job

    def _first(self, ident):
        def rank(item):
            base = {"finalization": 0, "evaluation": 1, "research": 2}[item["kind"]]
            age = max(0., time.time() - item["submitted"])
            return max(0, base - int(age // 120)), item["submitted"]
        queued = [j for j in self.store.all("job") if j["state"] == "queued"]
        return not queued or min(queued, key=rank)["id"] == ident

    @staticmethod
    def _json(path):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _result(self, job):
        result = self._json(Path(job["folder"]) / "result.json")
        if result and result.get("job_id") == job["id"] and result.get("token") == job.get("token"):
            return result
        return None

    def _identity(self, job):
        record = self._json(Path(job["folder"]) / "process.json")
        if not record or record.get("job_id") != job["id"] or record.get("token") != job.get("token"):
            return None
        try:
            proc = psutil.Process(record["pid"])
            if abs(proc.create_time() - record["created"]) > .05 or not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
                return None
            args = proc.cmdline()
            manifest = str(Path(job["folder"]) / "job.json")
            if manifest not in args:
                return None
            return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None

    async def _stop(self, job):
        (Path(job["folder"]) / "cancel.request").touch()
        for _ in range(10):
            if self._result(job) or not self._identity(job):
                break
            await asyncio.sleep(.2)
        proc = self._identity(job)
        if proc:
            kill_tree(proc.pid)
        else:
            self._stop_orphan_script(job)

    def _stop_orphan_script(self, job):
        identity = self._json(Path(job["folder"]) / "process.json")
        if not identity or identity.get("token") != job.get("token") or not identity.get("script_created"):
            return
        try:
            proc = psutil.Process(identity["script_pid"])
            if abs(proc.create_time() - identity["script_created"]) < .05 and job["script"] in proc.cmdline():
                kill_tree(proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    async def _run(self, ident, adopted=False):
        job = self.store.get("job", ident)
        acquired = adopted
        process = None
        try:
            if not adopted:
                while (not self._first(ident) or self.used_cpu + job["cpus"] > self.config.cpus
                       or self.used_ram + job["ram_mb"] > self.config.ram_mb):
                    if self.stopping or self.store.get("job", ident)["state"] == "cancelled":
                        raise asyncio.CancelledError
                    self.changed.clear()
                    await self.changed.wait()
                if self.stopping or (Path(job["folder"]) / "cancel.request").exists() or self.store.get("job", ident)["state"] == "cancelled":
                    raise asyncio.CancelledError
                self.used_cpu += job["cpus"]
                self.used_ram += job["ram_mb"]
                acquired = True
                if self.config.finalizing and job["kind"] == "research":
                    raise asyncio.CancelledError
                manifest = {**job, "deadline_wall": self.config.deadline_wall or time.time() + self.config.remaining,
                            "log_bytes": self.config.log_bytes}
                atomic_json(Path(job["folder"]) / "job.json", manifest)
                # Persist launch intent before yielding to OS process creation. Such an interrupted
                # launch is never replayed as though it were unambiguously unstarted.
                job["state"] = "launching"
                self.store.put("job", ident, job)
                env = {k: v for k, v in os.environ.items() if not re.search(r"API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", k, re.I)}
                env.update({k: str(job["cpus"]) for k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS")})
                env.update(PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", TASK_ROOT=str(self.config.task_path.parent),
                           JOB_DIR=job["folder"], RESEARCH_CPU=str(job["cpus"]), PYTHONPATH=str(self.source_root))
                process = await asyncio.create_subprocess_exec(sys.executable, "-u", str(self.source_root / "research_worker.py"),
                    str(Path(job["folder"]) / "job.json"), cwd=job["folder"], env=env,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    **({"start_new_session": True} if os.name != "nt" else {}))
                self.processes[ident] = process
                if self.stopping or self.store.get("job", ident)["state"] == "cancelled":
                    raise asyncio.CancelledError
                job.update(state="running", pid=process.pid, started=time.time())
                self.store.put("job", ident, job)
                self.store.event("job_started", {"id": ident, "pid": process.pid, "owner": job["owner"]})
            while True:
                result = self._result(job)
                if result:
                    job.update(result)
                    break
                if self.config.remaining < .5:
                    await self._stop(job)
                    job["state"] = "timed_out"
                    break
                alive = process.returncode is None if process else bool(self._identity(job))
                if not alive:
                    # A final manifest may appear just before process exit.
                    result = self._result(job)
                    if result:
                        job.update(result)
                    else:
                        job.update(state="interrupted", error="Worker exited without a complete result; inspect artifacts before deciding to rerun")
                    break
                await asyncio.sleep(.2)
        except asyncio.CancelledError:
            await self._stop(job)
            result = self._result(job)
            if result:
                job.update(result)
            else:
                job["state"] = "cancelled"
        except Exception as error:
            await self._stop(job)
            job.update(state="failed", error=f"{type(error).__name__}: {error}")
        finally:
            if process:
                try:
                    await asyncio.wait_for(process.wait(), 3)
                except asyncio.TimeoutError:
                    await self._stop(job)
            if acquired:
                self.used_cpu -= job["cpus"]
                self.used_ram -= job["ram_mb"]
            job.setdefault("finished", time.time())
            self.store.put("job", ident, job)
            self.processes.pop(ident, None)
            self.changed.set()
            await self.notify(job)

    async def recover(self, active_owners=None):
        for job in self.store.all("job"):
            if job["state"] in TERMINAL:
                continue
            if active_owners is not None and job["owner"] not in active_owners:
                await self._stop(job)
                job.update(state="cancelled", error="Owning team/session was already closed")
                self.store.put("job", job["id"], job)
                continue
            if self.config.remaining < 1:
                await self._stop(job)
                job.update(state="timed_out", error="Original task deadline elapsed; recovery cannot extend it")
                self.store.put("job", job["id"], job)
                continue
            result = self._result(job)
            if result:
                job.update(result)
                self.store.put("job", job["id"], job)
                await self.notify(job)
            elif self._identity(job):
                job.update(state="running", recovered=True)
                self.store.put("job", job["id"], job)
                self.used_cpu += job["cpus"]
                self.used_ram += job["ram_mb"]
                self.tasks[job["id"]] = asyncio.create_task(self._run(job["id"], adopted=True))
            elif job["state"] == "queued":
                self.tasks[job["id"]] = asyncio.create_task(self._run(job["id"]))
            else:
                # Cancels even a very late worker startup. Do not blindly repeat an unknown launch.
                (Path(job["folder"]) / "cancel.request").touch()
                self._stop_orphan_script(job)
                job.update(state="interrupted", error="No verified live worker/result; partial artifacts retained, no automatic retraining")
                self.store.put("job", job["id"], job)
                await self.notify(job)

    def cancel(self, ident):
        job = self.store.get("job", ident)
        requested = job["state"] not in TERMINAL
        if requested:
            (Path(job["folder"]) / "cancel.request").touch()
            if job["state"] == "queued":
                job["state"] = "cancelled"
                self.store.put("job", ident, job)
            elif ident in self.tasks:
                self.tasks[ident].cancel()
            self.changed.set()
        return {"id": ident, "cancellation_requested": requested}

    async def shutdown(self):
        self.stopping = True
        for ident, task in self.tasks.items():
            if not task.done():
                self.cancel(ident)
        self.changed.set()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
