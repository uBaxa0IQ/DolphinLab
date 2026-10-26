"""Durable Python job supervisor: logs/results survive coordinator failure."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil


def atomic_json(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def kill_tree(pid):
    try:
        root = psutil.Process(pid)
        children = root.children(recursive=True)
        for proc in reversed(children):
            try:
                proc.kill()
            except psutil.NoSuchProcess:
                pass
        root.kill()
    except psutil.NoSuchProcess:
        pass


def drain(stream, path, limit):
    written = 0
    with Path(path).open("wb") as out:
        while chunk := stream.read1(8192):
            left = limit - written
            if left > 0:
                out.write(chunk[:left])
                out.flush()
                written += min(left, len(chunk))


def main(manifest):
    spec = json.loads(Path(manifest).read_text(encoding="utf-8"))
    folder = Path(spec["folder"])
    identity = {"job_id": spec["id"], "token": spec["token"], "pid": os.getpid(),
                "created": psutil.Process().create_time(), "started": time.time()}
    atomic_json(folder / "process.json", identity)
    result = {"job_id": spec["id"], "token": spec["token"], "state": "failed", "started": identity["started"]}
    process, readers = None, []
    cancelled = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: cancelled.set())
    try:
        if (folder / "cancel.request").exists() or time.time() >= spec["deadline_wall"]:
            result["state"] = "cancelled"
            return
        process = subprocess.Popen([sys.executable, "-u", spec["script"], *spec["args"]],
                                   cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   **({"start_new_session": True} if os.name != "nt" else {}))
        identity["script_pid"] = process.pid
        try:
            identity["script_created"] = psutil.Process(process.pid).create_time()
        except psutil.NoSuchProcess:
            pass
        atomic_json(folder / "process.json", identity)
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            reader = threading.Thread(target=drain, args=(stream, folder / (name + ".log"), spec["log_bytes"]), daemon=True)
            reader.start()
            readers.append(reader)
        started = time.monotonic()
        while process.poll() is None:
            if cancelled.is_set() or (folder / "cancel.request").exists():
                result["state"] = "cancelled"
                break
            if time.time() >= spec["deadline_wall"] or time.monotonic() - started >= spec["timeout"]:
                result["state"] = "timed_out"
                break
            try:
                root = psutil.Process(os.getpid())
                rss = sum(p.memory_info().rss for p in [root] + root.children(recursive=True)) / 1024**2
                result["peak_ram_mb"] = max(result.get("peak_ram_mb", 0), round(rss, 1))
                if rss > spec["ram_mb"]:
                    result["state"] = "memory_exceeded"
                    break
            except psutil.NoSuchProcess:
                pass
            time.sleep(.2)
        if process.poll() is None:
            kill_tree(process.pid)
        process.wait(timeout=5)
        result["exit_code"] = process.returncode
        if result["state"] == "failed" and process.returncode == 0:
            result["state"] = "succeeded"
    except BaseException as error:
        result["error"] = f"{type(error).__name__}: {error}"
        if process and process.poll() is None:
            kill_tree(process.pid)
    finally:
        if process and os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for reader in readers:
            reader.join(timeout=2)
        result["finished"] = time.time()
        atomic_json(folder / "result.json", result)


if __name__ == "__main__":
    main(sys.argv[1])
