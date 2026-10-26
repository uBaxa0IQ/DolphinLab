"""Reformat an existing run's status without starting or resuming agents."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research_monitor import render_database


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--output", type=Path, default=Path("STATUS.md"))
    args = parser.parse_args()
    render_database(args.database, [args.output, args.database.resolve().parent / "STATUS.md"])
    print("Status rendered from saved data; no agents or API requests started.")
