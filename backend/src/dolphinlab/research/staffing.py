"""Frozen role profiles, assignment revisions and explicit handovers within one Run."""

import hashlib
import json
import time

from dolphinlab.common.serialization import uid
from dolphinlab.domain.staffing import Handover, RoleProfileRevision
from dolphinlab.research.jobs import TERMINAL
from dolphinlab.research.roles import LEGACY_ROLES, ROLES, V2_ROLES, prompt
from dolphinlab.research.tools import tools_for


def profile_digest(profile):
    content = {k: v for k, v in profile.items() if k != "id"}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def freeze_profiles(legacy=False, revision=None):
    revision = revision or (1 if legacy else 4)
    result = []
    for role in LEGACY_ROLES if revision == 1 else V2_ROLES if revision == 2 else ROLES:
        record = {
            "role": role,
            "revision": revision,
            "prompt": prompt(role, revision),
            "tools": tools_for(role, revision),
        }
        record["id"] = "role-" + role + "-" + profile_digest(record)
        result.append(RoleProfileRevision.model_validate(record).model_dump())
    return result


class Staffing:
    def __init__(self, runtime, legacy=False):
        self.rt, self.store = runtime, runtime.store
        saved = self.store.all("role_profile")
        if not saved:
            with self.store.transaction():
                for record in self.rt.config.role_catalog or freeze_profiles(legacy):
                    self.store.put("role_profile", record["id"], record)
            saved = self.store.all("role_profile")
        if self.rt.config.role_catalog and {p["id"] for p in saved} != {
            p["id"] for p in self.rt.config.role_catalog
        }:
            raise ValueError("Runtime profiles differ from the frozen Run catalog")
        self.profiles = {}
        for profile in saved:
            RoleProfileRevision.model_validate(profile)
            expected = "role-" + profile["role"] + "-" + profile_digest(profile)
            if profile["id"] != expected or profile["role"] in self.profiles:
                raise ValueError("Invalid or ambiguous frozen role profile")
            self.profiles[profile["role"]] = profile

    def profile(self, agent):
        profile = self.profiles.get(agent["role"])
        if not profile or agent.get("role_profile_id") != profile["id"]:
            raise ValueError("Agent identity does not match its frozen role profile")
        return profile

    def change(self, team_id, actor, reason, **details):
        record = {
            "id": uid("staffing"),
            "team_id": team_id,
            "actor": actor,
            "reason": reason,
            "created": time.time(),
            **details,
        }
        self.store.put("staffing_change", record["id"], record)
        return record

    def assign(self, agent, text, assigned_by, reason="assignment", *, legacy=False):
        previous = agent.get("assignment_id")
        if previous:
            old = self.store.get("assignment", previous)
            if old["state"] == "active":
                from dolphinlab.research.context import archive_context

                old["archive"] = archive_context(agent, self.store, "assignment superseded")
                old["handover"] = self.interim_handover(
                    agent, "Superseded before an authored final handover"
                )
                old.update(state="superseded", ended=time.time())
                self.store.put("assignment", previous, old)
        ident = uid("assignment")
        revision = agent.get("assignment_revision", 0) + 1
        record = {
            "id": ident,
            "agent_id": agent["id"],
            "team_id": agent["team_id"],
            "role_profile_id": agent["role_profile_id"],
            "session_id": agent["session_id"],
            "revision": revision,
            "question": text,
            "assigned_by": assigned_by,
            "reason": reason,
            "previous_assignment_id": previous,
            "team_revision": agent.get("focus_revision", 1),
            "legacy": legacy,
            "created": time.time(),
        }
        self.store.put("assignment_revision", ident, record)
        self.store.put("assignment", ident, {**record, "state": "active", "handover": None})
        agent.update(assignment_id=ident, assignment_revision=revision, focus=text)
        self.store.put("agent", agent["id"], agent)
        self.change(
            agent["team_id"],
            assigned_by,
            reason,
            action="assign",
            agent_id=agent["id"],
            assignment_id=ident,
        )
        return ident

    def bind(self, agent, legacy=False):
        profile = self.profiles.get(agent["role"])
        if not profile:
            raise ValueError("Role is outside this run's frozen catalog")
        agent["role_profile_id"] = agent.get("role_profile_id", profile["id"])
        self.profile(agent)
        if not agent.get("session_id"):
            self.new_session(agent, legacy=legacy)
        if not agent.get("assignment_id"):
            self.assign(
                agent,
                agent.get("focus", "Recovered assignment"),
                agent.get("supervisor") or "runtime",
                "legacy import" if legacy else "initial assignment",
                legacy=legacy,
            )

    def new_session(self, agent, legacy=False):
        ident = uid("session")
        agent["session_id"] = ident
        self.store.put(
            "agent_session",
            ident,
            {
                "id": ident,
                "agent_id": agent["id"],
                "role_profile_id": agent["role_profile_id"],
                "epoch": agent.get("epoch", 0),
                "legacy": legacy,
                "started": time.time(),
            },
        )

    def interrupt(self, agent, reason):
        if agent.get("assignment_id"):
            record = self.store.get("assignment", agent["assignment_id"])
            if record["state"] == "active":
                record.update(
                    state="interrupted",
                    ended=time.time(),
                    interruption_reason=reason,
                    archive=agent.get("last_archive"),
                )
                record["handover"] = self.interim_handover(agent, "Interrupted: " + reason)
                self.store.put("assignment", record["id"], record)

    def interim_handover(self, agent, reason):
        return {
            "summary": reason,
            "results": [],
            "evidence": [
                j["script"]
                for j in self.store.all("job")
                if j["owner"] == agent["id"]
                and j.get("metadata", {}).get("assignment_id") == agent.get("assignment_id")
            ],
            "unsuccessful_approaches": [],
            "open_questions": ["No final authored conclusion; inspect preserved evidence"],
            "next_actions": [],
            "authored": False,
            "working_note": agent.get("note", ""),
        }

    def complete(self, agent, args):
        if any(
            j["owner"] == agent["id"] and j["state"] not in TERMINAL for j in self.store.all("job")
        ):
            raise ValueError("Resolve your active jobs before completing the assignment")
        handover = Handover.model_validate(args["handover"]).model_dump(mode="json")
        record = self.store.get("assignment", agent["assignment_id"])
        if record["state"] == "completed":
            if record["handover"] != handover:
                raise ValueError("Completed handover is immutable; ask for a new assignment")
            return record
        if record["state"] != "active":
            raise ValueError("Assignment is no longer active")
        record.update(state="completed", handover=handover, ended=time.time())
        self.store.put("assignment", record["id"], record)
        agent["wait_requested"] = True
        self.store.put("agent", agent["id"], agent)
        self.rt.notify(
            agent.get("supervisor") or self.rt.root["id"],
            "Assignment handover (reported, not verified): "
            + json.dumps(record, ensure_ascii=False),
            agent["id"],
        )
        return record

    def reviewer(self, agent, args):
        candidate = self.store.get("candidate", args["candidate_id"])
        team = self.store.get("team", candidate["team_id"])
        if agent["role"] != "tech_lead" and team["lead"] != agent["id"]:
            raise ValueError("Only the responsible lead assigns this reviewer")
        reviewer = self.rt.agents.get(args["reviewer_id"])
        source_job = self.store.get("job", candidate["job_id"])
        if not reviewer or reviewer["role"] != "evaluator" or reviewer["state"] == "closed":
            raise ValueError("Choose an active evaluator")
        if reviewer["id"] in (candidate["author"], source_job["owner"]):
            raise ValueError("Reviewer must be independent of author and source job")
        if candidate.get("review_state") not in ("pending", "blocked", "rejected"):
            raise ValueError("A completed approved review cannot be reassigned silently")
        candidate["reviewer_id"] = reviewer["id"]
        if candidate.get("reviewer"):
            candidate.setdefault("review_history", []).append(
                {
                    k: candidate.get(k)
                    for k in (
                        "reviewer",
                        "review_state",
                        "verification_job",
                        "conclusion",
                        "review_findings",
                    )
                }
            )
        candidate["review_state"] = "pending"
        self.store.put("candidate", candidate["id"], candidate)
        self.change(
            team["id"],
            agent["id"],
            args["reason"],
            action="reviewer",
            reviewer_id=reviewer["id"],
            candidate_id=candidate["id"],
        )
        self.rt.notify(
            reviewer["id"], "Independent review assigned: " + json.dumps(candidate), agent["id"]
        )
        return candidate

    def reviewer_load(self):
        candidates = self.store.all("candidate")
        return [
            {
                "id": a["id"],
                "team_id": a["team_id"],
                "pending": sum(
                    c.get("reviewer_id") == a["id"] and c.get("review_state") == "pending"
                    for c in candidates
                ),
            }
            for a in self.rt.agents.values()
            if a["role"] == "evaluator" and a["state"] != "closed"
        ]
