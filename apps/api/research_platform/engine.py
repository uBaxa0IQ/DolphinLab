"""LangGraph agent turns backed by the existing research tools and PostgreSQL facts."""

import asyncio
import copy
import json
import os
import time

from sqlalchemy import select

from research_context import prepare_context, compact, build_context, is_context_error
from research_jobs import TERMINAL
from research_llm import normalize_arguments
from research_runtime import Runtime
from research_store import dumps

from .agent_graph import build_agent_graph
from .database import Directive, Event, ResearchMail, Run, Session, now
from .project_policy import context_message
from .remote_jobs import RemoteJobs


class PlatformMonitor:
    """Passive JSON export. PostgreSQL remains the authoritative state store."""

    def __init__(self, runtime):
        self.runtime, self.previous = runtime, 0.0

    def update(self, force=False):
        if not force and time.monotonic() - self.previous < 3:
            return
        self.previous = time.monotonic()
        from research_monitor import atomic_write

        atomic_write(
            self.runtime.config.work_dir / "runtime-state.json",
            json.dumps(self.runtime.snapshot(), ensure_ascii=False, default=str),
        )


ACK_TOOL = {
    "type": "function",
    "function": {
        "name": "acknowledge_directive",
        "description": "Acknowledge a user directive relayed by PM. State your decision and reason; only mark completed after actual work.",
        "parameters": {
            "type": "object",
            "properties": {
                "directive_id": {"type": "string"},
                "state": {
                    "type": "string",
                    "enum": ["accepted", "rejected", "completed"],
                },
                "reason": {"type": "string"},
            },
            "required": ["directive_id", "state", "reason"],
            "additionalProperties": False,
        },
    },
}


