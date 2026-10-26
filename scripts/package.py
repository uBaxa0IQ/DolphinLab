"""Build a flat submission archive. Never includes credentials, datasets or test doubles."""
import argparse
import sys
import zipfile
from pathlib import Path


def package(destination):
    root = Path(__file__).resolve().parents[1]
    files = [root / "solution.py", root / "requirements.txt", *sorted(root.glob("research_*.py"))]
    if any(not p.is_file() for p in files):
        raise ValueError("Missing production source")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.name)
    if destination.stat().st_size > 100_000_000:
        destination.unlink()
        raise ValueError("Archive exceeds 100 MB")
    return {"archive": str(destination.resolve()), "bytes": destination.stat().st_size,
            "files": [p.name for p in files]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", nargs="?", default="dist/solution.zip")
    print(package(parser.parse_args().destination))
