"""Event-driven dynamic research teams. Agents own all ML decisions and code."""
from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import os
import shutil
import time
from pathlib import Path

from research_context import build_context, compact, is_context_error, prepare_context, archive_context, refresh_context_status
from research_jobs import Jobs, TERMINAL
from research_llm import normalize_arguments, failure_kind, backoff, ResponseProblem, request_actor, request_effort
from research_roles import ROLES, SPECIALISTS, MANAGERS
from research_recovery import RunLease, open_run, checkpoint
from research_ledger import ResearchLedger
from research_monitor import Monitor
from research_stack import STACK
from research_store import Store, dumps, uid
from research_tools import tools_for, resolve_call
from research_timing import Timing


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


class Runtime:
    def __init__(self, config, client):
        self.config, self.client = config, client
        config.work_dir.mkdir(parents=True, exist_ok=True)
        self.lease = RunLease(config.work_dir)
        try:
            self.store = Store(config.work_dir / "state.sqlite3")
            self.store.on_close = self.lease.close
            self.run_meta = open_run(config, self.store)
            if config.context_window and hasattr(client, "window_source") and client.window_source == "not confirmed":
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
        self.jobs = Jobs(config, self.store, self.job_finished)
        self.ledger = ResearchLedger(self)
        self.timing = Timing(self)
        self.task_text = config.task_path.read_text(encoding="utf-8")
        restored = self.store.all("agent")
        if restored:
            self.agents = {a["id"]: a for a in restored}
            roots = [a for a in restored if a["role"] == "tech_lead"]
            if len(roots) != 1:
                self.store.close()
                raise ValueError("Saved run must have exactly one tech lead")
            self.root = roots[0]
            if self.run_meta.get("previous_status") in ("completed", "expired") or self.config.remaining <= .2:
                self.done = True
            else:
                self.restore_sessions()
        else:
            self.root = self.new_agent("tech_lead", None, None,
                                       "Solve the task autonomously. Read the original task and start organizing research.")
        publications = self.store.all("publication")
        if publications and publications[0].get("candidate_id"):
            self.published = publications[0]
        self.last_checkpoint = time.monotonic()
        if hasattr(client, "config"):
            client.log_dir = config.work_dir / "requests"
            client.log_event = self.model_event
        self.monitor = Monitor(self)
        self.run_meta = checkpoint(config, self.store, self.run_meta,
                                   self.run_meta.get("previous_status", "running") if self.done else "running")
        self.store.event("resumed" if restored else "startup", {"task": str(config.task_path), "model": config.model,
                                      "reasoning_effort": config.reasoning_effort})
        self.monitor.update(force=True)

    def model_event(self, kind, data):
        actor = self.agents.get(data.get("agent"))
        if actor:
            activity = {"model_queued": "ожидание слота/API", "model_request_started": "запрос к модели",
                        "model_response": "ответ получен", "model_request_failed": "ошибка API"}.get(kind)
            if activity:
                actor["activity"] = "сжатие контекста" if data.get("purpose") == "compaction" else activity
                actor["last_activity_wall"] = time.time()
                self.store.put("agent", actor["id"], actor)
        self.store.event(kind, data)

    def restore_sessions(self):
        for team in self.store.all("team"):
            if team.get("closing_reason") == "runtime shutdown" and team.get("pre_shutdown_state"):
                team["state"] = team.pop("pre_shutdown_state")
                self.store.put("team", team["id"], team)
            if team["state"] == "draining":
                team["drain_until"] = time.monotonic() + min(self.config.remaining,
                    max(0., team.get("drain_deadline_wall", self.config.deadline_wall) - time.time()))
                self.store.put("team", team["id"], team)
        for agent in self.agents.values():
            active = not agent["team_id"] or self.store.get("team", agent["team_id"])["state"] in ("active", "draining")
            if active:
                self.complete_interrupted_calls(agent)
                state = agent.pop("pre_shutdown_state", agent["state"])
                agent.update(state="ready" if state != "blocked" or agent["role"] == "tech_lead" else "blocked",
                             retry_at=0., wake_at=0.)
                self.store.put("agent", agent["id"], agent)
                self.notify(agent["id"], "Coordinator resumed the existing run. Original deadline is unchanged. "
                            "Reconcile restored job/candidate state before issuing work again. Unknown actions were not blindly replayed.")

    def new_agent(self, role, team_id, supervisor, assignment):
        if role not in ROLES:
            raise ValueError("Only fixed catalog roles are allowed")
        if sum(a["state"] != "closed" for a in self.agents.values()) >= self.config.max_agents:
            raise ValueError("Active agent capacity reached; reuse or close a team")
        ident = uid(role)
        workspace = self.config.work_dir / "teams" / (team_id or "lead") / ident
        workspace.mkdir(parents=True)
        agent = {"id": ident, "role": role, "team_id": team_id, "supervisor": supervisor,
                 "reasoning_effort": self.config.role_reasoning_efforts.get(role, self.config.reasoning_effort),
                 "workspace": str(workspace), "history": [], "summary": "", "note": "",
                 "focus": assignment, "focus_revision": 1, "epoch": 0, "turn_count": 0,
                 "state": "ready", "wake_at": 0., "retry_at": 0., "failures": 0}
        self.agents[ident] = agent
        self.store.put("agent", ident, agent)
        self.store.send(ident, supervisor or "runtime", assignment)
        return agent

    def notify(self, recipient, text, sender="runtime"):
        if recipient in self.agents and self.agents[recipient]["state"] != "closed":
            self.store.send(recipient, sender, text)

    def job_view(self, job, tail_bytes=4000):
        facts = {k: v for k, v in job.items() if k != "token"}
        facts["job_id"] = job["id"]
        facts["elapsed_seconds"] = round(job.get("finished", time.time()) - job.get("started", job["submitted"]), 2)
        facts["logs"] = {name: str(Path(job["folder"]) / (name + ".log")) for name in ("stdout", "stderr")}
        for name, log_path in facts["logs"].items():
            path = Path(log_path)
            if path.is_file():
                with path.open("rb") as stream:
                    stream.seek(max(0, path.stat().st_size - tail_bytes))
                    facts[name + "_tail"] = stream.read().decode("utf-8", errors="replace")
        return facts

    async def job_finished(self, job):
        facts = self.job_view(job)
        message = "Python job completed. Interpret the evidence and report the useful conclusion. " + dumps(facts)
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
                self.notify(owner["id"], "Experiment code changed the published submission; the reviewed checkpoint was restored. Write prediction artifacts only in JOB_DIR and register that path.")
                self.store.event("publication_restored", {"job_id": job["id"]})
        if job["state"] in ("memory_exceeded", "timed_out"):
            self.notify(self.root["id"], message)
        self.store.event("job_completed", {"id": job["id"], "state": job["state"]})

    def team_board(self, team_id, full=False):
        team = self.store.get("team", team_id)
        jobs = self.store.all("job")
        own_jobs = [j for j in jobs if j.get("metadata", {}).get("team_id") == team_id
                    or self.agents.get(j["owner"], {}).get("team_id") == team_id]
        findings = [f for f in self.store.all("finding") if f["team_id"] == team_id
                    or f["id"] in team.get("shared_findings", [])]
        board = {"charter": team, "members": [{k: a.get(k) for k in (
                    ("id", "role", "state", "focus", "focus_revision", "note", "last_archive") if full else
                    ("id", "role", "state", "focus", "focus_revision"))}
                    for a in self.agents.values() if a["team_id"] == team_id],
                 "jobs": [{k: j.get(k) for k in ("id", "owner", "state", "kind", "metadata", "folder")}
                          for j in own_jobs if full or j["state"] not in TERMINAL],
                 "findings": findings if full else findings[-8:], "finding_count": len(findings),
                 "research_progress": self.ledger.signals(team_id),
                 "timing": self.timing.view(team),
                 "journal": [j for j in self.store.all("team_journal") if j["team_id"] == team_id][-3:],
                 "recall": "Use recall(topic=finding/job/messages) or inspect_team for detail"}
        if full:
            board["recent_messages"] = self.store.recent_mail(team["members"])
        return board

    def memory_facts(self, agent):
        return {"focus": agent.get("focus"), "personal_note": agent.get("note"),
                "global_note": self.root.get("note"),
                "team_board": self.team_board(agent["team_id"]) if agent["team_id"] else None,
                "published": self.published}

    def state(self, agent):
        team = self.store.get("team", agent["team_id"]) if agent["team_id"] else None
        all_candidates = self.store.all("candidate")
        visible = [c for c in all_candidates if agent["role"] == "tech_lead" or c["team_id"] == agent["team_id"]]
        pending = [c for c in visible if "reviewer" not in c][-8:]
        recent = visible[-5:]
        selected = {c["id"]: c for c in pending + recent}
        jobs = self.store.all("job")
        return {**self.config.public(), "agent_id": agent["id"], "role": agent["role"],
                "supervisor": agent["supervisor"], "workspace": agent["workspace"],
                "reasoning_effort": agent.get("reasoning_effort", self.config.role_reasoning_efforts.get(agent["role"], self.config.reasoning_effort)),
                "api_budget": self.store.all("budget"),
                "task_path": str(self.config.task_path), "task_root": str(self.config.task_path.parent),
                "original_task": self.task_text, "allowed_stack": STACK,
                "team": {k: team[k] for k in ("id", "revision", "state")} if team else None,
                "personal_note": agent["note"],
                "current_focus": agent.get("focus"), "focus_revision": agent.get("focus_revision"),
                "context": agent.get("context_status", {}),
                "evaluation_agreement": self.ledger.active(),
                "research_progress": self.ledger.signals(agent["team_id"]) if agent["role"] in ("tech_lead", "team_lead") else None,
                "last_context_archive": agent.get("last_archive"),
                "team_board": self.team_board(agent["team_id"]) if agent["team_id"] else None,
                "global_note": self.root["note"],
                "portfolio_plan": self.store.all("portfolio"),
                "time_facts": {"now_wall": time.time(), "global_deadline_wall": self.config.deadline_wall,
                               "elapsed_seconds": round(self.config.seconds - self.config.remaining, 1),
                               "job_timeouts_are_per_job_and_editable": True},
                "team_capacity": {"active_agent_slots_free": self.config.max_agents - sum(a["state"] != "closed" for a in self.agents.values()),
                                  "agents_per_new_team": 4,
                                  "full_team_slots_free": (self.config.max_agents - sum(a["state"] != "closed" for a in self.agents.values())) // 4,
                                  "max_active_agents": self.config.max_agents,
                                  "concurrent_model_requests": self.config.llm_concurrency},
                "teams": [{**t, "timing": self.timing.view(t)} for t in self.store.all("team") if t["state"] in ("active", "draining")]
                         if agent["role"] == "tech_lead" else [],
                "other_directions": [{k: t.get(k) for k in ("id", "direction", "scope", "out_of_scope", "state")}
                    for t in self.store.all("team") if t["state"] in ("active", "draining") and t["id"] != agent["team_id"]],
                "agents": [{k: a[k] for k in ("id", "role", "team_id", "state")}
                           for a in self.agents.values()
                           if a["state"] != "closed" and (agent["role"] == "tech_lead" or a["team_id"] == agent["team_id"])],
                "active_jobs": [{k: j.get(k) for k in ("id", "owner", "state", "kind", "cpus", "ram_mb", "metadata")}
                                for j in jobs if j["state"] not in TERMINAL],
                "candidates": [{k: c.get(k) for k in ("id", "team_id", "approved", "reviewer", "job_id", "submission_path",
                                                      "declared_protocol_id", "review_protocol_id", "comparable")}
                               for c in selected.values()], "candidate_count": len(visible), "published": self.published,
                "registry": "Use recall(topic, record_id), inspect_candidate or inspect_team for evidence",
                "resources_in_use": {"cpus": self.jobs.used_cpu, "ram_requested_mb": self.jobs.used_ram}}

    async def run(self):
        try:
            await self.jobs.recover({a["id"] for a in self.agents.values() if a["state"] != "closed"})
            while self.config.remaining > max(.2, self.config.shutdown_seconds) and not self.done:
                self.monitor.update()
                if time.monotonic() - self.last_checkpoint >= 2:
                    self.run_meta = checkpoint(self.config, self.store, self.run_meta, "running")
                    self.last_checkpoint = time.monotonic()
                if self.config.finalizing and not self.phase_announced:
                    self.phase_announced = True
                    for agent in self.agents.values():
                        self.notify(agent["id"], "Finalization boundary reached. Concentrate on verified output. "
                                    "Running jobs may finish; no new research jobs. Tech lead can adjust the "
                                    "plan with plan_run or delegate local timing to team leads; cancel obsolete work explicitly.")
                    if not self.published:
                        approved = [c for c in self.store.all("candidate") if c.get("approved")]
                        if approved:
                            self.publish(approved[-1]["id"])
                now = time.monotonic()
                self.timing.tick()
                await self.advance_teams()
                if self.api_blocked and not self.turns and all(j["state"] in TERMINAL for j in self.store.all("job")):
                    self.done = True
                    continue
                if self.root["state"] == "blocked" and not self.turns and all(j["state"] in TERMINAL for j in self.store.all("job")):
                    productive = any(a["state"] not in ("closed", "blocked") and
                                     (a["state"] == "ready" or self.store.pending(a["id"]))
                                     for a in self.agents.values())
                    if not productive:
                        self.store.event("control_plane_blocked", {"agent": self.root["id"]})
                        self.done = True
                        continue
                for ident, agent in list(self.agents.items()):
                    if self.api_blocked or agent["state"] in ("closed", "blocked") or ident in self.turns or now < agent["retry_at"]:
                        continue
                    team_draining = bool(agent["team_id"] and self.store.get("team", agent["team_id"])["state"] == "draining")
                    if agent["state"] == "ready" or self.store.pending(ident) or (not team_draining and agent["wake_at"] <= now):
                        if agent["state"] == "waiting" and not self.store.pending(ident):
                            self.notify(ident, "Your chosen review timer has elapsed; current facts are in your state.")
                        self.turns[ident] = asyncio.create_task(self.turn(agent))
                for ident, task in list(self.turns.items()):
                    if task.done():
                        try:
                            task.result()
                        except asyncio.CancelledError:
                            pass
                        except Exception as error:
                            self.store.event("driver_error", {"agent": ident, "error_type": type(error).__name__})
                            self.agents[ident]["retry_at"] = time.monotonic() + 3
                        del self.turns[ident]
                await asyncio.sleep(min(.1, self.config.remaining))
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
                elif (not Path(self.published["path"]).is_file()
                      or digest(self.published["path"]) != self.published["sha256"]):
                    self.publish(self.published["candidate_id"])
                self.store.event("shutdown", {"published": self.published,
                                             "remaining_seconds": self.config.remaining})
                status = "expired" if self.config.remaining <= max(.2, self.config.shutdown_seconds) else "completed" if self.done and self.published else "stopped"
                self.run_meta = checkpoint(self.config, self.store, self.run_meta, status)
                self.store.sync_events()
                self.monitor.update(force=True)
            finally:
                await self.client.close()
        return self.published

    async def turn(self, agent):
        actor_token = request_actor.set(agent["id"])
        effort_token = request_effort.set(agent.get("reasoning_effort", self.config.role_reasoning_efforts.get(agent["role"], self.config.reasoning_effort)))
        self.store.take_mail(agent)
        agent["state"] = "running"
        self.store.put("agent", agent["id"], agent)
        try:
            tools = tools_for(agent["role"])
            messages = await prepare_context(agent, self.state(agent), self.client, self.config, self.store, tools)
            try:
                message, usage = await self.client.complete(messages, tools)
            except Exception as error:
                if not is_context_error(error):
                    raise
                await compact(agent, self.client, self.store, facts=self.memory_facts(agent), aggressive=True)
                message, usage = await self.client.complete(build_context(agent, self.state(agent)), tools)
            agent["failures"] = 0
            agent["turn_count"] += 1
            agent["history"].append(message)
            self.store.put("agent", agent["id"], agent)
            self.store.event("model_usage", {"agent": agent["id"], "usage": usage})
            agent["last_actual_prompt_tokens"] = usage.get("prompt_tokens")
            calls = message.get("tool_calls", [])
            agent["wait_requested"] = False
            for call in calls:
                receipt_id = f"{agent.get('epoch', 0)}:{call['id']}"
                cached = self.store.receipt(agent["id"], receipt_id)
                if cached is None:
                    try:
                        agent["activity"] = "инструмент: " + call["function"]["name"]
                        self.store.event("tool_started", {"agent": agent["id"], "call_id": call["id"],
                                         "tool": call["function"]["name"], "arguments": normalize_arguments(call["function"]["arguments"])})
                        result = await self.dispatch(agent, call["function"]["name"],
                                                     normalize_arguments(call["function"]["arguments"]))
                    except Exception as error:
                        result = {"error": f"{type(error).__name__}: {error}"}
                    self.store.save_receipt(agent["id"], receipt_id, result)
                    self.store.event("tool_finished", {"agent": agent["id"], "call_id": call["id"],
                                                       "tool": call["function"]["name"], "result": result})
                else:
                    result = cached
                agent["history"].append({"role": "tool", "tool_call_id": call["id"], "content": dumps(result)})
                self.store.put("agent", agent["id"], agent)
            if agent.pop("compact_requested", False):
                try:
                    await compact(agent, self.client, self.store, facts=self.memory_facts(agent))
                except Exception as error:
                    self.notify(agent["id"], "Context compaction failed: " + type(error).__name__)
            if agent["state"] != "closed":
                if not calls:
                    if message.get("content") and agent["supervisor"]:
                        self.notify(agent["supervisor"], message["content"], agent["id"])
                        self.journal_message(agent, message["content"], "phase_update", [])
                    agent["wake_at"] = self.config.started + self.config.seconds
                agent["state"] = "waiting" if not calls or agent["wait_requested"] else "ready"
                agent["activity"] = "ожидание события" if agent["state"] == "waiting" else "следующий шаг"
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
            self.store.event("model_error", {"agent": agent["id"], "error_type": type(error).__name__,
                                              "category": failure.category, "detail": detail})
            if response_failure:
                folder = self.config.work_dir / "transcripts" / agent["id"]
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / (uid("rejected-response") + ".json")
                path.write_text(dumps({"raw": error.raw, "category": error.category}), encoding="utf-8")
                self.store.event("model_usage", {"agent": agent["id"], "purpose": "rejected response", "usage": error.usage})
                self.notify(agent["id"], detail + f" Full rejected response is at {path}. No tool calls from it ran.")
            elif failure.category == "transport":
                self.notify(agent["id"], "The model request failed before a complete response. No tool calls from that response ran. "
                            "For a timeout, make the next action smaller (write/append a short module or one decision) instead of repeating a large generation. "
                            f"Request timeout is {self.config.request_timeout}s; the global deadline is unchanged. Error: " + (detail or type(error).__name__))
            if not failure.retryable and failure.shared:
                self.api_blocked = True
                print("Model API stopped: " + detail, flush=True)
            if blocked or agent["failures"] == 3:
                recipient = agent["supervisor"] or self.root["id"]
                self.notify(recipient, f"Session {agent['id']} needs attention: {failure.category}. "
                            "Inspect its board/archive; restart_agent can create a fresh episode.")
        finally:
            self.complete_interrupted_calls(agent)
            refresh_context_status(agent, self.state(agent), self.client, tools_for(agent["role"]))
            self.store.put("agent", agent["id"], agent)
            request_actor.reset(actor_token)
            request_effort.reset(effort_token)

    def complete_interrupted_calls(self, agent):
        pending = {}
        for message in agent["history"]:
            for call in message.get("tool_calls", []):
                pending[call["id"]] = call
            if message["role"] == "tool":
                pending.pop(message["tool_call_id"], None)
        for call_id in pending:
            known = self.store.receipt(agent["id"], f"{agent.get('epoch', 0)}:{call_id}")
            result = known if known is not None else {
                "error": "Turn interrupted; execution outcome is not established. Inspect jobs and artifacts before repeating.",
                "execution_status": "unknown"}
            agent["history"].append({"role": "tool", "tool_call_id": call_id, "content": dumps(result)})

    def readable(self, value):
        path = Path(value)
        if not path.is_absolute():
            path = self.config.task_path.parent / path
        path = path.resolve()
        roots = (self.config.task_path.parent, self.config.work_dir)
        if not any(path.is_relative_to(root) for root in roots):
            raise ValueError("Read paths must be inside the task or run directory")
        return path

    def writable(self, agent, value):
        path = Path(value)
        own = Path(agent["workspace"])
        path = (own / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_relative_to(own):
            raise ValueError("Write inside your workspace; share new artifact paths instead of overwriting colleagues")
        return path

    async def dispatch(self, agent, name, args):
        route, resolved = resolve_call(agent["role"], name, args)
        return await self._dispatch_backend(agent, route, resolved)

    def journal_message(self, agent, text, kind, references, message_id=None, next_action=None):
        if not agent["team_id"] or kind == "assignment":
            return None
        ident = uid("journal")
        record = {"id": ident, "team_id": agent["team_id"], "author": agent["id"], "kind": kind,
                  "claim_status": "reported", "text": text, "references": references,
                  "message_id": message_id, "next_action": next_action, "created": time.time()}
        self.store.put("team_journal", ident, record)
        return record

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
                    matches = [a["id"] for a in self.agents.values() if a["role"] == recipient and a["state"] != "closed"
                               and (recipient == "tech_lead" or agent["team_id"] is None or a["team_id"] == agent["team_id"])]
                    if len(matches) != 1:
                        raise ValueError("Recipient role is ambiguous/unavailable; use an agent ID from " + dumps(matches))
                    recipient = matches[0]
                else:
                    raise ValueError("Unknown recipient; use an agent ID, team ID (routes to its lead), supervisor, or a unique role")
            args = {**args, "to": recipient}
            target = self.agents[args["to"]]
            if target["state"] == "closed":
                raise ValueError("Recipient is closed; choose an active team/agent")
            if args.get("kind") == "assignment":
                if agent["role"] != "tech_lead" and target["supervisor"] != agent["id"]:
                    raise ValueError("Assign work through the recipient's lead; ordinary collaboration messages are unrestricted")
                target["focus"] = args["text"]
                if target["team_id"]:
                    target["focus_revision"] = self.store.get("team", target["team_id"])["revision"]
                self.store.put("agent", target["id"], target)
            seq = self.store.send(args["to"], agent["id"], args["text"])
            kind = args.get("kind", "message")
            self.journal_message(agent, args["text"], kind, args.get("references", []), seq, args.get("next_action"))
            if kind in ("finding", "blocker"):
                target["phase_pending"] = True
                self.store.put("agent", target["id"], target)
            if kind == "blocker":
                if args.get("candidate_id") and agent["role"] == "evaluator":
                    self.manage_candidate(agent, {"action": "flag", "candidate_id": args["candidate_id"],
                                                  "reason": args["text"], "references": args.get("references", [])})
                if agent["team_id"] and not (args.get("candidate_id") and agent["role"] == "evaluator"):
                    team = self.store.get("team", agent["team_id"])
                    for lead in {team["lead"], self.root["id"]} - {args["to"]}:
                        self.notify(lead, "BLOCKER from " + agent["id"] + ": " + args["text"], agent["id"])
            return {"message_id": seq, "recipient": recipient}
        if name == "wait":
            seconds = max(.5, float(args.get("seconds", self.config.remaining)))
            agent["wait_requested"] = True
            agent["wake_at"] = time.monotonic() + min(seconds, self.config.remaining)
            return {"wake_in_seconds": seconds, "message_events": True}
        if name == "finish":
            if not self.published:
                raise ValueError("Publish an approved candidate before finishing")
            if any(t["state"] in ("active", "draining") for t in self.store.all("team")):
                raise ValueError("Research teams are still active. Consult leads, resolve candidate caveats, consider reassigning/opening a feasible direction, then collect handovers and close teams before finish. One job's timeout does not exhaust the run budget.")
            if any(j["state"] not in TERMINAL for j in self.store.all("job")):
                raise ValueError("Resolve outstanding jobs before ending the run")
            if not args["reason"].strip():
                raise ValueError("Explain the evidence for stopping with unused time")
            self.store.put("stop_decision", "current", {"reason": args["reason"], "remaining_seconds": self.config.remaining, "created": time.time()})
            self.done = True
            return {"finished": True, "published": self.published}
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
                raise ValueError("Managers do not write implementation files. Assign a specialist; use write_note for coordination.")
            path = self.writable(agent, args["path"]) if action in ("write", "append", "replace") else self.readable(args["path"])
            if action == "replace":
                original = path.read_text(encoding="utf-8")
                old = args["old_text"]
                expected = int(args.get("expected_matches", 1))
                matches = original.count(old) if old else 0
                if not old or expected < 1 or matches != expected:
                    raise ValueError(f"Literal edit expected {expected} matches, found {matches}; read the actual file before editing")
                path.write_text(original.replace(old, args["new_text"]), encoding="utf-8")
                return {"path": str(path), "replacements": matches, "bytes": path.stat().st_size}
            if action in ("write", "append"):
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a" if action == "append" else "w", encoding="utf-8") as out:
                    out.write(args["content"])
                return {"path": str(path), "bytes": path.stat().st_size}
            if action == "list":
                return {"path": str(path), "entries": [{"name": p.name, "directory": p.is_dir()}
                        for p in sorted(path.iterdir()) if p.name != ".env"]}
            if path.name == ".env":
                raise ValueError("Credentials are held by the runtime and must not enter model context")
            start = max(0, args.get("start_line", 1) - 1)
            count = max(1, args.get("lines", 200))
            with path.open(encoding="utf-8") as stream:
                lines = list(itertools.islice(stream, start, start + count))
                more = next(stream, None) is not None
            return {"path": str(path), "start_line": start + 1, "has_more": more,
                    "text": "".join(lines)}
        if name == "python_job":
            action = args["action"]
            if action != "run":
                job = self.store.get("job", args["job_id"])
                owner = self.agents[job["owner"]]
                if agent["role"] != "tech_lead" and owner["team_id"] != agent["team_id"]:
                    raise ValueError("Job belongs to another team")
                if action == "cancel":
                    return self.jobs.cancel(job["id"])
                return self.job_view(job, tail_bytes=16000)
            if agent["role"] in MANAGERS:
                raise ValueError("Managers cannot execute Python. Delegate to ML/data engineer or evaluator.")
            if "code" in args:
                path = self.writable(agent, uid("script") + ".py")
                path.write_text(args["code"], encoding="utf-8")
            else:
                path = self.writable(agent, args["path"])
            if path.suffix != ".py" or not path.is_file():
                raise ValueError("Provide an existing .py path or code")
            team = self.store.get("team", agent["team_id"]) if agent["team_id"] else None
            default_kind = "evaluation" if agent["role"] == "evaluator" else (
                "finalization" if agent["role"] == "ml_engineer" and team and team.get("purpose") == "delivery" and not self.published else "research")
            kind = args.get("kind", default_kind)
            if team and team["state"] != "active" and kind == "research":
                raise ValueError("Team is draining: preserve results and verify/finalize existing work")
            if team and kind == "research":
                timing = self.timing.view(team)
                if timing["allocated_remaining_seconds"] <= 0 or timing["phase"] == "finalization":
                    raise ValueError("Team's manager-chosen time boundary reached. Ask your lead to replan, or run evaluation/finalization work.")
            job = self.jobs.submit(agent["id"], path, args.get("args", []),
                                   cpus=int(args.get("cpus", 2)), ram_mb=int(args.get("ram_mb", 4096)),
                                   timeout=float(args.get("timeout", 600)), kind=kind,
                                   source_dir=Path(agent["workspace"]), metadata={
                                       "team_id": agent["team_id"], "assignment_revision": agent.get("focus_revision"),
                                       "team_revision": team["revision"] if team else None,
                                       "protocol_id": self.ledger.active()["id"] if self.ledger.active() else None,
                                       "purpose": args.get("purpose", "current assignment")})
            inline_wait = min(30., max(0., float(args.get("wait_for_seconds", 10))),
                              max(0., self.config.remaining - self.config.shutdown_seconds))
            if inline_wait:
                self.inline_jobs.add(job["id"])
                try:
                    try:
                        await asyncio.wait_for(asyncio.shield(self.jobs.tasks[job["id"]]), inline_wait)
                    except asyncio.TimeoutError:
                        pass
                    current = self.store.get("job", job["id"])
                    if current["state"] in TERMINAL:
                        self.store.event("job_result_inline", {"job_id": job["id"], "agent": agent["id"]})
                        return {**self.job_view(current), "completion_in_tool_result": True}
                finally:
                    self.inline_jobs.discard(job["id"])
            current = self.store.get("job", job["id"])
            return {"job_id": job["id"], "folder": job["folder"], "state": current["state"], "script": job["script"], "source_path": job["source_path"],
                    "stdout_path": str(Path(job["folder"]) / "stdout.log"), "stderr_path": str(Path(job["folder"]) / "stderr.log"),
                    "completion_wakes_owner": True, "next_action": "Continue other useful work or wait() for completion; avoid polling."}
        if name == "memory":
            return self.manage_memory(agent, args)
        if name == "candidate":
            return self.manage_candidate(agent, args)
        raise ValueError("Unknown tool")

    def manage_memory(self, agent, args):
        action, scope = args["action"], args.get("scope", "personal")
        if action == "compact":
            agent["compact_requested"] = True
            return {"scheduled": "after complete tool results are recorded"}
        if action == "recall":
            topic = args["topic"]
            offset, limit = max(0, int(args.get("offset", 0))), max(1, int(args.get("limit", 20)))
            if topic == "messages":
                members = self.store.get("team", agent["team_id"])["members"] if agent["team_id"] else list(self.agents)
                records = self.store.recent_mail(members, offset + limit)
                return records[max(0, len(records) - offset - limit):len(records) - offset or None]
            if topic == "archives":
                records = [{"kind": k, **json.loads(data)} for k, data in self.store.db.execute(
                    "SELECT kind,data FROM events WHERE kind='context_archive' ORDER BY seq")]
                if agent["role"] != "tech_lead":
                    records = [r for r in records if self.agents.get(r["agent"], {}).get("team_id") == agent["team_id"]]
            elif args.get("record_id"):
                return self.store.get(topic, args["record_id"])
            else:
                records = self.store.all(topic)
            return {"total": len(records), "offset": offset, "records": records[offset:offset + limit]}
        if action == "publish":
            if not agent["team_id"]:
                raise ValueError("Publish team findings from a research team; use the global note for overall strategy")
            team = self.store.get("team", agent["team_id"])
            ident = uid("finding")
            record = {"id": ident, "team_id": team["id"], "team_revision": team["revision"],
                      "author": agent["id"], "title": args.get("title", "Research finding"),
                      "text": args["text"], "references": args.get("references", []), "created": time.time()}
            record["claim_status"] = args.get("claim_status", "reported")
            snapshots = []
            for reference in record["references"]:
                try:
                    source = self.readable(reference)
                except (ValueError, OSError):
                    continue
                if source.is_file() and source.is_relative_to(self.config.work_dir / "teams"):
                    target = self.config.work_dir / "shared" / ident / f"{len(snapshots)}-{source.name}"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    snapshots.append({"original": str(source), "path": str(target), "sha256": digest(target)})
            record["reference_snapshots"] = snapshots
            self.store.put("finding", ident, record)
            if args.get("share"):
                self.notify(self.root["id"], "Potentially reusable finding; consider sharing with another direction: " + dumps(record), agent["id"])
            return record
        if action == "assess":
            record = self.store.get("finding", args["finding_id"])
            if agent["role"] != "evaluator" or record["team_id"] != agent["team_id"]:
                raise ValueError("The originating team's evaluator qualifies its findings")
            record.setdefault("assessments", []).append({"author": agent["id"], "status": args["status"],
                "text": args["text"], "references": args["references"], "created": time.time()})
            record["claim_status"] = args["status"]
            self.store.put("finding", record["id"], record)
            team = self.store.get("team", record["team_id"])
            for lead in (team["lead"], self.root["id"]):
                self.notify(lead, "Finding assessment changed: " + dumps(record), agent["id"])
            return record
        if action == "share":
            if agent["role"] != "tech_lead":
                raise ValueError("Flag a reusable finding with publish(share=true); tech lead coordinates cross-team reuse")
            finding = self.store.get("finding", args["finding_id"])
            target = self.store.get("team", args["team_id"])
            if target["state"] != "active":
                raise ValueError("Share with an active research team")
            ids = target.setdefault("shared_findings", [])
            if finding["id"] not in ids:
                ids.append(finding["id"])
            self.store.put("team", target["id"], target)
            self.notify(target["lead"], "Tech lead shared original evidence from another direction. "
                        + dumps({"finding": finding, "guidance": args.get("guidance", "Assess applicability within your charter")}), agent["id"])
            self.store.event("finding_shared", {"finding_id": finding["id"], "to_team": target["id"]})
            return {"finding_id": finding["id"], "to_team": target["id"]}
        if scope == "team":
            if not agent["team_id"]:
                raise ValueError("No team")
            team = self.store.get("team", agent["team_id"])
            if action == "write":
                if agent["id"] != team["lead"] and agent["role"] != "tech_lead":
                    raise ValueError("Publish an additive finding; only the lead edits the team synthesis")
                team["note"] = args["text"]
                self.store.put("team", team["id"], team)
            elif action != "read":
                raise ValueError("Unknown memory action")
            return self.team_board(team["id"], full=True)
        owner = self.root if scope == "global" else agent
        if action == "write":
            if scope == "global" and agent["role"] != "tech_lead":
                raise ValueError("Send global findings to tech lead")
            owner["note"] = args["text"]
            self.store.put("agent", owner["id"], owner)
        elif action != "read":
            raise ValueError("Unknown memory action")
        return {"note": owner["note"]}

    async def retire_team(self, team, archived=False, reason="completed"):
        team["state"] = "archived" if archived else "closed"
        team["closing_reason"] = reason
        self.store.put("team", team["id"], team)
        cancelled = []
        for member in team["members"]:
            self.agents[member]["state"] = "closed"
            self.store.put("agent", member, self.agents[member])
            task = self.turns.get(member)
            if task and not task.done():
                task.cancel()
                cancelled.append(task)
        await asyncio.gather(*cancelled, return_exceptions=True)
        own_jobs = [j for j in self.store.all("job") if j["owner"] in team["members"] and j["state"] not in TERMINAL]
        for job in own_jobs:
            self.jobs.cancel(job["id"])
        await asyncio.gather(*(self.jobs.tasks[j["id"]] for j in own_jobs), return_exceptions=True)
        archives = {}
        for member in team["members"]:
            a = self.agents[member]
            self.complete_interrupted_calls(a)
            archives[member] = archive_context(a, self.store, "team " + team["state"])
            a["last_archive"] = archives[member]
            a["history"], a["summary"] = [], ""
            self.store.put("agent", member, a)
        team["handover"] = {"member_archives": archives,
                            "finding_ids": [f["id"] for f in self.store.all("finding") if f["team_id"] == team["id"]],
                            "candidate_ids": [c["id"] for c in self.store.all("candidate") if c["team_id"] == team["id"]]}
        self.store.put("team", team["id"], team)
        self.store.event("team_retired", {"team_id": team["id"], "state": team["state"], "reason": reason})
        self.notify(self.root["id"], f"Team {team['id']} is {team['state']}. Handover and evidence remain available through team inspect.")
        return team

    async def advance_teams(self):
        for team in self.store.all("team"):
            if team["state"] != "draining":
                continue
            active_jobs = any(j["owner"] in team["members"] and j["state"] not in TERMINAL for j in self.store.all("job"))
            active_turns = any(m in self.turns and not self.turns[m].done() for m in team["members"])
            pending = any(self.store.pending(m) for m in team["members"] if self.agents[m]["state"] != "closed")
            ready = any(self.agents[m]["state"] == "ready" for m in team["members"])
            expired = time.monotonic() >= team["drain_until"]
            if expired or not (active_jobs or active_turns or pending or ready):
                await self.retire_team(team, team.get("archive_after_close", False), team.get("closing_reason", "completed"))

    async def manage_team(self, agent, args):
        action = args["action"]
        if action == "create":
            if agent["role"] != "tech_lead":
                raise ValueError("Only tech lead creates teams")
            if sum(a["state"] != "closed" for a in self.agents.values()) + 4 > self.config.max_agents:
                raise ValueError("Need room for lead, ML engineer, data engineer and evaluator")
            ident = uid("team")
            team = {"id": ident, "direction": args["direction"], "goal": args["goal"],
                    "revision": 1, "state": "active", "note": "", "members": [],
                    "scope": args.get("scope", args["direction"]), "out_of_scope": args.get("out_of_scope", ""),
                    "deliverables": args.get("deliverables", []), "shared_findings": [],
                    "allocation_deadline_wall": time.time() + min(self.config.remaining, max(0., float(args.get("budget_seconds", self.config.remaining)))),
                    "time_plan": {}, "time_alerts": {},
                    "resources": args.get("resources", {})}
            first_delivery = not self.published and not any(t.get("purpose") == "delivery" and t["state"] in ("active", "draining") for t in self.store.all("team"))
            team["purpose"] = args.get("purpose", "delivery" if first_delivery else "research")
            milestone = ("CURRENT FIRST MILESTONE: deliver one modest genuine fit/assessment/inference artifact before any sweep or extensive CV. "
                         "One configuration and the simplest honest task-compatible assessment, then register and independently review. "
                         "Research the broader charter after that checkpoint. " if team["purpose"] == "delivery" else
                         "Investigate this independent research question using shared data/evaluation evidence; avoid duplicating the delivery team's pipeline. ")
            leader = self.new_agent("team_lead", ident, self.root["id"], milestone + args["goal"])
            evaluator = self.new_agent("evaluator", ident, leader["id"],
                                       "You are this team's evaluator. Familiarize yourself with the task, "
                                       "coordinate evaluation with the lead, then wait for concrete candidates.")
            ml = self.new_agent("ml_engineer", ident, leader["id"],
                                milestone + "You own implementation and candidate registration within this charter: " + args["goal"]
                                + ". Coordinate with the lead, data engineer and evaluator; do not wait for exhaustive profiling before a safe baseline.")
            data = self.new_agent("data_engineer", ident, leader["id"],
                                  "Investigate schema, fit/transform boundaries and useful data opportunities for: " + args["goal"]
                                  + ". Supply evidence and usable code to ML engineer; support the first delivery in parallel.")
            team.update(lead=leader["id"], evaluator=evaluator["id"], ml_engineer=ml["id"], data_engineer=data["id"],
                        members=[leader["id"], ml["id"], data["id"], evaluator["id"]])
            self.store.put("team", ident, team)
            self.store.event("team_created", team)
            return team
        team = self.store.get("team", args["team_id"])
        if agent["role"] != "tech_lead" and team["lead"] != agent["id"]:
            raise ValueError("Manage your own team")
        if action == "inspect":
            return self.team_board(team["id"], full=True)
        if action in ("close", "delete"):
            if agent["role"] != "tech_lead":
                raise ValueError("Tech lead closes or deletes teams")
            if team["state"] in ("closed", "archived"):
                if action == "delete":
                    team["state"] = "archived"
                    self.store.put("team", team["id"], team)
                return team
            reason = args.get("reason", "direction complete or no longer prioritized")
            if args.get("mode", "graceful") == "immediate":
                return await self.retire_team(team, action == "delete", reason)
            team.update(state="draining", closing_reason=reason, archive_after_close=action == "delete",
                        drain_until=time.monotonic() + min(self.config.remaining, max(0., float(args.get("grace_seconds", self.config.remaining)))))
            team["drain_deadline_wall"] = time.time() + max(0., team["drain_until"] - time.monotonic())
            self.store.put("team", team["id"], team)
            for member in team["members"]:
                self.notify(member, "Team is winding down. Start no new research. Preserve useful findings, "
                            "finish or cancel current jobs, register/check ready candidates, send a concise "
                            "handover to the lead, then wait. Reason: " + reason, agent["id"])
            return team
        if action == "restart_agent":
            target = self.agents[args["agent_id"]]
            if target["team_id"] != team["id"] or target["id"] == agent["id"]:
                raise ValueError("Restart a subordinate in this team, not the caller")
            if target["state"] == "closed" or team["state"] not in ("active", "draining"):
                raise ValueError("Closed teams are archived; create a fresh team and reference its findings")
            task = self.turns.get(target["id"])
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            self.complete_interrupted_calls(target)
            archive = archive_context(target, self.store, "fresh agent episode")
            self.store.retire_mail(target["id"])
            target.update(history=[], summary="", epoch=target.get("epoch", 0) + 1, last_archive=archive,
                          failures=0, retry_at=0., wake_at=0., state="ready",
                          focus=args.get("assignment", target.get("focus", team["goal"])), focus_revision=team["revision"])
            if not args.get("keep_note", True):
                target["note"] = ""
            self.store.put("agent", target["id"], target)
            self.notify(target["id"], "Fresh context episode. Original history: " + archive + ". "
                        "Your notes, team board and existing jobs remain. Reconcile their state before duplicating work. "
                        "Assignment: " + target["focus"], agent["id"])
            return {"agent_id": target["id"], "epoch": target["epoch"], "archive": archive}
        if team["state"] != "active":
            raise ValueError("Team is not active")
        if action == "staff":
            role = args["role"]
            if role not in SPECIALISTS:
                raise ValueError("Role not in specialist catalog")
            if role == "evaluator":
                return {"agent_id": team["evaluator"], "existing": True}
            child = self.new_agent(role, team["id"], team["lead"], args["assignment"])
            child["focus_revision"] = team["revision"]
            self.store.put("agent", child["id"], child)
            team["members"].append(child["id"])
            self.store.put("team", team["id"], team)
            return {"agent_id": child["id"], "workspace": child["workspace"]}
        if agent["role"] != "tech_lead":
            raise ValueError("Only tech lead reassigns or closes teams")
        if action == "reassign":
            team.update(direction=args["direction"], goal=args["goal"], revision=team["revision"] + 1)
            for key in ("resources", "scope", "out_of_scope", "deliverables", "purpose"):
                if key in args:
                    team[key] = args[key]
            if "scope" not in args:
                team["scope"] = args["direction"]
            self.store.put("team", team["id"], team)
            leader = self.agents[team["lead"]]
            leader.update(focus=team["goal"], focus_revision=team["revision"])
            self.store.put("agent", leader["id"], leader)
            for member in team["members"]:
                self.notify(member, "Team charter revised; prior assignments may be superseded. "
                            "Coordinate current experiments with the team lead. " + dumps({k: team[k] for k in (
                                "id", "revision", "goal", "scope", "out_of_scope", "deliverables")}), agent["id"])
            return team
        raise ValueError("Unknown team action")

    def manage_candidate(self, agent, args):
        action = args["action"]
        if action == "list":
            return self.store.all("candidate")
        if action == "inspect":
            return self.store.get("candidate", args["candidate_id"])
        if action == "register":
            if agent["role"] != "ml_engineer":
                raise ValueError("ML engineer owns candidate delivery; managers delegate and evaluator independently reviews")
            job = self.store.get("job", args["job_id"])
            team_id = agent["team_id"]
            if not team_id:
                raise ValueError("Select an existing team_id for independent evaluator routing")
            team = self.store.get("team", team_id)
            if team["state"] not in ("active", "draining"):
                raise ValueError("Route new candidates to an active or draining team's evaluator")
            if self.agents[job["owner"]]["team_id"] != team_id:
                raise ValueError("Candidate must come from your team's job")
            if job["state"] != "succeeded" and not (args.get("allow_partial_source") and job["state"] in TERMINAL):
                raise ValueError("Source job is not successful. To offer an existing partial artifact for strict independent review, set allow_partial_source=true")
            path = self.readable(args["submission_path"])
            if not path.is_file() or not path.stat().st_size:
                raise ValueError("Provide a nonempty output file attributable to the cited job")
            ident = uid("candidate")
            frozen = self.config.work_dir / "candidates" / ident
            frozen.mkdir(parents=True)
            submission = frozen / "submission.csv"
            shutil.copyfile(path, submission)
            candidate = {"id": ident, "team_id": team_id, "author": agent["id"],
                         "team_revision": team["revision"], "job_assignment": job.get("metadata", {}),
                         "job_id": job["id"], "submission_path": str(submission), "sha256": digest(submission),
                         "findings": args.get("findings", {}), "references": args.get("references", []),
                         "declared_protocol_id": args.get("protocol_id", job.get("metadata", {}).get("protocol_id")),
                         "source_job_state": job["state"], "review_state": "pending",
                         "approved": False, "created": time.time()}
            self.store.put("candidate", ident, candidate)
            self.notify(team["evaluator"], "Candidate ready for your independent verification: " + dumps(candidate), agent["id"])
            self.notify(team["lead"], "Candidate registered and handed to evaluator: " + ident, agent["id"])
            return candidate
        candidate = self.store.get("candidate", args["candidate_id"])
        if action == "flag":
            if agent["role"] != "evaluator" or candidate["team_id"] != agent["team_id"]:
                raise ValueError("The candidate team's evaluator flags correctness blockers")
            candidate.setdefault("blockers", []).append({"reason": args["reason"], "references": args.get("references", []), "created": time.time()})
            candidate.update(approved=False, review_state="blocked", reviewer=agent["id"], conclusion=args["reason"])
            self.store.put("candidate", candidate["id"], candidate)
            self.withdraw_candidate(candidate)
            team = self.store.get("team", candidate["team_id"])
            for lead in (team["lead"], self.root["id"]):
                self.notify(lead, "BLOCKED candidate " + candidate["id"] + ": " + args["reason"]
                            + ". Evidence: " + dumps(args.get("references", [])), agent["id"])
            self.journal_message(agent, args["reason"], "blocker", args.get("references", []))
            self.store.event("candidate_blocked", {"candidate_id": candidate["id"], "reason": args["reason"]})
            return candidate
        if action == "review":
            if agent["role"] != "evaluator" or candidate["team_id"] != agent["team_id"]:
                raise ValueError("Only the candidate team's evaluator can record its review")
            verification = self.store.get("job", args["job_id"])
            if verification["owner"] != agent["id"] or verification["state"] != "succeeded":
                raise ValueError("Run your own verification Python successfully before recording a review")
            if verification["submitted"] < candidate["created"]:
                raise ValueError("Verification must run after candidate registration; an earlier profiling job is not its review")
            if not args.get("conclusion", "").strip():
                raise ValueError("Provide your evidence-based conclusion")
            if digest(candidate["submission_path"]) != candidate["sha256"]:
                raise ValueError("Candidate bytes changed since registration")
            candidate.update(approved=bool(args["approved"]), reviewer=agent["id"],
                             review_state="approved" if args["approved"] else "rejected",
                             verification_job=verification["id"], conclusion=args["conclusion"],
                             review_findings=args.get("findings", {}))
            protocol_id = args.get("protocol_id")
            if protocol_id:
                self.store.get("protocol", protocol_id)
            candidate.update(review_protocol_id=protocol_id,
                             comparable=bool(protocol_id and args.get("comparable", False)))
            self.store.put("candidate", candidate["id"], candidate)
            if not candidate["approved"]:
                self.withdraw_candidate(candidate)
            team = self.store.get("team", agent["team_id"])
            for recipient in (team["lead"], self.root["id"]):
                self.notify(recipient, "Evaluator verdict: " + dumps(candidate), agent["id"])
            # First verified output is a safety checkpoint; tech lead chooses subsequent versions.
            if candidate["approved"] and not self.published:
                self.publish(candidate["id"])
            return candidate
        if action == "publish":
            if agent["role"] != "tech_lead":
                raise ValueError("Tech lead chooses the published result")
            return self.publish(candidate["id"])
        raise ValueError("Unknown candidate action")

    def withdraw_candidate(self, candidate):
        if self.published and self.published["candidate_id"] == candidate["id"]:
            target = Path(self.published["path"])
            if target.is_file() and digest(target) == self.published["sha256"]:
                target.unlink()  # Exact reviewed bytes remain preserved in the immutable candidate.
            self.published = None
            self.store.put("publication", "current", {"revoked_candidate": candidate["id"]})
            alternatives = [c for c in self.store.all("candidate") if c.get("approved")]
            if alternatives:
                self.publish(alternatives[-1]["id"])

    def publish(self, ident):
        candidate = self.store.get("candidate", ident)
        if not candidate.get("approved"):
            raise ValueError("Evaluator approval is required")
        source = Path(candidate["submission_path"])
        if digest(source) != candidate["sha256"]:
            raise ValueError("Candidate changed after review")
        target = self.config.task_path.parent / "submission.csv"
        temporary = target.with_name(".submission-" + uid("publish") + ".tmp")
        try:
            with source.open("rb") as inp, temporary.open("wb") as out:
                shutil.copyfileobj(inp, out)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        self.published = {"candidate_id": ident, "path": str(target), "sha256": candidate["sha256"]}
        self.store.put("publication", "current", self.published)
        self.store.event("published", self.published)
        return self.published
