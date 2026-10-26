"""Platform entry point: python solution.py /path/to/task.md."""
from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

from research_config import Config
from research_llm import ModelClient
from research_runtime import Runtime
from research_store import uid


def main():
    started = time.monotonic()
    # Local .env is a development convenience; provided platform variables always take precedence.
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", nargs="?", type=Path, default=Path("task.md"))
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--seconds", type=float, default=10800)
    parser.add_argument("--reserve", type=float, default=0, help="Optional initial global reserve; managers otherwise choose their own plan")
    parser.add_argument("--cpus", type=int, default=8)
    parser.add_argument("--ram-mb", type=int, default=28000)
    parser.add_argument("--llm-concurrency", type=int, default=6)
    parser.add_argument("--max-agents", type=int, default=25)
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high"], default="high")
    parser.add_argument("--context-window", type=int, help="Confirmed provider context capacity in tokens, not a guessed model limit")
    parser.add_argument("--generation-reserve", type=int, help="Input budgeting headroom for reasoning plus visible output; not max_tokens")
    parser.add_argument("--context-history-bytes", type=int, default=128000,
                        help="Working-history size for optional summaries, not an input token limit")
    parser.add_argument("--context-compact-turns", type=int, default=16)
    parser.add_argument("--request-timeout", type=float, default=300)
    parser.add_argument("--resume", action="store_true", help="Resume the existing --work-dir without resetting the original task deadline")
    args = parser.parse_args()
    if args.resume and args.work_dir is None:
        parser.error("--resume requires an existing --work-dir")
    work = args.work_dir or args.task.resolve().parent / ".research" / uid("run")
    context_options = {key: value for key, value in {
        "context_window": args.context_window, "generation_reserve": args.generation_reserve}.items() if value is not None}
    config = Config(args.task, work, seconds=args.seconds, reserve=args.reserve, cpus=args.cpus,
                    ram_mb=args.ram_mb, llm_concurrency=args.llm_concurrency, max_agents=args.max_agents,
                    reasoning_effort=args.reasoning_effort, request_timeout=args.request_timeout,
                    context_history_bytes=args.context_history_bytes, context_compact_turns=args.context_compact_turns,
                    started=started, resume=args.resume, **context_options)
    client = ModelClient(config)
    try:
        runtime = Runtime(config, client)
    except Exception:
        asyncio.run(client.close())
        raise
    print(f"Research started; state: {work / 'state.sqlite3'}", flush=True)
    try:
        result = asyncio.run(runtime.run())
        print("Published " + result["path"] if result else "No evaluator-approved output was produced", flush=True)
        return 0 if result else 1
    finally:
        runtime.store.close()


if __name__ == "__main__":
    raise SystemExit(main())
