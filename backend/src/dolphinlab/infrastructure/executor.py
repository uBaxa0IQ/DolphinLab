"""Research jobs on the isolated executor, with durable identity and shared resource ceilings."""

import asyncio
import os
import time
from pathlib import Path

import httpx

from dolphinlab.common.files import atomic_json
from dolphinlab.research.jobs import TERMINAL, JobQueue


class RemoteJobs(JobQueue):
    def __init__(self, config, store, notify):
        super().__init__(config, store, notify)
        self.remote = httpx.AsyncClient(
            base_url=os.environ["EXECUTOR_URL"],
            headers={"X-Executor-Token": os.environ["EXECUTOR_TOKEN"]},
            timeout=20,
        )
        self.paused = False
        self.preserve_queued = False
        self.parallel_jobs = config.platform_parallel_jobs
        self.running_jobs = 0

    async def request(self, action, job):
        response = await self.remote.post(
            "/" + action,
            json={
                "manifest": str(Path(job["folder"]) / "job.json"),
                "token": job["token"],
            },
        )
        response.raise_for_status()
        return response.json()

    async def _run(self, ident, adopted=False):
        job = self.store.get("job", ident)
        acquired = False
        try:
            while not adopted and (
                self.paused
                or self.running_jobs >= self.parallel_jobs
                or not self._first(ident)
                or self.used_cpu + job["cpus"] > self.config.cpus
                or self.used_ram + job["ram_mb"] > self.config.ram_mb
            ):
                if self.stopping:
                    raise asyncio.CancelledError
                await asyncio.sleep(0.2)
            self.used_cpu += job["cpus"]
            self.used_ram += job["ram_mb"]
            self.running_jobs += 1
            acquired = True
            path = Path(job["folder"]) / "job.json"
            path.parent.chmod(0o770)
            (path.parent / "tmp").mkdir(exist_ok=True, mode=0o770)
            (path.parent / "tmp").chmod(0o770)
            if not path.exists():
                atomic_json(
                    path,
                    {
                        **job,
                        "deadline_wall": self.config.deadline_wall,
                        "log_bytes": self.config.log_bytes,
                    },
                )
            action = "inspect" if adopted else "execute"
            if not adopted:
                job["state"] = "launching"
                self.store.put("job", ident, job)
            while True:
                if self.config.remaining < 0.5:
                    await self.request("cancel", job)
                    job["state"] = "timed_out"
                    break
                try:
                    status = await self.request(action, job)
                    # A lost launch response is reconciled by executor identity; execute is idempotent.
                    action = "execute" if status["state"] == "queued" and not adopted else "inspect"
                except httpx.TransportError:
                    await asyncio.sleep(1)
                    continue
                if status.get("result"):
                    job.update(status["result"])
                    break
                if status["state"] == "interrupted" or (adopted and status["state"] == "queued"):
                    job.update(
                        state="interrupted",
                        error="Unknown prior launch outcome; inspect artifacts before rerunning",
                    )
                    break
                if job["state"] != "running":
                    job.update(state="running", started=job.get("started", time.time()))
                    self.store.put("job", ident, job)
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            if self.preserve_queued and not acquired:
                return
            try:
                if (Path(job["folder"]) / "job.json").exists():
                    await self.request("cancel", job)
            except Exception:
                (Path(job["folder"]) / "cancel.request").touch()
            for _ in range(20):
                result = self._result(job)
                if result:
                    job.update(result)
                    break
                await asyncio.sleep(0.25)
            else:
                job["state"] = "cancelled"
        except Exception as error:
            job.update(state="failed", error=type(error).__name__)
        finally:
            if acquired:
                self.used_cpu -= job["cpus"]
                self.used_ram -= job["ram_mb"]
                self.running_jobs -= 1
            if not (self.preserve_queued and not acquired):
                job.setdefault("finished", time.time())
                self.store.put("job", ident, job)
                await self.notify(job)
            self.changed.set()

    async def recover(self, active_owners=None):
        for job in self.store.all("job"):
            if job["state"] not in TERMINAL:
                if active_owners is not None and job["owner"] not in active_owners:
                    (Path(job["folder"]) / "cancel.request").touch()
                    job.update(state="cancelled", error="Owning session was already closed")
                    self.store.put("job", job["id"], job)
                    continue
                self.tasks[job["id"]] = asyncio.create_task(
                    self._run(job["id"], adopted=job["state"] != "queued")
                )

    def cancel(self, ident):
        job = self.store.get("job", ident)
        (Path(job["folder"]) / "cancel.request").touch()
        if ident in self.tasks:
            self.tasks[ident].cancel()
        return {"id": ident, "cancellation_requested": job["state"] not in TERMINAL}

    async def shutdown(self):
        self.stopping = True
        for task in self.tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        await self.remote.aclose()