class PlatformRuntime(Runtime):
    def __init__(self, config, client, *, store, saver, policy, run_id, capabilities):
        self.platform_run_id, self.policy, self.saver, self.capabilities = (
            run_id,
            policy,
            saver,
            capabilities,
        )
        self.graphs = {}
        self.last_control = 0.0
        self.pause_requested = False
        self.finish_requested = False
        self.budget_warned = False
        self.current_usage = {}
        super().__init__(
            config,
            client,
            store=store,
            jobs_factory=RemoteJobs,
            monitor_factory=PlatformMonitor,
        )

    def state(self, agent):
        state = super().state(agent)
        state["allowed_stack"] = self.capabilities
        state["project_policy"] = self.policy
        state["api_budget"] = {
            "limit": self.policy["limits"].get("api_budget"),
            **self.current_usage,
        }
        state["external_control"] = {
            "pause_requested": self.pause_requested,
            "finalization_requested": self.finish_requested,
        }
        return state

    async def execute_turn(self, agent, tools):
        if agent["role"] == "tech_lead":
            tools = [*tools, ACK_TOOL]
        agent["wait_requested"] = False

        async def prepare(_):
            messages = await prepare_context(
                agent, self.state(agent), self.client, self.config, self.store, tools
            )
            if messages and messages[0]["role"] == "system":
                messages[0] = {
                    **messages[0],
                    "content": messages[0]["content"]
                    + "\nPlatform policy: the project's exploration preference controls whether to deepen or diversify; generic portfolio examples above are suggestions. Never force a team count or exploration intensity that contradicts the project policy.",
                }
            return {
                "context": [*messages, context_message(self.policy)],
                "history": copy.deepcopy(agent["history"]),
                "summary": agent.get("summary", ""),
            }

        async def complete(state):
            try:
                response, usage = await self.client.complete(state["context"], tools)
            except Exception as error:
                if not is_context_error(error):
                    raise
                await compact(
                    agent,
                    self.client,
                    self.store,
                    facts=self.memory_facts(agent),
                    aggressive=True,
                )
                response, usage = await self.client.complete(
                    [
                        *build_context(agent, self.state(agent)),
                        context_message(self.policy),
                    ],
                    tools,
                )
            agent["history"].append(response)
            agent["failures"] = 0
            agent["turn_count"] += 1
            agent["last_actual_prompt_tokens"] = usage.get("prompt_tokens")
            self.store.put("agent", agent["id"], agent)
            self.store.event("model_usage", {"agent": agent["id"], "usage": usage})
            return response

        async def dispatch(_, call):
            receipt_id = f"{agent.get('epoch', 0)}:{call['id']}"
            result = self.store.receipt(agent["id"], receipt_id)
            if result is None:
                name = call["function"]["name"]
                arguments = normalize_arguments(call["function"]["arguments"])
                self.store.event(
                    "tool_started",
                    {
                        "agent": agent["id"],
                        "call_id": call["id"],
                        "tool": name,
                        "arguments": arguments,
                    },
                )
                try:
                    result = await self.dispatch(agent, name, arguments)
                except Exception as error:
                    result = {"error": str(error)}
                self.store.save_receipt(agent["id"], receipt_id, result)
                self.store.event(
                    "tool_finished",
                    {
                        "agent": agent["id"],
                        "call_id": call["id"],
                        "tool": name,
                        "result": result,
                    },
                )
            agent["history"].append(
                {"role": "tool", "tool_call_id": call["id"], "content": dumps(result)}
            )
            self.store.put("agent", agent["id"], agent)
            return result

        # Fresh input starts a reconciled turn; abandoned tool calls are resolved by the runtime
        # and receipts, rather than blindly resuming an unknown external side effect.
        graph = build_agent_graph(
            checkpointer=self.saver,
            prepare=prepare,
            complete=complete,
            dispatch=dispatch,
            one_turn=True,
        )
        result = await graph.ainvoke(
            {
                "agent_id": agent["id"],
                "role": agent["role"],
                "history": copy.deepcopy(agent["history"]),
            },
            {
                "configurable": {
                    "thread_id": f"{self.platform_run_id}:{agent['id']}:{agent.get('epoch', 0)}"
                }
            },
            durability="sync",
        )
        return result["response"].get("tool_calls", [])

    async def dispatch(self, agent, name, args):
        if name == "acknowledge_directive":
            if agent["role"] != "tech_lead" or args.get("state") not in (
                "accepted",
                "rejected",
                "completed",
            ):
                raise ValueError("Only tech lead can acknowledge a directive")
            with Session.begin() as db:
                directive = db.get(Directive, args["directive_id"])
                if not directive or directive.run_id != self.platform_run_id:
                    raise ValueError("Unknown directive for this run")
                directive.state, directive.decision = args["state"], args["reason"]
                db.add(
                    Event(
                        project_id=directive.project_id,
                        kind="directive_decision",
                        payload={
                            "directive_id": directive.id,
                            "state": directive.state,
                            "name": directive.decision,
                        },
                    )
                )
            return {"state": args["state"]}
        if name == "create_team":
            count = sum(
                t["state"] in ("active", "draining") for t in self.store.all("team")
            )
            if count >= self.policy["limits"]["max_teams"] or self.finish_requested:
                raise ValueError(
                    "Project team ceiling/finalization boundary reached; reuse or close a direction"
                )
        if self.pause_requested and name in ("run_python", "run_script", "create_team"):
            raise ValueError(
                "User requested pause; finish the current turn without launching new work"
            )
        if (
            self.finish_requested
            and name == "plan_run"
            and not args.get("finalize_now")
        ):
            raise ValueError(
                "User requested finalization; the global reserve cannot be removed"
            )
        return await super().dispatch(agent, name, args)

    async def control_tick(self):
        if time.monotonic() - self.last_control < 1:
            return
        self.last_control = time.monotonic()
        with Session.begin() as db:
            run = db.get(Run, self.platform_run_id)
            action = run.requested_action
            self.current_usage = copy.deepcopy(run.usage or {})
            budget = self.policy["limits"].get("api_budget")
            if (
                budget
                and not self.budget_warned
                and (run.usage or {}).get("accounted_cost", 0) >= budget * 0.75
            ):
                self.budget_warned = True
                self.notify(
                    self.root["id"],
                    "The configured API budget is approaching its conservative admission boundary. Prioritize independent review and a verified publication now.",
                    "runtime",
                )
            if action == "stop":
                self.done = True
            elif action == "pause":
                self.pause_requested = True
                self.scheduling_paused = True
                self.jobs.paused = True
                active = [
                    j
                    for j in self.store.all("job")
                    if j["state"] not in TERMINAL | {"queued"}
                ]
                if not active and not any(
                    not task.done() for task in self.turns.values()
                ):
                    self.jobs.preserve_queued = True
                    self.done = True
            elif action == "finish" and not self.finish_requested:
                self.finish_requested = True
                self.config.reserve = self.config.remaining
                self.config.deadline_wall = min(
                    self.config.deadline_wall, time.time() + 600
                )
                self.notify(
                    self.root["id"],
                    "User requested final delivery. Stop opening research directions, obtain evaluator verdicts and handovers, and finish using verified artifacts. The remaining deadline is at most ten minutes.",
                    "PM",
                )
            # Deliver external instructions atomically with their mailbox insertion.
            directives = db.scalars(
                select(Directive)
                .where(
                    Directive.project_id == run.project_id,
                    Directive.state.in_(["received", "queued_for_tech_lead"]),
                )
                .with_for_update()
            ).all()
            if not self.pause_requested:
                for directive in directives:
                    if directive.run_id and directive.run_id != run.id:
                        continue
                    db.add(
                        ResearchMail(
                            run_id=run.id,
                            recipient=self.root["id"],
                            sender="project_manager",
                            body=f"USER DIRECTIVE {directive.id}: {directive.content}\nUse acknowledge_directive with a decision and reason. You own implementation delegation.",
                            created=time.time(),
                        )
                    )
                    directive.state, directive.run_id = "delivered", run.id
            run.elapsed_seconds = max(0.0, self.config.seconds - self.config.remaining)
            run.updated = now()
            run.snapshot = self.snapshot()
            run.publication = copy.deepcopy(self.published)
            db.add(
                Event(
                    project_id=run.project_id,
                    kind="runtime_update",
                    payload={"run_id": run.id},
                )
            )

    def snapshot(self):
        teams = self.store.all("team")
        agents = self.store.all("agent")
        jobs = self.store.all("job")
        candidates = self.store.all("candidate")
        palette = ["sage", "lilac", "sand", "rose"]
        result = {
            "teams": [],
            "experiments": [],
            "remaining_seconds": self.config.remaining,
            "resources": {"cpu": self.jobs.used_cpu, "ram_mb": self.jobs.used_ram},
            "published": self.published,
            "findings": self.store.all("finding")[-8:],
            "candidates": [
                {
                    key: candidate.get(key)
                    for key in (
                        "id",
                        "approved",
                        "review_state",
                        "findings",
                        "references",
                    )
                }
                for candidate in candidates[-8:]
            ],
        }
        for team in teams:
            members = [a for a in agents if a.get("team_id") == team["id"]]
            active_jobs = [
                j
                for j in jobs
                if j["owner"] in team["members"] and j["state"] not in TERMINAL
            ]
            result["teams"].append(
                {
                    "id": team["id"],
                    "name": team.get("direction", "Команда"),
                    "focus": team.get("goal", ""),
                    "icon": "layers",
                    "state": "running" if active_jobs else "waiting",
                    "stage": "Вычисления" if active_jobs else "Координация",
                    "current": team.get("note", team.get("goal", "")),
                    "updated": "только что",
                    "completed": sum(
                        j["state"] in TERMINAL
                        for j in jobs
                        if j["owner"] in team["members"]
                    ),
                    "total": sum(j["owner"] in team["members"] for j in jobs),
                    "members": [
                        {
                            "name": a["id"],
                            "role": a["role"],
                            "state": a.get("activity", a["state"]),
                            "color": palette[i % 4],
                        }
                        for i, a in enumerate(members)
                    ],
                }
            )
        for job in jobs[-30:]:
            result["experiments"].append(
                {
                    "id": job["id"],
                    "name": job.get("metadata", {}).get("purpose", "Python-задание"),
                    "team": next(
                        (
                            t.get("direction", "Команда")
                            for t in teams
                            if t["id"] == job.get("metadata", {}).get("team_id")
                        ),
                        "",
                    ),
                    "status": "running"
                    if job["state"] not in TERMINAL
                    else "verified"
                    if any(
                        c.get("approved") and c.get("job_id") == job["id"]
                        for c in candidates
                    )
                    else "succeeded"
                    if job["state"] == "succeeded"
                    else "cancelled"
                    if job["state"] == "cancelled"
                    else "failed",
                    "score": "—",
                    "duration": f"{int(job.get('finished', time.time()) - job.get('started', job['submitted']))} с",
                    "detail": f"Состояние кода: {job['state']}. Успешный выход кода сам по себе не подтверждает качество модели. Логи и исходник доступны в артефактах запуска.",
                }
            )
        return result
