"""Atomic writes for durable manifests and exported snapshots."""

import hashlib
import json
import os
import tempfile
from pathlib import Path


def atomic_write(target, content):
    target = Path(target)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, target)


def atomic_json(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def freeze_bytes(target, content):
    """Commit an immutable file atomically; accept an identical interrupted retry."""
    target = Path(target)
    if target.exists():
        if target.is_symlink() or target.read_bytes() != content:
            raise ValueError(f"Frozen file changed: {target.name}")
        target.chmod(0o444)
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        target.chmod(0o444)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
