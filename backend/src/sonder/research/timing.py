"""Managers choose time plans. Only the global task deadline is non-negotiable."""

from __future__ import annotations

import time


class Timing:
    def __init__(self, runtime):
        self.runtime = runtime
        self.previous = 0.0

    def view(self, team):
        rt = self.runtime
        now = time.time()
        deadline = min(
            rt.config.deadline_wall, team.get("allocation_deadline_wall", rt.config.deadline_wall)
        )
        remaining = max(0.0, min(rt.config.remaining, deadline - now))
        plan = team.get("time_plan", {})
        override = plan.get("phase", "auto")
        reserve = plan.get("finalization_reserve_seconds", 0.0)
        phase = (
            "finalization"
            if rt.config.finalizing
            or override == "finalization"
            or (override == "auto" and reserve > 0 and remaining <= reserve)
            else "research"
        )
        jobs = [
            j
            for j in rt.store.all("job")
            if j["owner"] in team["members"] and j.get("finished") and j.get("started")
        ]
        durations = [j["finished"] - j["started"] for j in jobs]
        return {
            "allocated_remaining_seconds": round(remaining, 1),
            "allocation_deadline_wall": deadline,
            "phase": phase,
            "plan": plan,
            "first_result_due_in_seconds": round(plan["first_result_deadline_wall"] - now, 1)
            if plan.get("first_result_deadline_wall")
            else None,
            "next_review_in_seconds": round(plan["review_wall"] - now, 1)
            if plan.get("review_wall")
            else None,
            "completed_job_count": len(jobs),
            "last_job_seconds": round(durations[-1], 2) if durations else None,
            "max_observed_job_seconds": round(max(durations), 2) if durations else None,
            "interpretation": "Allocation/checkpoints guide the leads. Running work is not automatically killed at a soft boundary; global deadline cannot be extended.",
        }

    def handle(self, agent, args):
        rt = self.runtime
        action = args["action"]
        now = time.time()
        if action == "portfolio":
            if agent["role"] != "tech_lead":
                raise ValueError("Tech lead owns research portfolio decisions")
            record = {
                "strategy": args["strategy"],
                "updated": now,
                "review_wall": now
                + min(rt.config.remaining, max(0.5, float(args["next_review_in_seconds"]))),
            }
            rt.store.put("portfolio", "current", record)
            rt.store.event("portfolio_plan", record)
            return record
        if action == "global":
            if agent["role"] != "tech_lead":
                raise ValueError("Only tech lead plans global time")
            if "reserve_seconds" in args:
                rt.config.reserve = min(
                    rt.config.remaining, max(0.0, float(args["reserve_seconds"]))
                )
            if args.get("finalize_now"):
                rt.config.reserve = rt.config.remaining
            if args.get("cancel_research"):
                for job in rt.store.all("job"):
                    if job["kind"] == "research" and job["state"] in (
                        "queued",
                        "launching",
                        "running",
                    ):
                        rt.jobs.cancel(job["id"])
            if not rt.config.finalizing:
                rt.phase_announced = False
            rt.store.event("run_time_plan", {**rt.config.public(), "reason": args["reason"]})
            return rt.config.public()
        team = rt.store.get("team", args["team_id"])
        if team["state"] != "active":
            raise ValueError("Time plans belong to active teams")
        if action == "allocate":
            if agent["role"] != "tech_lead":
                raise ValueError("Tech lead allocates time between teams")
            seconds = min(rt.config.remaining, max(0.0, float(args["seconds"])))
            team["allocation_deadline_wall"] = now + seconds
            team["time_alerts"] = {}
            rt.store.put("team", team["id"], team)
            rt.notify(
                team["lead"],
                "Time allocation updated. Replan local milestones and experiments. Reason: "
                + args["reason"],
                agent["id"],
            )
        elif action == "plan":
            if agent["id"] != team["lead"]:
                raise ValueError("The team lead owns its local research and delivery plan")
            plan = dict(team.get("time_plan", {}))
            deadline = min(
                rt.config.deadline_wall,
                team.get("allocation_deadline_wall", rt.config.deadline_wall),
            )
            remaining = max(0.0, deadline - now)
            if "finalization_reserve_seconds" in args:
                plan["finalization_reserve_seconds"] = min(
                    remaining, max(0.0, float(args["finalization_reserve_seconds"]))
                )
            if "first_result_in_seconds" in args:
                plan["first_result_deadline_wall"] = min(
                    deadline, now + max(0.0, float(args["first_result_in_seconds"]))
                )
            if "review_in_seconds" in args:
                plan["review_wall"] = min(
                    deadline, now + max(0.5, float(args["review_in_seconds"]))
                )
            if "phase" in args:
                plan["phase"] = args["phase"]
            plan.update(reason=args["reason"], updated=now)
            team.update(time_plan=plan, time_alerts={})
            rt.store.put("team", team["id"], team)
        else:
            raise ValueError("Unknown timing operation")
        result = self.view(team)
        rt.store.event("team_time_plan", {"team_id": team["id"], "actor": agent["id"], **result})
        return result

    def tick(self):
        if time.monotonic() - self.previous < 1:
            return
        self.previous = time.monotonic()
        rt = self.runtime
        now = time.time()
        plans = rt.store.all("portfolio")
        if plans and now >= plans[0]["review_wall"] and not plans[0].get("review_notified"):
            plan = plans[0]
            plan["review_notified"] = now
            rt.store.put("portfolio", "current", plan)
            rt.notify(
                rt.root["id"],
                "Your portfolio review is due. Reassess independent questions, team allocation, feasible alternatives and open review caveats; a checkpoint publication is not a reason to stop research.",
            )
            rt.store.event("portfolio_review_due", {})
        for team in rt.store.all("team"):
            if team["state"] != "active":
                continue
            plan = team.get("time_plan", {})
            alerts = team.setdefault("time_alerts", {})
            view = self.view(team)
            approved = any(
                c["team_id"] == team["id"] and c.get("approved") for c in rt.store.all("candidate")
            )
            events = []
            if plan.get("review_wall", now + 1) <= now and not alerts.get("review"):
                events.append(
                    (
                        "review",
                        "Your chosen team review checkpoint has arrived. Reassess evidence, job durations, first candidate and next priority.",
                    )
                )
            if (
                plan.get("first_result_deadline_wall", now + 1) <= now
                and not approved
                and not alerts.get("first_result")
            ):
                events.append(
                    (
                        "first_result",
                        "Your first-result checkpoint arrived without a reviewed candidate. Decide a concrete delivery/review action or explain the charter-specific alternative.",
                    )
                )
            if view["phase"] == "finalization" and not alerts.get("finalization"):
                events.append(
                    (
                        "finalization",
                        "The manager-chosen team finalization boundary has arrived. Assign exact refit/check/delivery owners; preserve the last reviewed output.",
                    )
                )
            if view["allocated_remaining_seconds"] <= 0 and not alerts.get("allocation"):
                events.append(
                    (
                        "allocation",
                        "Team allocation has elapsed. Finish safe delivery/checks or request a changed allocation; do not silently start another research cycle.",
                    )
                )
            for key, message in events:
                alerts[key] = now
                rt.notify(team["lead"], message)
                if key in ("first_result", "allocation"):
                    rt.notify(rt.root["id"], f"Team {team['id']}: " + message)
                rt.store.event("team_time_checkpoint", {"team_id": team["id"], "kind": key})
            if events:
                rt.store.put("team", team["id"], team)
