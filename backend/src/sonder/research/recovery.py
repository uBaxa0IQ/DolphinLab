"""Exclusive run ownership and a clock that survives process restarts. No credentials persisted."""

from __future__ import annotations

import hashlib
import os
import time

from sonder.common.serialization import uid


class RunLease:
    """OS releases the byte/file lock on crash. Never unlink the lock inode while contenders wait."""

    def __init__(self, folder):
        self.stream = (folder / "run.lock").open("a+b")
        self.locked = False
        try:
            self.stream.seek(0, 2)
            if not self.stream.tell():
                self.stream.write(b"0")
                self.stream.flush()
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.locked = True
        except OSError as error:
            self.stream.close()
            raise ValueError(
                "This run is owned by another process; do not start a second coordinator"
            ) from error

    def close(self):
        if self.stream.closed:
            return
        try:
            if self.locked:
                self.stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.locked = False
            self.stream.close()


def open_run(config, store):
    previous = store.all("run")
    task_hash = hashlib.sha256(config.task_path.read_bytes()).hexdigest()
    if previous:
        if not config.resume:
            raise ValueError(
                "Work directory already contains a run; use --resume or a fresh directory"
            )
        meta = store.get("run", "current")
        if meta.get("format") != 1:
            raise ValueError("Unsupported saved run format")
        if meta["task_path"] != str(config.task_path) or meta["task_sha256"] != task_hash:
            raise ValueError("Resume requires the same original task and task path")
        if meta["model"] != config.model:
            raise ValueError("Resume model differs from the original provided model")
        elapsed = max(
            meta["elapsed_seconds"],
            time.time() - meta["started_wall"],
            meta["elapsed_seconds"] + max(0.0, time.time() - meta["checkpoint_wall"]),
        )
        config.seconds = meta["seconds"]
        config.started = time.monotonic() - elapsed
        config.reserve = meta["reserve"]
        config.reasoning_effort = meta.get("reasoning_effort", config.reasoning_effort)
        config.deadline_wall = meta["deadline_wall"]
        config.cpus = min(config.cpus, meta.get("cpus", config.cpus))
        config.ram_mb = min(config.ram_mb, meta.get("ram_mb", config.ram_mb))
        if meta.get("context_window"):
            config.context_window = (
                min(config.context_window, meta["context_window"])
                if config.context_window
                else meta["context_window"]
            )
        if config.generation_reserve is None:
            config.generation_reserve = meta.get("generation_reserve")
        meta["previous_status"] = meta.get("terminal_status") or meta["status"]
        meta["status"] = "recovering"
    else:
        if config.resume:
            raise ValueError("No saved run in this directory; cannot resume")
        if store.all("agent"):
            raise ValueError("Legacy run has no recovery manifest; use a fresh work directory")
        started_wall = time.time() - (time.monotonic() - config.started)
        config.deadline_wall = started_wall + config.seconds
        meta = {
            "format": 1,
            "id": uid("run"),
            "task_path": str(config.task_path),
            "task_sha256": task_hash,
            "model": config.model,
            "seconds": config.seconds,
            "started_wall": started_wall,
            "deadline_wall": config.deadline_wall,
            "status": "created",
        }
    return checkpoint(config, store, meta)


def checkpoint(config, store, meta, status=None):
    meta = dict(meta)
    meta.update(
        elapsed_seconds=max(0.0, config.seconds - config.remaining),
        checkpoint_wall=time.time(),
        reasoning_effort=config.reasoning_effort,
        reserve=config.reserve,
        coordinator_pid=os.getpid(),
        cpus=config.cpus,
        ram_mb=config.ram_mb,
        context_window=config.context_window,
        generation_reserve=config.generation_reserve,
    )
    if status:
        meta["status"] = status
        if status in ("completed", "expired"):
            meta["terminal_status"] = status
    store.put("run", "current", meta)
    return meta
