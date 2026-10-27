from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from sonder.common.files import digest
from sonder.common.serialization import dumps, uid
from sonder.research.jobs import TERMINAL


class CandidateRegistry:
    """Register candidates, record independent review, and publish approved artifacts."""

    def __init__(self, runtime):
        self.runtime = runtime

    def manage_candidate(self, agent, args):
        action = args["action"]
        if action == "list":
            return self.runtime.store.all("candidate")
        if action == "inspect":
            return self.runtime.store.get("candidate", args["candidate_id"])
        if action == "register":
            if agent["role"] != "ml_engineer":
                raise ValueError(
                    "ML engineer owns candidate delivery; managers delegate and evaluator independently reviews"
                )
            job = self.runtime.store.get("job", args["job_id"])
            team_id = agent["team_id"]
            if not team_id:
                raise ValueError("Select an existing team_id for independent evaluator routing")
            team = self.runtime.store.get("team", team_id)
            if team["state"] not in ("active", "draining"):
                raise ValueError("Route new candidates to an active or draining team's evaluator")
            if self.runtime.agents[job["owner"]]["team_id"] != team_id:
                raise ValueError("Candidate must come from your team's job")
            if job["state"] != "succeeded" and not (
                args.get("allow_partial_source") and job["state"] in TERMINAL
            ):
                raise ValueError(
                    "Source job is not successful. To offer an existing partial artifact for strict independent review, set allow_partial_source=true"
                )
            path = self.runtime.readable(args["submission_path"])
            if not path.is_file() or not path.stat().st_size:
                raise ValueError("Provide a nonempty output file attributable to the cited job")
            ident = uid("candidate")
            frozen = self.runtime.config.work_dir / "candidates" / ident
            frozen.mkdir(parents=True)
            submission = frozen / "submission.csv"
            shutil.copyfile(path, submission)
            candidate = {
                "id": ident,
                "team_id": team_id,
                "author": agent["id"],
                "team_revision": team["revision"],
                "job_assignment": job.get("metadata", {}),
                "job_id": job["id"],
                "submission_path": str(submission),
                "sha256": digest(submission),
                "findings": args.get("findings", {}),
                "references": args.get("references", []),
                "declared_protocol_id": args.get(
                    "protocol_id", job.get("metadata", {}).get("protocol_id")
                ),
                "source_job_state": job["state"],
                "review_state": "pending",
                "approved": False,
                "created": time.time(),
            }
            self.runtime.store.put("candidate", ident, candidate)
            self.runtime.notify(
                team["evaluator"],
                "Candidate ready for your independent verification: " + dumps(candidate),
                agent["id"],
            )
            self.runtime.notify(
                team["lead"], "Candidate registered and handed to evaluator: " + ident, agent["id"]
            )
            return candidate
        candidate = self.runtime.store.get("candidate", args["candidate_id"])
        if action == "flag":
            if agent["role"] != "evaluator" or candidate["team_id"] != agent["team_id"]:
                raise ValueError("The candidate team's evaluator flags correctness blockers")
            candidate.setdefault("blockers", []).append(
                {
                    "reason": args["reason"],
                    "references": args.get("references", []),
                    "created": time.time(),
                }
            )
            candidate.update(
                approved=False,
                review_state="blocked",
                reviewer=agent["id"],
                conclusion=args["reason"],
            )
            self.runtime.store.put("candidate", candidate["id"], candidate)
            self.runtime.withdraw_candidate(candidate)
            team = self.runtime.store.get("team", candidate["team_id"])
            for lead in (team["lead"], self.runtime.root["id"]):
                self.runtime.notify(
                    lead,
                    "BLOCKED candidate "
                    + candidate["id"]
                    + ": "
                    + args["reason"]
                    + ". Evidence: "
                    + dumps(args.get("references", [])),
                    agent["id"],
                )
            self.runtime.journal_message(
                agent, args["reason"], "blocker", args.get("references", [])
            )
            self.runtime.store.event(
                "candidate_blocked", {"candidate_id": candidate["id"], "reason": args["reason"]}
            )
            return candidate
        if action == "review":
            if agent["role"] != "evaluator" or candidate["team_id"] != agent["team_id"]:
                raise ValueError("Only the candidate team's evaluator can record its review")
            verification = self.runtime.store.get("job", args["job_id"])
            if verification["owner"] != agent["id"] or verification["state"] != "succeeded":
                raise ValueError(
                    "Run your own verification Python successfully before recording a review"
                )
            if verification["submitted"] < candidate["created"]:
                raise ValueError(
                    "Verification must run after candidate registration; an earlier profiling job is not its review"
                )
            if not args.get("conclusion", "").strip():
                raise ValueError("Provide your evidence-based conclusion")
            if digest(candidate["submission_path"]) != candidate["sha256"]:
                raise ValueError("Candidate bytes changed since registration")
            candidate.update(
                approved=bool(args["approved"]),
                reviewer=agent["id"],
                review_state="approved" if args["approved"] else "rejected",
                verification_job=verification["id"],
                conclusion=args["conclusion"],
                review_findings=args.get("findings", {}),
            )
            protocol_id = args.get("protocol_id")
            if protocol_id:
                self.runtime.store.get("protocol", protocol_id)
            candidate.update(
                review_protocol_id=protocol_id,
                comparable=bool(protocol_id and args.get("comparable", False)),
            )
            self.runtime.store.put("candidate", candidate["id"], candidate)
            if not candidate["approved"]:
                self.runtime.withdraw_candidate(candidate)
            team = self.runtime.store.get("team", agent["team_id"])
            for recipient in (team["lead"], self.runtime.root["id"]):
                self.runtime.notify(
                    recipient, "Evaluator verdict: " + dumps(candidate), agent["id"]
                )
            # First verified output is a safety checkpoint; tech lead chooses subsequent versions.
            if candidate["approved"] and not self.runtime.published:
                self.runtime.publish(candidate["id"])
            return candidate
        if action == "publish":
            if agent["role"] != "tech_lead":
                raise ValueError("Tech lead chooses the published result")
            return self.runtime.publish(candidate["id"])
        raise ValueError("Unknown candidate action")

    def withdraw_candidate(self, candidate):
        if self.runtime.published and self.runtime.published["candidate_id"] == candidate["id"]:
            target = Path(self.runtime.published["path"])
            if target.is_file() and digest(target) == self.runtime.published["sha256"]:
                target.unlink()  # Exact reviewed bytes remain preserved in the immutable candidate.
            self.runtime.published = None
            self.runtime.store.put("publication", "current", {"revoked_candidate": candidate["id"]})
            alternatives = [c for c in self.runtime.store.all("candidate") if c.get("approved")]
            if alternatives:
                self.runtime.publish(alternatives[-1]["id"])

    def publish(self, ident):
        candidate = self.runtime.store.get("candidate", ident)
        if not candidate.get("approved"):
            raise ValueError("Evaluator approval is required")
        source = Path(candidate["submission_path"])
        if digest(source) != candidate["sha256"]:
            raise ValueError("Candidate changed after review")
        target = self.runtime.config.task_path.parent / "submission.csv"
        temporary = target.with_name(".submission-" + uid("publish") + ".tmp")
        try:
            with source.open("rb") as inp, temporary.open("wb") as out:
                shutil.copyfileobj(inp, out)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        self.runtime.published = {
            "candidate_id": ident,
            "path": str(target),
            "sha256": candidate["sha256"],
        }
        self.runtime.store.put("publication", "current", self.runtime.published)
        self.runtime.store.event("published", self.runtime.published)
        return self.runtime.published
