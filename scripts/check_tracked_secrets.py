"""Check tracked sources for credential patterns without printing their contents."""

import re
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
names = (
    subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
)
pattern = re.compile(rb"sk-[A-Za-z0-9_-]{24,}")
bad = []
for name in filter(None, names):
    path = root / name
    if path.name in (".env", ".env.docker.local", "secret.key") or path.suffix in (
        ".sqlite",
        ".sqlite3",
    ):
        bad.append(name)
        continue
    if not path.is_file():
        continue
    if pattern.search(path.read_bytes()):
        bad.append(name)
if bad:
    print("Potential secret-bearing tracked files: " + ", ".join(bad))
    sys.exit(1)
print("Tracked-file credential scan completed; no matching keys.")
