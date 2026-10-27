from __future__ import annotations

import asyncio
import time

from sonder.common.serialization import dumps, uid
from sonder.research.context import (
    archive_context,
)
from sonder.research.jobs import TERMINAL
from sonder.research.roles import SPECIALISTS


class TeamManager:
    """Own team membership, charters, draining and retirement."""

    def __init__(self, runtime):
        self.runtime = runtime

    async def retire_team(self, team, archived=False, reason="completed"):
        team["state"] = "archived" if archived else "closed"
        team["closing_reason"] = reason
        self.runtime.store.put("team", team["id"], team)
        cancelled = []
        for member in team["members"]:
            self.runtime.agents[member]["state"] = "closed"
            self.runtime.store.put("agent", member, self.runtime.agents[member])
            task = self.runtime.turns.get(member)
            if task and not task.done():
                task.cancel()
                cancelled.append(task)
        await asyncio.gather(*cancelled, return_exceptions=True)
        own_jobs = [
            j
            for j in self.runtime.store.all("job")
            if j["owner"] in team["members"] and j["state"] not in TERMINAL
        ]
        for job in own_jobs:
            self.runtime.jobs.cancel(job["id"])
        await asyncio.gather(
            *(self.runtime.jobs.tasks[j["id"]] for j in own_jobs), return_exceptions=True
        )
        archives = {}
        for member in team["members"]:
            a = self.runtime.agents[member]
            self.runtime.complete_interrupted_calls(a)
            archives[member] = archive_context(a, self.runtime.store, "team " + team["state"])
            a["last_archive"] = archives[member]
            a["history"], a["summary"] = [], ""
            self.runtime.store.put("agent", member, a)
        team["handover"] = {
            "member_archives": archives,
            "finding_ids": [
                f["id"] for f in self.runtime.store.all("finding") if f["team_id"] == team["id"]
            ],
            "candidate_ids": [
                c["id"] for c in self.runtime.store.all("candidate") if c["team_id"] == team["id"]
            ],
        }
        self.runtime.store.put("team", team["id"], team)
        self.runtime.store.event(
            "team_retired", {"team_id": team["id"], "state": team["state"], "reason": reason}
        )
        self.runtime.notify(
            self.runtime.root["id"],
            f"Team {team['id']} is {team['state']}. Handover and evidence remain available through team inspect.",
        )
        return team

    async def advance_teams(self):
        for team in self.runtime.store.all("team"):
            if team["state"] != "draining":
                continue
            active_jobs = any(
                j["owner"] in team["members"] and j["state"] not in TERMINAL
                for j in self.runtime.store.all("job")
            )
            active_turns = any(
                m in self.runtime.turns and not self.runtime.turns[m].done()
                for m in team["members"]
            )
            pending = any(
                self.runtime.store.pending(m)
                for m in team["members"]
                if self.runtime.agents[m]["state"] != "closed"
            )
            ready = any(self.runtime.agents[m]["state"] == "ready" for m in team["members"])
            expired = time.monotonic() >= team["drain_until"]
            if expired or not (active_jobs or active_turns or pending or ready):
                await self.runtime.retire_team(
                    team,
                    team.get("archive_after_close", False),
                    team.get("closing_reason", "completed"),
                )

    async def manage_team(self, agent, args):
        action = args["action"]
        if action == "create":
            if agent["role"] != "tech_lead":
                raise ValueError("Only tech lead creates teams")
            if (
                sum(a["state"] != "closed" for a in self.runtime.agents.values()) + 4
                > self.runtime.config.max_agents
            ):
                raise ValueError("Need room for lead, ML engineer, data engineer and evaluator")
            ident = uid("team")
            team = {
                "id": ident,
                "direction": args["direction"],
                "goal": args["goal"],
                "revision": 1,
                "state": "active",
                "note": "",
                "members": [],
                "scope": args.get("scope", args["direction"]),
                "out_of_scope": args.get("out_of_scope", ""),
                "deliverables": args.get("deliverables", []),
                "shared_findings": [],
                "allocation_deadline_wall": time.time()
                + min(
                    self.runtime.config.remaining,
                    max(0.0, float(args.get("budget_seconds", self.runtime.config.remaining))),
                ),
                "time_plan": {},
                "time_alerts": {},
                "resources": args.get("resources", {}),
            }
            first_delivery = not self.runtime.published and not any(
                t.get("purpose") == "delivery" and t["state"] in ("active", "draining")
                for t in self.runtime.store.all("team")
            )
            team["purpose"] = args.get("purpose", "delivery" if first_delivery else "research")
            milestone = (
                "CURRENT FIRST MILESTONE: deliver one modest genuine fit/assessment/inference artifact before any sweep or extensive CV. "
                "One configuration and the simplest honest task-compatible assessment, then register and independently review. "
                "Research the broader charter after that checkpoint. "
                if team["purpose"] == "delivery"
                else "Investigate this independent research question using shared data/evaluation evidence; avoid duplicating the delivery team's pipeline. "
            )
            leader = self.runtime.new_agent(
                "team_lead", ident, self.runtime.root["id"], milestone + args["goal"]
            )
            evaluator = self.runtime.new_agent(
                "evaluator",
                ident,
                leader["id"],
                "You are this team's evaluator. Familiarize yourself with the task, "
                "coordinate evaluation with the lead, then wait for concrete candidates.",
            )
            ml = self.runtime.new_agent(
                "ml_engineer",
                ident,
                leader["id"],
                milestone
                + "You own implementation and candidate registration within this charter: "
                + args["goal"]
                + ". Coordinate with the lead, data engineer and evaluator; do not wait for exhaustive profiling before a safe baseline.",
            )
            data = self.runtime.new_agent(
                "data_engineer",
                ident,
                leader["id"],
                "Investigate schema, fit/transform boundaries and useful data opportunities for: "
                + args["goal"]
                + ". Supply evidence and usable code to ML engineer; support the first delivery in parallel.",
            )
            team.update(
                lead=leader["id"],
                evaluator=evaluator["id"],
                ml_engineer=ml["id"],
                data_engineer=data["id"],
                members=[leader["id"], ml["id"], data["id"], evaluator["id"]],
            )
            self.runtime.store.put("team", ident, team)
            self.runtime.store.event("team_created", team)
            return team
        team = self.runtime.store.get("team", args["team_id"])
        if agent["role"] != "tech_lead" and team["lead"] != agent["id"]:
            raise ValueError("Manage your own team")
        if action == "inspect":
            return self.runtime.team_board(team["id"], full=True)
        if action in ("close", "delete"):
            if agent["role"] != "tech_lead":
                raise ValueError("Tech lead closes or deletes teams")
            if team["state"] in ("closed", "archived"):
                if action == "delete":
                    team["state"] = "archived"
                    self.runtime.store.put("team", team["id"], team)
                return team
            reason = args.get("reason", "direction complete or no longer prioritized")
            if args.get("mode", "graceful") == "immediate":
                return await self.runtime.retire_team(team, action == "delete", reason)
            team.update(
                state="draining",
                closing_reason=reason,
                archive_after_close=action == "delete",
                drain_until=time.monotonic()
                + min(
                    self.runtime.config.remaining,
                    max(0.0, float(args.get("grace_seconds", self.runtime.config.remaining))),
                ),
            )
            team["drain_deadline_wall"] = time.time() + max(
                0.0, team["drain_until"] - time.monotonic()
            )
            self.runtime.store.put("team", team["id"], team)
            for member in team["members"]:
                self.runtime.notify(
                    member,
                    "Team is winding down. Start no new research. Preserve useful findings, "
                    "finish or cancel current jobs, register/check ready candidates, send a concise "
                    "handover to the lead, then wait. Reason: " + reason,
                    agent["id"],
                )
            return team
        if action == "restart_agent":
            target = self.runtime.agents[args["agent_id"]]
            if target["team_id"] != team["id"] or target["id"] == agent["id"]:
                raise ValueError("Restart a subordinate in this team, not the caller")
            if target["state"] == "closed" or team["state"] not in ("active", "draining"):
                raise ValueError(
                    "Closed teams are archived; create a fresh team and reference its findings"
                )
            task = self.runtime.turns.get(target["id"])
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            self.runtime.complete_interrupted_calls(target)
            archive = archive_context(target, self.runtime.store, "fresh agent episode")
            self.runtime.store.retire_mail(target["id"])
            target.update(
                history=[],
                summary="",
                epoch=target.get("epoch", 0) + 1,
                last_archive=archive,
                failures=0,
                retry_at=0.0,
                wake_at=0.0,
                state="ready",
                focus=args.get("assignment", target.get("focus", team["goal"])),
                focus_revision=team["revision"],
            )
            if not args.get("keep_note", True):
                target["note"] = ""
            self.runtime.store.put("agent", target["id"], target)
            self.runtime.notify(
                target["id"],
                "Fresh context episode. Original history: " + archive + ". "
                "Your notes, team board and existing jobs remain. Reconcile their state before duplicating work. "
                "Assignment: " + target["focus"],
                agent["id"],
            )
            return {"agent_id": target["id"], "epoch": target["epoch"], "archive": archive}
        if team["state"] != "active":
            raise ValueError("Team is not active")
        if action == "staff":
            role = args["role"]
            if role not in SPECIALISTS:
                raise ValueError("Role not in specialist catalog")
            if role == "evaluator":
                return {"agent_id": team["evaluator"], "existing": True}
            child = self.runtime.new_agent(role, team["id"], team["lead"], args["assignment"])
            child["focus_revision"] = team["revision"]
            self.runtime.store.put("agent", child["id"], child)
            team["members"].append(child["id"])
            self.runtime.store.put("team", team["id"], team)
            return {"agent_id": child["id"], "workspace": child["workspace"]}
        if agent["role"] != "tech_lead":
            raise ValueError("Only tech lead reassigns or closes teams")
        if action == "reassign":
            team.update(
                direction=args["direction"], goal=args["goal"], revision=team["revision"] + 1
            )
            for key in ("resources", "scope", "out_of_scope", "deliverables", "purpose"):
                if key in args:
                    team[key] = args[key]
            if "scope" not in args:
                team["scope"] = args["direction"]
            self.runtime.store.put("team", team["id"], team)
            leader = self.runtime.agents[team["lead"]]
            leader.update(focus=team["goal"], focus_revision=team["revision"])
            self.runtime.store.put("agent", leader["id"], leader)
            for member in team["members"]:
                self.runtime.notify(
                    member,
                    "Team charter revised; prior assignments may be superseded. "
                    "Coordinate current experiments with the team lead. "
                    + dumps(
                        {
                            k: team[k]
                            for k in (
                                "id",
                                "revision",
                                "goal",
                                "scope",
                                "out_of_scope",
                                "deliverables",
                            )
                        }
                    ),
                    agent["id"],
                )
            return team
        raise ValueError("Unknown team action")
