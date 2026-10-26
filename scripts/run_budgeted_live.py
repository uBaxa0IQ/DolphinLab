"""Local RouterAI observation run. No ML logic and no output-token limit."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
import httpx
from research_config import Config
from research_llm import ModelClient, APIUnavailable, ResponseProblem
from research_runtime import Runtime
from research_monitor import atomic_write


class BudgetedClient(ModelClient):
    def __init__(self, config, endpoint, budget):
        super().__init__(config)
        self.endpoint, self.budget = endpoint, budget
        self.spent = self.reserved = 0.
        self.exhausted = False
        self.admission = asyncio.Semaphore(config.llm_concurrency)
        self.on_budget = None
        prices = endpoint["pricing"]
        # Reserve the provider's full advertised request capacity, without capping generation.
        self.reservation = 1.1 * (endpoint["context_length"] * prices["prompt"] +
                                  endpoint["max_completion_tokens"] * prices["completion"])

    def record(self):
        if self.on_budget:
            self.on_budget({"limit_rub": self.budget, "accounted_rub": round(self.spent, 6),
                            "reserved_rub": round(self.reserved, 6), "currency": "RUB",
                            "provider": self.endpoint["tag"], "exhausted": self.exhausted,
                            "request_reservation_rub": self.reservation})

    async def complete(self, messages, tools=None):
        async with self.admission:
            if self.exhausted or self.spent + self.reserved + self.reservation > self.budget:
                # Let existing reservations settle before deciding there is no room.
                while self.reserved and not self.exhausted:
                    await asyncio.sleep(.1)
                if self.spent + self.reservation > self.budget:
                    self.exhausted = True
                    self.record()
                    raise APIUnavailable("Local RUB budget: insufficient headroom for another full provider response")
            self.reserved += self.reservation
            self.record()
            usage = None
            try:
                message, usage = await super().complete(messages, tools)
                if usage.get("cost") is None and all(usage.get(k) is not None for k in ("prompt_tokens", "completion_tokens")):
                    usage["cost"] = (usage.get("prompt_tokens", 0) * self.endpoint["pricing"]["prompt"] +
                                     usage.get("completion_tokens", 0) * self.endpoint["pricing"]["completion"])
                    usage["cost_source"] = "uncached catalogue estimate"
                elif usage.get("cost") is None:
                    usage["cost"] = self.reservation
                    usage["cost_source"] = "unknown usage: full conservative reservation"
                usage["currency"] = "RUB"
                return message, usage
            except ResponseProblem as error:
                usage = error.usage
                raise
            finally:
                # Unknown interrupted requests consume their complete reservation conservatively.
                cost = usage.get("cost") if usage else None
                self.spent += float(cost) if cost is not None else self.reservation
                self.reserved -= self.reservation
                self.record()


async def execute(args):
    ledger_path = args.ledger.resolve() if args.ledger else None
    ledger = None
    if ledger_path:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        committed = sum(float(r.get("accounted_rub", 0)) + float(r.get("reserved_rub", 0))
                        for r in ledger["runs"].values())
        args.budget = min(args.budget, ledger["limit_rub"] - committed)
        if args.budget <= 0:
            raise ValueError("The cumulative experiment budget has no uncommitted funds")
    config = Config(args.task, args.work_dir, seconds=args.seconds, cpus=8, ram_mb=8192,
                    llm_concurrency=args.llm_concurrency, reasoning_effort=args.reasoning_effort, request_timeout=300)
    if "routerai.ru" not in config.base_url:
        raise ValueError("This local bounded runner is specifically for the configured RouterAI API")
    async with httpx.AsyncClient(timeout=20) as http:
        result = await http.get(config.base_url.rstrip("/") + "/models/" + config.model + "/endpoints")
        result.raise_for_status()
        endpoints = result.json()["data"]["endpoints"]
    endpoint = next(e for e in endpoints if e.get("tag") == "tenstorrent" and e.get("status") == 0)
    if any(endpoint.get("pricing_units", {}).get(k) != "token" for k in ("prompt", "completion")) or endpoint.get("variable_pricings"):
        raise ValueError("Unsupported pricing structure: cannot reserve request cost safely")
    config.context_window = endpoint["context_length"]
    config.request_extra = {"reasoning": {"effort": args.reasoning_effort}, "provider": {"only": [endpoint["tag"]], "allow_fallbacks": False}}
    client = BudgetedClient(config, endpoint, args.budget)
    runtime = Runtime(config, client)
    (config.work_dir / "provider-metadata.json").write_text(json.dumps(endpoint, ensure_ascii=False, indent=2), encoding="utf-8")
    def record_budget(record):
        runtime.store.put("budget", "current", record)
        atomic_write(config.work_dir / "BUDGET.json", json.dumps(record, indent=2))
        if ledger is not None:
            ledger["runs"][str(config.work_dir)] = {**record, "task": str(config.task_path)}
            ledger["committed_rub"] = round(sum(float(r.get("accounted_rub", 0)) + float(r.get("reserved_rub", 0))
                                                 for r in ledger["runs"].values()), 6)
            atomic_write(ledger_path, json.dumps(ledger, indent=2))
    client.on_budget = record_budget
    client.record()
    runtime.notify(runtime.root["id"], f"Observation run: {args.seconds / 60:g} minutes, {args.reasoning_effort} reasoning, {args.budget:g} RUB maximum. Use the research opportunity after a safe checkpoint; a single slow job is a reason to adapt the portfolio, not stop the whole run. CPU/RAM are in state. Money admission is managed externally; do not change the model/provider.")
    async def watch_budget():
        warned = False
        while not runtime.done and config.remaining > 0:
            if client.spent >= args.budget * .75 and not warned:
                warned = True
                runtime.notify(runtime.root["id"], "Financial budget is approaching its admission boundary. Prioritize independent review and verified delivery now.")
            if client.exhausted:
                runtime.store.event("financial_stop", {"accounted_rub": client.spent})
                runtime.done = True
                break
            await asyncio.sleep(.5)
    watcher = asyncio.create_task(watch_budget())
    print(f"Live run started: {config.work_dir}; {args.reasoning_effort}; {args.seconds}s; budget {args.budget} RUB; provider {endpoint['tag']}", flush=True)
    try:
        result = await runtime.run()
        print(json.dumps({"published": result, "accounted_rub": client.spent, "unused_seconds": config.remaining}), flush=True)
        return 0 if result else 1
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        client.record()
        runtime.store.close()


if __name__ == "__main__":
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    parser = argparse.ArgumentParser()
    parser.add_argument("task", type=Path)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=1500)
    parser.add_argument("--budget", type=float, default=150)
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high"], default="high")
    parser.add_argument("--ledger", type=Path, help="Cumulative experiment budget JSON; unknown reservations remain committed")
    parser.add_argument("--llm-concurrency", type=int, default=6,
                        help="Concurrent API requests; uses the normal runtime default unless overridden")
    raise SystemExit(asyncio.run(execute(parser.parse_args())))
