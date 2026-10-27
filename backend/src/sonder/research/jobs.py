"""Job queue and immutable source snapshots, independent of process transport."""

import asyncio
import hashlib
import json
import shutil
import time
from abc import ABC, abstractmethod
from pathlib import Path

from sonder.common.serialization import dumps, uid

TERMINAL = {"succeeded", "failed", "cancelled", "timed_out", "memory_exceeded", "interrupted"}


class JobQueue(ABC):
    def __init__(self, config, store, notify):
        self.config, self.store, self.notify = config, store, notify
        self.tasks = {}
        self.used_cpu = self.used_ram = 0
        self.changed = asyncio.Event()
        self.stopping = False

    def submit(
        self,
        owner,
        script,
        args,
        cpus=2,
        ram_mb=2048,
        timeout=600,
        kind="research",
        metadata=None,
        source_dir=None,
    ):
        if self.stopping or self.config.remaining < 1:
            raise ValueError("Execution is shutting down")
        if self.config.finalizing and kind == "research":
            raise ValueError(
                "Finalization phase: use evaluation/finalization or discuss the reserve"
            )
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
        fingerprint.update(
            dumps({"args": args, "protocol_id": (metadata or {}).get("protocol_id")}).encode()
        )
        job = {
            "id": ident,
            "token": uid("worker"),
            "owner": owner,
            "state": "queued",
            "kind": kind,
            "cpus": cpus,
            "ram_mb": ram_mb,
            "timeout": min(max(0.1, timeout), self.config.remaining),
            "script": str(command),
            "source_path": str(script),
            "args": [str(a) for a in args],
            "folder": str(folder),
            "metadata": metadata or {},
            "submitted": time.time(),
            "source_fingerprint": fingerprint.hexdigest(),
        }
        self.store.put("job", ident, job)
        self.store.event("job_queued", job)
        self.tasks[ident] = asyncio.create_task(self._run(ident))
        self.changed.set()
        return job

    def _first(self, ident):
        def rank(item):
            base = {"finalization": 0, "evaluation": 1, "research": 2}[item["kind"]]
            age = max(0.0, time.time() - item["submitted"])
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

    @abstractmethod
    async def _run(self, ident, adopted=False):
        """Execute or reconcile one queued job."""
        raise NotImplementedError
