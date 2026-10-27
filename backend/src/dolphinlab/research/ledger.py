"""Agent-authored evaluation agreements and evidence-based research reports."""

from __future__ import annotations

import time
from collections import Counter
from pathlib import Path

from dolphinlab.common.serialization import uid


class ResearchLedger:
    def __init__(self, runtime):
        self.runtime, self.store = runtime, runtime.store

    def active(self):
        pointers = self.store.all("evaluation_active")
        return self.store.get("protocol", pointers[0]["id"]) if pointers else None

    def signals(self, team_id=None):
        jobs = [
            j
            for j in self.store.all("job")
            if team_id is None or self.runtime.agents[j["owner"]]["team_id"] == team_id
        ]
        reports = [
            r
            for r in self.store.all("research_report")
            if team_id is None or r["team_id"] == team_id
        ]
        signatures = Counter(
            j.get("source_fingerprint") for j in jobs if j.get("source_fingerprint")
        )
        return {
            "job_count": len(jobs),
            "research_report_count": len(reports),
            "seconds_since_report": round(time.time() - reports[-1]["created"], 1)
            if reports
            else None,
            "recent_reports": [
                {k: r.get(k) for k in ("id", "question", "learned", "next_action", "assessment")}
                for r in reports[-4:]
            ],
            "possibly_repeated_source": [
                {"fingerprint": key, "count": count}
                for key, count in signatures.items()
                if count > 1
            ],
            "interpretation": "Signals are questions for the lead, not proof of stagnation. "
            "Replication, failed hypotheses and cheaper execution can be useful progress.",
        }

    def handle(self, agent, args):
        action = args["action"]
        if action == "status":
            return {"active_protocol": self.active(), "progress": self.signals(agent["team_id"])}
        if action == "inspect":
            topic = args.get("topic", "protocol")
            if topic not in ("protocol", "research_report"):
                raise ValueError("Inspect a protocol or research_report")
            return self.store.get(topic, args["record_id"])
        if action == "protocol":
            if agent["role"] != "evaluator":
                raise ValueError("The team's evaluator publishes the checked evaluation agreement")
            job = self.store.get("job", args["job_id"])
            if job["owner"] != agent["id"] or job["state"] != "succeeded":
                raise ValueError(
                    "Cite your successful job that inspected/exercised the proposed assessment code"
                )
            text = args["text"].strip()
            if not text:
                raise ValueError(
                    "Describe metric, direction, split/object alignment and calculation conventions"
                )
            ident = uid("protocol")
            folder = self.runtime.config.work_dir / "protocols" / ident
            folder.mkdir(parents=True)
            import shutil

            from dolphinlab.common.files import digest

            evidence = []
            references = list(dict.fromkeys([job["script"], *args.get("references", [])]))
            for index, value in enumerate(references):
                source = self.runtime.readable(value)
                if not source.is_file():
                    raise ValueError("Protocol evidence must reference existing files")
                target = folder / f"{index}-{source.name}"
                shutil.copyfile(source, target)
                evidence.append(
                    {"original": str(source), "frozen": str(target), "sha256": digest(target)}
                )
            record = {
                "id": ident,
                "team_id": agent["team_id"],
                "author": agent["id"],
                "description": text,
                "verification_job": job["id"],
                "evidence": evidence,
                "created": time.time(),
                "status": "proposed",
            }
            self.store.put("protocol", ident, record)
            self.runtime.notify(
                self.runtime.root["id"],
                "Evaluator proposed a checked evaluation agreement: "
                + ident
                + ". Inspect and adopt if appropriate.",
                agent["id"],
            )
            return record
        if action == "adopt":
            if agent["role"] != "tech_lead":
                raise ValueError("Tech lead adopts the shared evaluation agreement")
            record = self.store.get("protocol", args["record_id"])
            from dolphinlab.common.files import digest

            if any(
                not Path(e["frozen"]).is_file() or digest(e["frozen"]) != e["sha256"]
                for e in record["evidence"]
            ):
                raise ValueError(
                    "Evaluation evidence changed; publish a new checked protocol version"
                )
            previous = self.active()
            self.store.put("evaluation_active", "current", {"id": record["id"]})
            record["status"] = "adopted"
            self.store.put("protocol", record["id"], record)
            self.store.event(
                "protocol_adopted",
                {"id": record["id"], "previous": previous["id"] if previous else None},
            )
            for team in self.store.all("team"):
                if team["state"] in ("active", "draining"):
                    self.runtime.notify(
                        team["lead"],
                        "Shared evaluation agreement changed to "
                        + record["id"]
                        + ". Read its code/split artifacts and coordinate comparable experiments. Old scores retain their old protocol; "
                        "recheck strong finalists as needed. Existing useful output remains preserved.",
                        agent["id"],
                    )
            return record
        if action == "report":
            team_id = agent["team_id"]
            if not team_id:
                raise ValueError("Publish a research report from a team")
            references = args.get("references", [])
            if not references:
                raise ValueError(
                    "Attach evidence paths or job IDs; a report is a conclusion, not only a status update"
                )
            team = self.store.get("team", team_id)
            ident = uid("research")
            record = {
                "id": ident,
                "team_id": team_id,
                "team_revision": team["revision"],
                "author": agent["id"],
                "question": args.get("question", agent.get("focus", team["goal"])),
                "learned": args["text"],
                "next_action": args["next_action"],
                "references": references,
                "created": time.time(),
            }
            self.store.put("research_report", ident, record)
            self.runtime.notify(team["lead"], "Research conclusion recorded: " + ident, agent["id"])
            return record
        if action == "assess":
            report = self.store.get("research_report", args["record_id"])
            if agent["role"] != "evaluator" or agent["team_id"] != report["team_id"]:
                raise ValueError(
                    "The team's evaluator can assess whether its research conclusion follows from evidence"
                )
            report["assessment"] = {
                "author": agent["id"],
                "text": args["text"],
                "created": time.time(),
            }
            self.store.put("research_report", report["id"], report)
            return report
        if action == "compare":
            candidates = [self.store.get("candidate", ident) for ident in args["candidate_ids"]]
            if len(candidates) < 2:
                raise ValueError("Compare at least two candidates")
            groups = {}
            for candidate in candidates:
                key = candidate.get("review_protocol_id") or "not established"
                groups.setdefault(key, []).append(candidate["id"])
            comparable = (
                len(groups) == 1
                and "not established" not in groups
                and all(c.get("approved") and c.get("comparable") for c in candidates)
            )
            active = self.active()
            return {
                "same_verified_protocol": comparable,
                "groups": groups,
                "active_protocol": active["id"] if active else None,
                "matches_active_protocol": bool(
                    comparable and active and next(iter(groups)) == active["id"]
                ),
                "action": "Compare evidence/metrics"
                if comparable
                else "Have evaluators recheck strong candidates on one agreed protocol; do not rank incompatible scores",
            }
        raise ValueError("Unknown research operation")
