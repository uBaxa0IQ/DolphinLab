"""Read a running experiment's factual state without interrupting any agent."""
import argparse
import json
import sqlite3
from pathlib import Path


def status(path, history=False):
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    def rows(kind):
        return [json.loads(r[0]) for r in db.execute("SELECT data FROM objects WHERE kind=?", (kind,))]
    usage = [json.loads(r[0]).get("usage", {}) for r in db.execute("SELECT data FROM events WHERE kind='model_usage'")]
    result = {"run": rows("run"), "evaluation_agreements": rows("protocol"),
              "active_agreement": rows("evaluation_active"), "research_reports": rows("research_report"),
              "teams": [{k: t.get(k) for k in ("id", "direction", "revision", "state")} for t in rows("team")],
              "agents": [{k: a.get(k) for k in ("id", "role", "state", "failures")} for a in rows("agent")],
              "jobs": [{k: j.get(k) for k in ("id", "owner", "state", "folder", "exit_code", "error")} for j in rows("job")],
              "candidates": rows("candidate"), "published": rows("publication"),
              "model_requests": len(usage), "tokens": sum(u.get("total_tokens", 0) or 0 for u in usage),
              "reported_cost": sum(float(u.get("cost", 0) or 0) for u in usage),
              "recent_events": [(kind, json.loads(data)) for kind, data in db.execute(
                  "SELECT kind,data FROM events ORDER BY seq DESC LIMIT 6")]}
    if history:
        result["recent_agent_messages"] = [{"agent": a["id"], "history": a.get("history", [])[-4:]}
                                           for a in rows("agent")]
    db.close()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("database")
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()
    print(json.dumps(status(args.database, args.history), ensure_ascii=False, indent=2))
