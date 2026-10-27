from __future__ import annotations

import shutil
import time

from dolphinlab.common.files import digest
from dolphinlab.common.serialization import dumps, uid


class MemoryManager:
    """Manage shared findings, handovers, journals and durable notes."""

    def __init__(self, runtime):
        self.runtime = runtime

    def manage_memory(self, agent, args):
        action, scope = args["action"], args.get("scope", "personal")
        if action == "compact":
            agent["compact_requested"] = True
            return {"scheduled": "after complete tool results are recorded"}
        if action == "recall":
            topic = args["topic"]
            offset, limit = max(0, int(args.get("offset", 0))), max(1, int(args.get("limit", 20)))
            if topic == "messages":
                members = (
                    self.runtime.store.get("team", agent["team_id"])["members"]
                    if agent["team_id"]
                    else list(self.runtime.agents)
                )
                records = self.runtime.store.recent_mail(members, offset + limit)
                return records[
                    max(0, len(records) - offset - limit) : len(records) - offset or None
                ]
            if topic == "archives":
                records = self.runtime.store.archives()
                if agent["role"] != "tech_lead":
                    records = [
                        r
                        for r in records
                        if self.runtime.agents.get(r["agent"], {}).get("team_id")
                        == agent["team_id"]
                    ]
            elif args.get("record_id"):
                return self.runtime.store.get(topic, args["record_id"])
            else:
                records = self.runtime.store.all(topic)
            return {
                "total": len(records),
                "offset": offset,
                "records": records[offset : offset + limit],
            }
        if action == "publish":
            if not agent["team_id"]:
                raise ValueError(
                    "Publish team findings from a research team; use the global note for overall strategy"
                )
            team = self.runtime.store.get("team", agent["team_id"])
            ident = uid("finding")
            record = {
                "id": ident,
                "team_id": team["id"],
                "team_revision": team["revision"],
                "author": agent["id"],
                "title": args.get("title", "Research finding"),
                "text": args["text"],
                "references": args.get("references", []),
                "created": time.time(),
            }
            record["claim_status"] = args.get("claim_status", "reported")
            snapshots = []
            for reference in record["references"]:
                try:
                    source = self.runtime.readable(reference)
                except (ValueError, OSError):
                    continue
                if source.is_file() and source.is_relative_to(
                    self.runtime.config.work_dir / "teams"
                ):
                    target = (
                        self.runtime.config.work_dir
                        / "shared"
                        / ident
                        / f"{len(snapshots)}-{source.name}"
                    )
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    snapshots.append(
                        {"original": str(source), "path": str(target), "sha256": digest(target)}
                    )
            record["reference_snapshots"] = snapshots
            self.runtime.store.put("finding", ident, record)
            if args.get("share"):
                self.runtime.notify(
                    self.runtime.root["id"],
                    "Potentially reusable finding; consider sharing with another direction: "
                    + dumps(record),
                    agent["id"],
                )
            return record
        if action == "assess":
            record = self.runtime.store.get("finding", args["finding_id"])
            if agent["role"] != "evaluator" or record["team_id"] != agent["team_id"]:
                raise ValueError("The originating team's evaluator qualifies its findings")
            record.setdefault("assessments", []).append(
                {
                    "author": agent["id"],
                    "status": args["status"],
                    "text": args["text"],
                    "references": args["references"],
                    "created": time.time(),
                }
            )
            record["claim_status"] = args["status"]
            self.runtime.store.put("finding", record["id"], record)
            team = self.runtime.store.get("team", record["team_id"])
            for lead in (team["lead"], self.runtime.root["id"]):
                self.runtime.notify(
                    lead, "Finding assessment changed: " + dumps(record), agent["id"]
                )
            return record
        if action == "share":
            if agent["role"] != "tech_lead":
                raise ValueError(
                    "Flag a reusable finding with publish(share=true); tech lead coordinates cross-team reuse"
                )
            finding = self.runtime.store.get("finding", args["finding_id"])
            target = self.runtime.store.get("team", args["team_id"])
            if target["state"] != "active":
                raise ValueError("Share with an active research team")
            ids = target.setdefault("shared_findings", [])
            if finding["id"] not in ids:
                ids.append(finding["id"])
            self.runtime.store.put("team", target["id"], target)
            self.runtime.notify(
                target["lead"],
                "Tech lead shared original evidence from another direction. "
                + dumps(
                    {
                        "finding": finding,
                        "guidance": args.get(
                            "guidance", "Assess applicability within your charter"
                        ),
                    }
                ),
                agent["id"],
            )
            self.runtime.store.event(
                "finding_shared", {"finding_id": finding["id"], "to_team": target["id"]}
            )
            return {"finding_id": finding["id"], "to_team": target["id"]}
        if scope == "team":
            if not agent["team_id"]:
                raise ValueError("No team")
            team = self.runtime.store.get("team", agent["team_id"])
            if action == "write":
                if agent["id"] != team["lead"] and agent["role"] != "tech_lead":
                    raise ValueError(
                        "Publish an additive finding; only the lead edits the team synthesis"
                    )
                team["note"] = args["text"]
                self.runtime.store.put("team", team["id"], team)
            elif action != "read":
                raise ValueError("Unknown memory action")
            return self.runtime.team_board(team["id"], full=True)
        owner = self.runtime.root if scope == "global" else agent
        if action == "write":
            if scope == "global" and agent["role"] != "tech_lead":
                raise ValueError("Send global findings to tech lead")
            owner["note"] = args["text"]
            self.runtime.store.put("agent", owner["id"], owner)
        elif action != "read":
            raise ValueError("Unknown memory action")
        return {"note": owner["note"]}

    def journal_message(self, agent, text, kind, references, message_id=None, next_action=None):
        if not agent["team_id"] or kind == "assignment":
            return None
        ident = uid("journal")
        record = {
            "id": ident,
            "team_id": agent["team_id"],
            "author": agent["id"],
            "kind": kind,
            "claim_status": "reported",
            "text": text,
            "references": references,
            "message_id": message_id,
            "next_action": next_action,
            "created": time.time(),
        }
        self.runtime.store.put("team_journal", ident, record)
        return record
