"""Event-driven dynamic research teams. Agents own all ML decisions and code."""

from __future__ import annotations

import asyncio
import copy
import itertools
import time
from abc import ABC, abstractmethod
from pathlib import Path

from dolphinlab.common.files import digest
from dolphinlab.common.serialization import dumps, uid
from dolphinlab.infrastructure.llm import (
    ResponseProblem,
    backoff,
    failure_kind,
    request_actor,
    request_effort,
)
from dolphinlab.research.context import (
    compact,
    refresh_context_status,
)
from dolphinlab.research.jobs import TERMINAL
from dolphinlab.research.ledger import ResearchLedger
from dolphinlab.research.recovery import RunLease, checkpoint, open_run
from dolphinlab.research.roles import MANAGERS, ROLES
from dolphinlab.research.timing import Timing
from dolphinlab.research.tools import resolve_call

from .candidates import CandidateRegistry
from .memory import MemoryManager
from .ports import ResearchStore
from .staffing import Staffing
from .teams import TeamManager


class Runtime(ABC):
    def __init__(self, config, client, *, store: ResearchStore, jobs_factory, monitor_factory):
        self.config, self.client = config, client
        config.work_dir.mkdir(parents=True, exist_ok=True)
        self.lease = RunLease(config.work_dir)
        try:
            self.store = store
            self.store.on_close = self.lease.close
            self.run_meta = open_run(config, self.store)
            if (
                config.context_window
                and hasattr(client, "window_source")
                and client.window_source == "not confirmed"
            ):
                client.window_source = "persisted confirmed run capacity"
        except Exception:
            if hasattr(self, "store"):
                self.store.close()
            else:
                self.lease.close()
            raise
        self.agents = {}
        self.turns = {}
        self.done = False
        self.phase_announced = False
        self.published = None
        self.api_blocked = False
        self.inline_jobs = set()
        self.jobs = jobs_factory(config, self.store, self.job_finished)
        self.ledger = ResearchLedger(self)
        self.timing = Timing(self)
        self.teams = TeamManager(self)
        self.candidates = CandidateRegistry(self)
        self.memory = MemoryManager(self)
        self.task_text = config.task_path.read_text(encoding="utf-8")
        restored = self.store.all("agent")
        self.staffing = Staffing(self, legacy=bool(restored))
        if restored:
            self.agents = {a["id"]: a for a in restored}
            for saved_agent in restored:
                self.staffing.bind(saved_agent, legacy=True)
            roots = [a for a in restored if a["role"] == "tech_lead"]
            if len(roots) != 1:
                self.store.close()
                raise ValueError("Saved run must have exactly one tech lead")
            self.root = roots[0]
            if (
                self.run_meta.get("previous_status") in ("completed", "expired")
                or self.config.remaining <= 0.2
            ):
                self.done = True
            else:
                self.restore_sessions()
        else:
            self.root = self.new_agent(
                "tech_lead",
                None,
                None,
                "Solve the task autonomously. Read the original task and start organizing research.",
            )
        publications = self.store.all("publication")
        if publications and publications[0].get("candidate_id"):
            self.published = publications[0]
        self.last_checkpoint = time.monotonic()
        if hasattr(client, "config"):
            client.log_dir = config.work_dir / "requests"
            client.log_event = self.model_event
        self.monitor = monitor_factory(self)
        self.run_meta = checkpoint(
            config,
            self.store,
            self.run_meta,
            self.run_meta.get("previous_status", "running") if self.done else "running",
        )
        self.store.event(
            "resumed" if restored else "startup",
            {
                "task": str(config.task_path),
                "model": config.model,
                "reasoning_effort": config.reasoning_effort,
            },
        )
        self.monitor.update(force=True)

    def model_event(self, kind, data):
        actor = self.agents.get(data.get("agent"))
        if actor:
            activity = {
                "model_queued": "ожидание слота/API",
                "model_request_started": "запрос к модели",
                "model_response": "ответ получен",
                "model_request_failed": "ошибка API",
            }.get(kind)
            if activity:
                actor["activity"] = (
                    "сжатие контекста" if data.get("purpose") == "compaction" else activity
                )
                actor["last_activity_wall"] = time.time()
                self.store.put("agent", actor["id"], actor)
        self.store.event(kind, data)

    def restore_sessions(self):
        for team in self.store.all("team"):
            if team.get("closing_reason") == "runtime shutdown" and team.get("pre_shutdown_state"):
                team["state"] = team.pop("pre_shutdown_state")
                self.store.put("team", team["id"], team)
            if team["state"] == "draining":
                team["drain_until"] = time.monotonic() + min(
                    self.config.remaining,
                    max(
                        0.0,
                        team.get("drain_deadline_wall", self.config.deadline_wall) - time.time(),
                    ),
                )
                self.store.put("team", team["id"], team)
        for agent in self.agents.values():
            active = not agent["team_id"] or self.store.get("team", agent["team_id"])["state"] in (
                "active",
                "draining",
            )
            if active:
                self.complete_interrupted_calls(agent)
                state = agent.pop("pre_shutdown_state", agent["state"])
                if state == "closed":
                    self.store.put("agent", agent["id"], agent)
                    continue
                if self.store.get("assignment", agent["assignment_id"])["state"] == "completed":
                    agent.update(
                        state="waiting",
                        retry_at=0.0,
                        wake_at=self.config.started + self.config.seconds,
                    )
                    self.store.put("agent", agent["id"], agent)
                    continue
                agent.update(
                    state="ready"
                    if state != "blocked" or agent["role"] == "tech_lead"
                    else "blocked",
                    retry_at=0.0,
                    wake_at=0.0,
                )
                self.store.put("agent", agent["id"], agent)
                self.notify(
                    agent["id"],
                    "Coordinator resumed the existing run. Original deadline is unchanged. "
                    "Reconcile restored job/candidate state before issuing work again. Unknown actions were not blindly replayed.",
                )

    def new_agent(self, role, team_id, supervisor, assignment, focus_revision=1):
        if role not in ROLES:
            raise ValueError("Only catalog roles are allowed")
        if sum(a["state"] != "closed" for a in self.agents.values()) >= self.config.max_agents:
            raise ValueError("Active agent capacity reached; reuse or close a team")
        ident = uid(role)
        workspace = self.config.work_dir / "teams" / (team_id or "lead") / ident
        workspace.mkdir(parents=True)
        agent = {
            "id": ident,
            "role": role,
            "team_id": team_id,
            "supervisor": supervisor,
            "reasoning_effort": self.config.role_reasoning_efforts.get(
                role, self.config.reasoning_effort
            ),
            "workspace": str(workspace),
            "history": [],
            "summary": "",
            "note": "",
            "focus": assignment,
            "focus_revision": focus_revision,
            "epoch": 0,
            "turn_count": 0,
            "state": "ready",
            "wake_at": 0.0,
            "retry_at": 0.0,
            "failures": 0,
        }
        self.agents[ident] = agent
        self.staffing.bind(agent)
        self.store.send(ident, supervisor or "runtime", assignment)
        return agent

    def notify(self, recipient, text, sender="runtime"):
        if recipient in self.agents and self.agents[recipient]["state"] != "closed":
            self.store.send(recipient, sender, text)

    def job_view(self, job, tail_bytes=4000):
        facts = {k: v for k, v in job.items() if k != "token"}
        facts["job_id"] = job["id"]
        facts["elapsed_seconds"] = round(
            job.get("finished", time.time()) - job.get("started", job["submitted"]), 2
        )
        facts["logs"] = {
            name: str(Path(job["folder"]) / (name + ".log")) for name in ("stdout", "stderr")
        }
        for name, log_path in facts["logs"].items():
            path = Path(log_path)
            if path.is_file():
                with path.open("rb") as stream:
                    stream.seek(max(0, path.stat().st_size - tail_bytes))
                    facts[name + "_tail"] = stream.read().decode("utf-8", errors="replace")
        return facts

    async def job_finished(self, job):
        facts = self.job_view(job)
        message = (
            "Python job completed. Interpret the evidence and report the useful conclusion. "
            + dumps(facts)
        )
        if job["id"] not in self.inline_jobs:
            self.notify(job["owner"], message)
        owner = self.agents[job["owner"]]
        owner["phase_pending"] = True
        self.store.put("agent", owner["id"], owner)
        # Routine completion wakes the specialist. The board is current for all colleagues;
        # waking a manager per diagnostic job caused duplicate log reading and status polling.
        if owner["supervisor"] and job["state"] in ("memory_exceeded", "timed_out"):
            self.notify(owner["supervisor"], "Resource/time blocker: " + dumps(facts))
        if self.published:
            target = Path(self.published["path"])
            if not target.is_file() or digest(target) != self.published["sha256"]:
                self.publish(self.published["candidate_id"])
                self.notify(
                    owner["id"],
                    "Experiment code changed the published submission; the reviewed checkpoint was restored. Write prediction artifacts only in JOB_DIR and register that path.",
                )
                self.store.event("publication_restored", {"job_id": job["id"]})
        if job["state"] in ("memory_exceeded", "timed_out"):
            self.notify(self.root["id"], message)
        self.store.event("job_completed", {"id": job["id"], "state": job["state"]})

    def team_board(self, team_id, full=False):
        team = self.store.get("team", team_id)
        jobs = self.store.all("job")
        own_jobs = [
            j
            for j in jobs
            if j.get("metadata", {}).get("team_id") == team_id
            or self.agents.get(j["owner"], {}).get("team_id") == team_id
        ]
        findings = [
            f
            for f in self.store.all("finding")
            if f["team_id"] == team_id or f["id"] in team.get("shared_findings", [])
        ]
        board = {
            "charter": team,
            "members": [
                {
                    k: a.get(k)
                    for k in (
                        ("id", "role", "state", "focus", "focus_revision", "note", "last_archive")
                        if full
                        else ("id", "role", "state", "focus", "focus_revision")
                    )
                }
                for a in self.agents.values()
                if a["team_id"] == team_id
            ],
            "jobs": [
                {k: j.get(k) for k in ("id", "owner", "state", "kind", "metadata", "folder")}
                for j in own_jobs
                if full or j["state"] not in TERMINAL
            ],
            "findings": findings if full else findings[-8:],
            "assignments": [a for a in self.store.all("assignment") if a["team_id"] == team_id][
                -20:
            ],
            "staffing_history": [
                r for r in self.store.all("staffing_change") if r["team_id"] == team_id
            ][-20:],
            "finding_count": len(findings),
            "research_progress": self.ledger.signals(team_id),
            "timing": self.timing.view(team),
            "journal": [j for j in self.store.all("team_journal") if j["team_id"] == team_id][-3:],
            "recall": "Use recall(topic=finding/job/messages) or inspect_team for detail",
        }
        if full:
            board["recent_messages"] = self.store.recent_mail(team["members"])
        return board

    def memory_facts(self, agent):
        return {
            "focus": agent.get("focus"),
            "personal_note": agent.get("note"),
            "global_note": self.root.get("note"),
            "team_board": self.team_board(agent["team_id"]) if agent["team_id"] else None,
            "published": self.published,
        }

    def state(self, agent):
        team = self.store.get("team", agent["team_id"]) if agent["team_id"] else None
        all_candidates = self.store.all("candidate")
        visible = [
            c
            for c in all_candidates
            if agent["role"] == "tech_lead"
            or c["team_id"] == agent["team_id"]
            or c.get("reviewer_id") == agent["id"]
        ]
        pending = [c for c in visible if c.get("review_state") == "pending"][-8:]
        recent = visible[-5:]
        selected = {c["id"]: c for c in pending + recent}
        jobs = self.store.all("job")
        return {
            **self.config.public(),
            "agent_id": agent["id"],
            "role": agent["role"],
            "role_profile": {
                k: self.staffing.profile(agent)[k] for k in ("id", "revision", "role")
            },
            "role_prompt": self.staffing.profile(agent)["prompt"],
            "role_catalog": [
                {"role": p["role"], "revision": p["revision"]}
                for p in self.staffing.profiles.values()
            ],
            "assignment": self.store.get("assignment", agent["assignment_id"]),
            "reviewer_load": self.staffing.reviewer_load(),
            "supervisor": agent["supervisor"],
            "workspace": agent["workspace"],
            "reasoning_effort": agent.get(
                "reasoning_effort",
                self.config.role_reasoning_efforts.get(agent["role"], self.config.reasoning_effort),
            ),
            "api_budget": self.store.all("budget"),
            "task_path": str(self.config.task_path),
            "task_root": str(self.config.task_path.parent),
            "original_task": self.task_text,
            "allowed_stack": self.capabilities,
            "team": {k: team[k] for k in ("id", "revision", "state")} if team else None,
            "personal_note": agent["note"],
            "current_focus": agent.get("focus"),
            "focus_revision": agent.get("focus_revision"),
            "context": agent.get("context_status", {}),
            "evaluation_agreement": self.ledger.active(),
            "research_progress": self.ledger.signals(agent["team_id"])
            if agent["role"] in ("tech_lead", "team_lead")
            else None,
            "last_context_archive": agent.get("last_archive"),
            "team_board": self.team_board(agent["team_id"]) if agent["team_id"] else None,
            "global_note": self.root["note"],
            "portfolio_plan": self.store.all("portfolio"),
            "time_facts": {
                "now_wall": time.time(),
                "global_deadline_wall": self.config.deadline_wall,
                "elapsed_seconds": round(self.config.seconds - self.config.remaining, 1),
                "job_timeouts_are_per_job_and_editable": True,
            },
            "team_capacity": {
                "active_agent_slots_free": self.config.max_agents
                - sum(a["state"] != "closed" for a in self.agents.values()),
                "staffing": "Explicit members plus an optional coordinator; no fixed minimum quartet",
                "max_active_agents": self.config.max_agents,
                "concurrent_model_requests": self.config.llm_concurrency,
            },
            "teams": [
                {**t, "timing": self.timing.view(t)}
                for t in self.store.all("team")
                if t["state"] in ("active", "draining")
            ]
            if agent["role"] == "tech_lead"
            else [],
            "other_directions": [
                {k: t.get(k) for k in ("id", "direction", "scope", "out_of_scope", "state")}
                for t in self.store.all("team")
                if t["state"] in ("active", "draining") and t["id"] != agent["team_id"]
            ],
            "agents": [
                {k: a[k] for k in ("id", "role", "team_id", "state")}
                for a in self.agents.values()
                if a["state"] != "closed"
                and (agent["role"] == "tech_lead" or a["team_id"] == agent["team_id"])
            ],
            "active_jobs": [
                {
                    k: j.get(k)
                    for k in ("id", "owner", "state", "kind", "cpus", "ram_mb", "metadata")
                }
                for j in jobs
                if j["state"] not in TERMINAL
            ],
            "candidates": [
                {
                    k: c.get(k)
                    for k in (
                        "id",
                        "team_id",
                        "approved",
                        "reviewer",
                        "reviewer_id",
                        "job_id",
                        "submission_path",
                        "declared_protocol_id",
                        "review_protocol_id",
                        "comparable",
                    )
                }
                for c in selected.values()
            ],
            "candidate_count": len(visible),
            "published": self.published,
            "registry": "Use recall(topic, record_id), inspect_candidate or inspect_team for evidence",
            "resources_in_use": {
                "cpus": self.jobs.used_cpu,
                "ram_requested_mb": self.jobs.used_ram,
            },
        }

    async def run(self):
        try:
            await self.jobs.recover(
                {a["id"] for a in self.agents.values() if a["state"] != "closed"}
            )
            while self.config.remaining > max(0.2, self.config.shutdown_seconds) and not self.done:
                await self.control_tick()
                if self.done:
                    break
                self.monitor.update()
                if time.monotonic() - self.last_checkpoint >= 2:
                    self.run_meta = checkpoint(self.config, self.store, self.run_meta, "running")
                    self.last_checkpoint = time.monotonic()
                if self.config.finalizing and not self.phase_announced:
                    self.phase_announced = True
                    for agent in self.agents.values():
                        self.notify(
                            agent["id"],
                            "Finalization boundary reached. Concentrate on verified output. "
                            "Running jobs may finish; no new research jobs. Tech lead can adjust the "
                            "plan with plan_run or delegate local timing to team leads; cancel obsolete work explicitly.",
                        )
                    if not self.published:
                        approved = [c for c in self.store.all("candidate") if c.get("approved")]
                        if approved:
                            self.publish(approved[-1]["id"])
                now = time.monotonic()
                self.timing.tick()
                await self.advance_teams()
                if (
                    self.api_blocked
                    and not self.turns
                    and all(j["state"] in TERMINAL for j in self.store.all("job"))
                ):
                    self.done = True
                    continue
                if (
                    self.root["state"] == "blocked"
                    and not self.turns
                    and all(j["state"] in TERMINAL for j in self.store.all("job"))
                ):
                    productive = any(
                        a["state"] not in ("closed", "blocked")
                        and (a["state"] == "ready" or self.store.pending(a["id"]))
                        for a in self.agents.values()
                    )
                    if not productive:
                        self.store.event("control_plane_blocked", {"agent": self.root["id"]})
                        self.done = True
                        continue
                for ident, agent in list(self.agents.items()):
                    if getattr(self, "scheduling_paused", False):
                        continue
                    if (
                        self.api_blocked
                        or agent["state"] in ("closed", "blocked")
                        or ident in self.turns
                        or now < agent["retry_at"]
                    ):
                        continue
                    team_draining = bool(
                        agent["team_id"]
                        and self.store.get("team", agent["team_id"])["state"] == "draining"
                    )
                    if (
                        agent["state"] == "ready"
                        or self.store.pending(ident)
                        or (not team_draining and agent["wake_at"] <= now)
                    ):
                        if agent["state"] == "waiting" and not self.store.pending(ident):
                            self.notify(
                                ident,
                                "Your chosen review timer has elapsed; current facts are in your state.",
                            )
                        self.turns[ident] = asyncio.create_task(self.turn(agent))
                for ident, task in list(self.turns.items()):
                    if task.done():
                        try:
                            task.result()
                        except asyncio.CancelledError:
                            pass
                        except Exception as error:
                            self.store.event(
                                "driver_error", {"agent": ident, "error_type": type(error).__name__}
                            )
                            self.agents[ident]["retry_at"] = time.monotonic() + 3
                        del self.turns[ident]
                await asyncio.sleep(min(0.1, self.config.remaining))
        finally:
            for task in self.turns.values():
                task.cancel()
            await asyncio.gather(*self.turns.values(), return_exceptions=True)
            await self.jobs.shutdown()
            try:
                for agent in self.agents.values():
                    self.complete_interrupted_calls(agent)
                    agent["pre_shutdown_state"] = agent["state"]
                    agent["state"] = "closed"
                    self.store.put("agent", agent["id"], agent)
                for team in self.store.all("team"):
                    if team["state"] in ("active", "draining"):
                        team["pre_shutdown_state"] = team["state"]
                        team.update(state="closed", closing_reason="runtime shutdown")
                        self.store.put("team", team["id"], team)
                if not self.published:
                    approved = [c for c in self.store.all("candidate") if c.get("approved")]
                    if approved:
                        self.publish(approved[-1]["id"])
                elif (
                    not Path(self.published["path"]).is_file()
                    or digest(self.published["path"]) != self.published["sha256"]
                ):
                    self.publish(self.published["candidate_id"])
                self.store.event(
                    "shutdown",
                    {"published": self.published, "remaining_seconds": self.config.remaining},
                )
                status = (
                    "expired"
                    if self.config.remaining <= max(0.2, self.config.shutdown_seconds)
                    else "completed"
                    if self.done and self.published
                    else "stopped"
                )
                self.run_meta = checkpoint(self.config, self.store, self.run_meta, status)
                self.store.sync_events()
                self.monitor.update(force=True)
            finally:
                await self.client.close()
        return self.published

    async def turn(self, agent):
        actor_token = request_actor.set(agent["id"])
        effort_token = request_effort.set(
            agent.get(
                "reasoning_effort",
                self.config.role_reasoning_efforts.get(agent["role"], self.config.reasoning_effort),
            )
        )
        self.store.take_mail(agent)
        agent["state"] = "running"
        self.store.put("agent", agent["id"], agent)
        try:
            tools = self.tools_for(agent)
            calls = await self.execute_turn(agent, tools)
            if agent.pop("compact_requested", False):
                try:
                    await compact(agent, self.client, self.store, facts=self.memory_facts(agent))
                except Exception as error:
                    self.notify(agent["id"], "Context compaction failed: " + type(error).__name__)
            if agent["state"] != "closed":
                if not calls:
                    message = next(
                        (
                            item
                            for item in reversed(agent["history"])
                            if item.get("role") == "assistant"
                        ),
                        {},
                    )
                    if message.get("content") and agent["supervisor"]:
                        self.notify(agent["supervisor"], message["content"], agent["id"])
                        self.journal_message(agent, message["content"], "phase_update", [])
                    agent["wake_at"] = self.config.started + self.config.seconds
                agent["state"] = "waiting" if not calls or agent["wait_requested"] else "ready"
                agent["activity"] = (
                    "ожидание события" if agent["state"] == "waiting" else "следующий шаг"
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            failure = failure_kind(error)
            agent["failures"] += 1
            response_failure = isinstance(error, ResponseProblem)
            blocked = not failure.retryable or (response_failure and agent["failures"] >= 3)
            agent["state"] = "blocked" if blocked else "waiting"
            agent["retry_at"] = time.monotonic() + backoff(failure, agent["failures"])
            agent["wake_at"] = agent["retry_at"]
            # Never log request headers/credential-bearing error objects.
            detail = str(error)
            if self.config.api_key:
                detail = detail.replace(self.config.api_key, "[redacted]")
            self.store.event(
                "model_error",
                {
                    "agent": agent["id"],
                    "error_type": type(error).__name__,
                    "category": failure.category,
                    "detail": detail,
                },
            )
            if response_failure:
                folder = self.config.work_dir / "transcripts" / agent["id"]
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / (uid("rejected-response") + ".json")
                path.write_text(
                    dumps({"raw": error.raw, "category": error.category}), encoding="utf-8"
                )
                self.store.event(
                    "model_usage",
                    {"agent": agent["id"], "purpose": "rejected response", "usage": error.usage},
                )
                self.notify(
                    agent["id"],
                    detail + f" Full rejected response is at {path}. No tool calls from it ran.",
                )
            elif failure.category == "transport":
                self.notify(
                    agent["id"],
                    "The model request failed before a complete response. No tool calls from that response ran. "
                    "For a timeout, make the next action smaller (write/append a short module or one decision) instead of repeating a large generation. "
                    f"Request timeout is {self.config.request_timeout}s; the global deadline is unchanged. Error: "
                    + (detail or type(error).__name__),
                )
            if not failure.retryable and failure.shared:
                self.api_blocked = True
                print("Model API stopped: " + detail, flush=True)
            if blocked or agent["failures"] == 3:
                recipient = agent["supervisor"] or self.root["id"]
                self.notify(
                    recipient,
                    f"Session {agent['id']} needs attention: {failure.category}. "
                    "Inspect its board/archive; restart_agent can create a fresh episode.",
                )
        finally:
            self.complete_interrupted_calls(agent)
            refresh_context_status(agent, self.state(agent), self.client, self.tools_for(agent))
            self.store.put("agent", agent["id"], agent)
            request_actor.reset(actor_token)
            request_effort.reset(effort_token)

    async def control_tick(self):
        """Read application lifecycle controls between coordinator ticks."""
        return None

    @abstractmethod
    async def execute_turn(self, agent, tools):
        """Execute a durable agent session using the configured graph adapter."""
        raise NotImplementedError

    def complete_interrupted_calls(self, agent):
        pending = {}
        for message in agent["history"]:
            for call in message.get("tool_calls", []):
                pending[call["id"]] = call
            if message["role"] == "tool":
                pending.pop(message["tool_call_id"], None)
        for call_id in pending:
            known = self.store.receipt(agent["id"], f"{agent.get('epoch', 0)}:{call_id}")
            result = (
                known
                if known is not None
                else {
                    "error": "Turn interrupted; execution outcome is not established. Inspect jobs and artifacts before repeating.",
                    "execution_status": "unknown",
                }
            )
            agent["history"].append(
                {"role": "tool", "tool_call_id": call_id, "content": dumps(result)}
            )

    def readable(self, value):
        path = Path(value)
        if not path.is_absolute():
            path = self.config.task_path.parent / path
        path = path.resolve()
        roots = (self.config.task_path.parent, self.config.work_dir, *self.config.reference_roots)
        if not any(path.is_relative_to(root) for root in roots):
            raise ValueError("Read paths must be inside the task or run directory")
        return path

    def writable(self, agent, value):
        path = Path(value)
        own = Path(agent["workspace"])
        path = (own / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_relative_to(own):
            raise ValueError(
                "Write inside your workspace; share new artifact paths instead of overwriting colleagues"
            )
        return path

    def actor(self, supplied):
        agent = self.agents.get(supplied["id"])
        if not agent or agent["state"] == "closed" or supplied.get("role") != agent["role"]:
            raise ValueError("Unknown, closed or mismatched agent identity")
        self.staffing.profile(agent)
        return agent

    def tools_for(self, agent):
        return copy.deepcopy(self.staffing.profile(agent)["tools"])

    async def dispatch(self, agent, name, args):
        agent = self.actor(agent)
        profile = self.staffing.profile(agent)
        route, resolved = resolve_call(
            agent["role"], name, args, profile["revision"], profile["tools"]
        )
        return await self._dispatch_backend(agent, route, resolved)

    def journal_message(self, agent, text, kind, references, message_id=None, next_action=None):
        return self.memory.journal_message(agent, text, kind, references, message_id, next_action)

    async def _dispatch_backend(self, agent, name, args):
        if name == "set_reasoning":
            if args["effort"] not in ("low", "medium", "high"):
                raise ValueError("Choose low, medium or high")
            agent["reasoning_effort"] = args["effort"]
            self.store.put("agent", agent["id"], agent)
            self.store.event("reasoning_changed", {"agent": agent["id"], **args})
            return {"next_turn_effort": args["effort"], "reason": args["reason"]}
        if name == "send_message":
            recipient = args["to"]
            if recipient not in self.agents:
                teams = {t["id"]: t for t in self.store.all("team")}
                if recipient in teams:
                    recipient = teams[recipient]["lead"]
                elif recipient == "supervisor" and agent.get("supervisor"):
                    recipient = agent["supervisor"]
                elif recipient in ROLES:
                    matches = [
                        a["id"]
                        for a in self.agents.values()
                        if a["role"] == recipient
                        and a["state"] != "closed"
                        and (
                            recipient == "tech_lead"
                            or agent["team_id"] is None
                            or a["team_id"] == agent["team_id"]
                        )
                    ]
                    if len(matches) != 1:
                        raise ValueError(
                            "Recipient role is ambiguous/unavailable; use an agent ID from "
                            + dumps(matches)
                        )
                    recipient = matches[0]
                else:
                    raise ValueError(
                        "Unknown recipient; use an agent ID, team ID (routes to its lead), supervisor, or a unique role"
                    )
            args = {**args, "to": recipient}
            target = self.agents[args["to"]]
            if target["state"] == "closed":
                raise ValueError("Recipient is closed; choose an active team/agent")
            if args.get("kind") == "assignment":
                if agent["role"] != "tech_lead" and target["supervisor"] != agent["id"]:
                    raise ValueError(
                        "Assign work through the recipient's lead; ordinary collaboration messages are unrestricted"
                    )
                target["focus"] = args["text"]
                if target["team_id"]:
                    target["focus_revision"] = self.store.get("team", target["team_id"])["revision"]
                self.staffing.assign(target, args["text"], agent["id"], "lead reassignment")
            seq = self.store.send(args["to"], agent["id"], args["text"])
            kind = args.get("kind", "message")
            self.journal_message(
                agent, args["text"], kind, args.get("references", []), seq, args.get("next_action")
            )
            if kind in ("finding", "blocker"):
                target["phase_pending"] = True
                self.store.put("agent", target["id"], target)
            if kind == "blocker":
                if args.get("candidate_id") and agent["role"] == "evaluator":
                    self.manage_candidate(
                        agent,
                        {
                            "action": "flag",
                            "candidate_id": args["candidate_id"],
                            "reason": args["text"],
                            "references": args.get("references", []),
                        },
                    )
                if agent["team_id"] and not (
                    args.get("candidate_id") and agent["role"] == "evaluator"
                ):
                    team = self.store.get("team", agent["team_id"])
                    for lead in {team["lead"], self.root["id"]} - {args["to"]}:
                        self.notify(
                            lead, "BLOCKER from " + agent["id"] + ": " + args["text"], agent["id"]
                        )
            return {"message_id": seq, "recipient": recipient}
        if name == "wait":
            seconds = max(0.5, float(args.get("seconds", self.config.remaining)))
            agent["wait_requested"] = True
            agent["wake_at"] = time.monotonic() + min(seconds, self.config.remaining)
            return {"wake_in_seconds": seconds, "message_events": True}
        if name == "finish":
            if not self.published:
                raise ValueError("Publish an approved candidate before finishing")
            if any(t["state"] in ("active", "draining") for t in self.store.all("team")):
                raise ValueError(
                    "Research teams are still active. Consult leads, resolve candidate caveats, consider reassigning/opening a feasible direction, then collect handovers and close teams before finish. One job's timeout does not exhaust the run budget."
                )
            if any(j["state"] not in TERMINAL for j in self.store.all("job")):
                raise ValueError("Resolve outstanding jobs before ending the run")
            if not args["reason"].strip():
                raise ValueError("Explain the evidence for stopping with unused time")
            self.store.put(
                "stop_decision",
                "current",
                {
                    "reason": args["reason"],
                    "remaining_seconds": self.config.remaining,
                    "created": time.time(),
                },
            )
            self.done = True
            return {"finished": True, "published": self.published}
        if name == "staffing":
            return (
                self.staffing.complete(agent, args)
                if args["action"] == "complete"
                else self.staffing.reviewer(agent, args)
            )
        if name == "team":
            return await self.manage_team(agent, args)
        if name == "research":
            return self.ledger.handle(agent, args)
        if name == "timing":
            result = self.timing.handle(agent, args)
            self.run_meta = checkpoint(self.config, self.store, self.run_meta)
            return result
        if name == "files":
            action = args["action"]
            if agent["role"] in MANAGERS and action in ("write", "append", "replace"):
                raise ValueError(
                    "Managers do not write implementation files. Assign a specialist; use write_note for coordination."
                )
            path = (
                self.writable(agent, args["path"])
                if action in ("write", "append", "replace")
                else self.readable(args["path"])
            )
            if action == "replace":
                original = path.read_text(encoding="utf-8")
                old = args["old_text"]
                expected = int(args.get("expected_matches", 1))
                matches = original.count(old) if old else 0
                if not old or expected < 1 or matches != expected:
                    raise ValueError(
                        f"Literal edit expected {expected} matches, found {matches}; read the actual file before editing"
                    )
                path.write_text(original.replace(old, args["new_text"]), encoding="utf-8")
                return {"path": str(path), "replacements": matches, "bytes": path.stat().st_size}
            if action in ("write", "append"):
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a" if action == "append" else "w", encoding="utf-8") as out:
                    out.write(args["content"])
                return {"path": str(path), "bytes": path.stat().st_size}
            if action == "list":
                return {
                    "path": str(path),
                    "entries": [
                        {"name": p.name, "directory": p.is_dir()}
                        for p in sorted(path.iterdir())
                        if p.name != ".env"
                    ],
                }
            if path.name == ".env":
                raise ValueError(
                    "Credentials are held by the runtime and must not enter model context"
                )
            start = max(0, args.get("start_line", 1) - 1)
            count = max(1, args.get("lines", 200))
            with path.open(encoding="utf-8") as stream:
                lines = list(itertools.islice(stream, start, start + count))
                more = next(stream, None) is not None
            return {
                "path": str(path),
                "start_line": start + 1,
                "has_more": more,
                "text": "".join(lines),
            }
        if name == "python_job":
            action = args["action"]
            if action != "run":
                job = self.store.get("job", args["job_id"])
                owner = self.agents[job["owner"]]
                assigned_source = (
                    action == "status"
                    and agent["role"] == "evaluator"
                    and any(
                        c["job_id"] == job["id"] and c.get("reviewer_id") == agent["id"]
                        for c in self.store.all("candidate")
                    )
                )
                if (
                    agent["role"] != "tech_lead"
                    and owner["team_id"] != agent["team_id"]
                    and not assigned_source
                ):
                    raise ValueError("Job belongs to another team")
                if action == "cancel":
                    return self.jobs.cancel(job["id"])
                return self.job_view(job, tail_bytes=16000)
            if agent["role"] not in {"ml_engineer", "data_engineer", "data_analyst", "evaluator"}:
                raise ValueError(
                    "This role cannot execute Python; delegate to an execution specialist"
                )
            if self.store.get("assignment", agent["assignment_id"])["state"] != "active":
                raise ValueError("Request a new assignment before starting new computations")
            if "code" in args:
                path = self.writable(agent, uid("script") + ".py")
                path.write_text(args["code"], encoding="utf-8")
            else:
                path = self.writable(agent, args["path"])
            if path.suffix != ".py" or not path.is_file():
                raise ValueError("Provide an existing .py path or code")
            team = self.store.get("team", agent["team_id"]) if agent["team_id"] else None
            default_kind = (
                "evaluation"
                if agent["role"] == "evaluator"
                else (
                    "finalization"
                    if agent["role"] == "ml_engineer"
                    and team
                    and team.get("purpose") == "delivery"
                    and not self.published
                    else "research"
                )
            )
            kind = args.get("kind", default_kind)
            if team and team["state"] != "active" and kind == "research":
                raise ValueError(
                    "Team is draining: preserve results and verify/finalize existing work"
                )
            if team and kind == "research":
                timing = self.timing.view(team)
                if timing["allocated_remaining_seconds"] <= 0 or timing["phase"] == "finalization":
                    raise ValueError(
                        "Team's manager-chosen time boundary reached. Ask your lead to replan, or run evaluation/finalization work."
                    )
            job = self.jobs.submit(
                agent["id"],
                path,
                args.get("args", []),
                cpus=int(args.get("cpus", 2)),
                ram_mb=int(args.get("ram_mb", 4096)),
                timeout=float(args.get("timeout", 600)),
                kind=kind,
                source_dir=Path(agent["workspace"]),
                metadata={
                    "team_id": agent["team_id"],
                    "assignment_revision": agent.get("focus_revision"),
                    "assignment_id": agent.get("assignment_id"),
                    "role_profile_id": agent.get("role_profile_id"),
                    "session_id": agent.get("session_id"),
                    "team_revision": team["revision"] if team else None,
                    "protocol_id": self.ledger.active()["id"] if self.ledger.active() else None,
                    "purpose": args.get("purpose", "current assignment"),
                },
            )
            inline_wait = min(
                30.0,
                max(0.0, float(args.get("wait_for_seconds", 10))),
                max(0.0, self.config.remaining - self.config.shutdown_seconds),
            )
            if inline_wait:
                self.inline_jobs.add(job["id"])
                try:
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(self.jobs.tasks[job["id"]]), inline_wait
                        )
                    except asyncio.TimeoutError:
                        pass
                    current = self.store.get("job", job["id"])
                    if current["state"] in TERMINAL:
                        self.store.event(
                            "job_result_inline", {"job_id": job["id"], "agent": agent["id"]}
                        )
                        return {**self.job_view(current), "completion_in_tool_result": True}
                finally:
                    self.inline_jobs.discard(job["id"])
            current = self.store.get("job", job["id"])
            return {
                "job_id": job["id"],
                "folder": job["folder"],
                "state": current["state"],
                "script": job["script"],
                "source_path": job["source_path"],
                "stdout_path": str(Path(job["folder"]) / "stdout.log"),
                "stderr_path": str(Path(job["folder"]) / "stderr.log"),
                "completion_wakes_owner": True,
                "next_action": "Continue other useful work or wait() for completion; avoid polling.",
            }
        if name == "memory":
            return self.manage_memory(agent, args)
        if name == "candidate":
            return self.manage_candidate(agent, args)
        raise ValueError("Unknown tool")

    def manage_memory(self, agent, args):
        return self.memory.manage_memory(agent, args)

    async def retire_team(self, team, archived=False, reason="completed"):
        return await self.teams.retire_team(team, archived, reason)

    async def advance_teams(self):
        return await self.teams.advance_teams()

    async def manage_team(self, agent, args):
        return await self.teams.manage_team(agent, args)

    def manage_candidate(self, agent, args):
        return self.candidates.manage_candidate(agent, args)

    def withdraw_candidate(self, candidate):
        return self.candidates.withdraw_candidate(candidate)

    def publish(self, ident):
        return self.candidates.publish(ident)
