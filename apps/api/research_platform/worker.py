"""Single-owner research scheduler and PM service. Model access requires explicit opt-in."""

import asyncio
import copy
import json
import hashlib
import os
import shutil
import signal
import time
from datetime import timezone
from pathlib import Path
from uuid import uuid4, uuid5, NAMESPACE_URL

import httpx
from sqlalchemy import select, text
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from research_config import Config
from research_llm import ModelClient, APIUnavailable
from research_context import summarize, blocks
from .agent_graph import build_agent_graph
from .database import (
    DATA_ROOT,
    Directive,
    Event,
    InputFile,
    Message,
    Project,
    ResearchObject,
    ResearchEvent,
    Run,
    Session,
    WorkspaceSettings,
    engine,
    now,
)
from .engine import PlatformRuntime
from .pg_store import PostgresStore
from .secrets import decrypt_key
from .usage import tracked

RUNS_ROOT = Path(os.environ.get("RUNS_ROOT", "/data/runs"))
ENABLED = os.getenv("ENABLE_MODEL_CALLS", "false").lower() == "true"
PM_PROMPT = """You are the project manager of an autonomous research workspace. Speak the user's language.
Answer status questions from observed project state; distinguish measurements, researcher claims and unknowns.
Project metadata may be edited for a future run. The active run's task, inputs and policy are its own snapshot.
Your only research-command recipient is the tech lead. Relay additional user instructions with send_to_tech_lead,
then report delivery honestly; a queued directive is not an accepted or completed change. Never claim you trained,
paused, stopped or verified something merely because the user asked. Runtime control buttons own lifecycle changes.
Use project_status to inspect teams, current run, artifacts and directive decisions. You cannot execute code or assign
specialists directly. Ask a focused question when an instruction is ambiguous. Preserve scope and evidence links.
If no research run exists, help clarify the task and explain that the user starts a run explicitly in the interface.
"""


def model_settings(connection=None):
    with Session() as db:
        settings = db.get(WorkspaceSettings, "local")
        if (
            not settings
            or not settings.encrypted_api_key
            or not settings.base_url
            or not settings.model
        ):
            raise ValueError("Configure model, Base URL and API key in Settings")
        if connection and any(
            connection.get(key) != getattr(settings, key)
            for key in ("base_url", "model")
        ):
            raise ValueError(
                "Model/provider changed since run creation; restore its connection or create a new run"
            )
        return {
            "base_url": settings.base_url,
            "model": settings.model,
            "api_key": decrypt_key(settings.encrypted_api_key),
        }


def project_status(project_id):
    with Session() as db:
        project = db.get(Project, project_id)
        run = db.scalar(
            select(Run)
            .where(Run.project_id == project_id)
            .order_by(Run.created.desc())
            .limit(1)
        )
        directives = db.scalars(
            select(Directive)
            .where(Directive.project_id == project_id)
            .order_by(Directive.created.desc())
            .limit(20)
        ).all()
        return {
            "project": {"name": project.name, "task": project.description},
            "run": {
                "id": run.id,
                "state": run.status,
                "snapshot": run.snapshot,
                "elapsed_seconds": run.elapsed_seconds,
                "usage": run.usage,
                "error": run.error,
                "policy": run.policy,
                "task": run.task_description,
                "input_files": [
                    {"name": item["name"], "sha256": item["sha256"]}
                    for item in run.inputs
                ],
            }
            if run
            else None,
            "directives": [
                {
                    "id": d.id,
                    "state": d.state,
                    "decision": d.decision,
                    "content": d.content,
                }
                for d in directives
            ],
        }


class Worker:
    def __init__(self, saver, owner):
        self.saver = saver
        self.owner = owner
        self.runtime = None
        self.active = None
        self.stopping = False
        self.global_slots = asyncio.Semaphore(
            int(os.getenv("GLOBAL_MODEL_PARALLELISM", "6"))
        )
        self.run_slots = {}

    def client(self, config, run_id=None):
        client = ModelClient(config)
        original = client.complete
        with Session() as db:
            credential = db.get(WorkspaceSettings, "local").encrypted_api_key
        if run_id and run_id not in self.run_slots:
            with Session() as db:
                limits = db.get(Run, run_id).policy["limits"]
            self.run_slots[run_id] = asyncio.Semaphore(limits["parallel_model_calls"])

        async def bounded(*args, **kwargs):
            with Session() as db:
                settings = db.get(WorkspaceSettings, "local")
                if (
                    not settings
                    or settings.encrypted_api_key != credential
                    or settings.base_url != config.base_url
                    or settings.model != config.model
                ):
                    raise APIUnavailable(
                        "Model connection changed or was removed; create a new run with the updated connection"
                    )
            if run_id:
                async with self.run_slots[run_id]:
                    async with self.global_slots:
                        return await original(*args, **kwargs)
            async with self.global_slots:
                return await original(*args, **kwargs)

        client.complete = bounded
        return tracked(client, run_id) if run_id else client

    async def pm_message(self, message):
        config = Config(
            Path("/tmp/task.md"),
            Path("/tmp/pm"),
            seconds=600,
            **model_settings(),
            status_file=None,
            reasoning_effort="medium",
        )
        with Session() as db:
            budget_run = db.scalar(
                select(Run.id)
                .where(
                    Run.project_id == message.project_id,
                    Run.status.not_in(["completed", "failed", "stopped"]),
                )
                .order_by(Run.created.desc())
                .limit(1)
            )
        client = self.client(config, budget_run)
        source_id, project_id = message.id, message.project_id
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_artifact",
                    "description": "Read an evidence file from the latest project run, without executing it; offsets are bytes",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "offset": {"type": "integer", "minimum": 0},
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 32000,
                            },
                        },
                        "required": ["path"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "project_status",
                    "description": "Read actual project/run state and decisions",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "send_to_tech_lead",
                    "description": "Relay an authorized user instruction to tech lead, without assigning specialists",
                    "parameters": {
                        "type": "object",
                        "properties": {"instruction": {"type": "string"}},
                        "required": ["instruction"],
                        "additionalProperties": False,
                    },
                },
            },
        ]

        async def prepare(state):
            history = state.get("history", [])
            summary = state.get("summary", "")
            return [
                {"role": "system", "content": PM_PROMPT},
                {
                    "role": "user",
                    "content": "Current factual project state:\n"
                    + json.dumps(project_status(project_id), ensure_ascii=False),
                },
                *(
                    [
                        {
                            "role": "user",
                            "content": "Working memory, verify against evidence:\n"
                            + summary,
                        }
                    ]
                    if summary
                    else []
                ),
                *history,
            ]

        async def complete(state):
            response, usage = await client.complete(state["context"], tools)
            with Session.begin() as db:
                db.add(
                    Event(
                        project_id=project_id, kind="pm_usage", payload={"usage": usage}
                    )
                )
            return response

        async def dispatch(_, call):
            name = call["function"]["name"]
            args = json.loads(call["function"]["arguments"])
            if name == "project_status":
                return project_status(project_id)
            if name == "read_artifact":
                with Session() as db:
                    latest = db.scalar(
                        select(Run)
                        .where(Run.project_id == project_id)
                        .order_by(Run.created.desc())
                        .limit(1)
                    )
                if not latest:
                    return {"error": "No run exists"}
                root = (RUNS_ROOT / latest.id).resolve()
                path = (root / args.get("path", "")).resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    return {"error": "Unknown evidence path inside this run"}
                offset, limit = (
                    max(0, int(args.get("offset", 0))),
                    min(32000, max(1, int(args.get("limit", 12000)))),
                )
                with path.open("rb") as stream:
                    stream.seek(offset)
                    content = stream.read(limit)
                return {
                    "path": str(path.relative_to(root)),
                    "text": content.decode("utf-8", errors="replace"),
                    "next_offset": offset + len(content),
                    "size": path.stat().st_size,
                }
            if name != "send_to_tech_lead" or not args.get("instruction"):
                return {"error": "Unknown PM operation or empty instruction"}
            with Session.begin() as db:
                directive = db.scalar(
                    select(Directive).where(Directive.message_id == source_id)
                )
                if not directive:
                    directive = Directive(
                        id=str(uuid4()),
                        project_id=project_id,
                        message_id=source_id,
                        content=args["instruction"],
                    )
                    db.add(directive)
                if directive.state in ("received", "awaiting_pm"):
                    directive.content, directive.state = (
                        args["instruction"],
                        "queued_for_tech_lead",
                    )
                db.flush()
                return {
                    "directive_id": directive.id,
                    "state": directive.state,
                    "decision": directive.decision,
                }

        graph = build_agent_graph(
            checkpointer=self.saver,
            prepare=prepare,
            complete=complete,
            dispatch=dispatch,
        )
        graph_config = {
            "configurable": {"thread_id": "pm:" + project_id},
            "recursion_limit": 80,
        }
        try:
            previous = await graph.aget_state(graph_config)
            if (
                previous.values.get("source_message_id") == source_id
                and not previous.next
                and previous.values.get("response", {}).get("content")
            ):
                result = previous.values
            else:
                history = (
                    list(previous.values.get("history", [])) if previous.values else []
                )
                summary = previous.values.get("summary", "") if previous.values else ""
                # Recover an incomplete tool exchange conservatively; directive effects are idempotent by message_id.
                pending = {
                    c["id"] for item in history for c in item.get("tool_calls", [])
                } - {
                    item.get("tool_call_id")
                    for item in history
                    if item["role"] == "tool"
                }
                history.extend(
                    {
                        "role": "tool",
                        "tool_call_id": ident,
                        "content": '{"interrupted":true,"instruction":"Reconcile directive state with project_status before another action"}',
                    }
                    for ident in pending
                )
                if len(json.dumps(history).encode()) > 128000:
                    exchanges = blocks(history)
                    older = exchanges[:-3]
                    if older:
                        source = json.dumps(
                            {"summary": summary, "history": older}, ensure_ascii=False
                        )
                        condensed, _ = await summarize(client, source)
                        if len(condensed) < len(source):
                            summary = condensed
                            history = [
                                item for group in exchanges[-3:] for item in group
                            ]
                result = await graph.ainvoke(
                    {
                        "agent_id": "pm",
                        "role": "project_manager",
                        "source_message_id": source_id,
                        "history": [
                            *history,
                            {"role": "user", "content": message.content},
                        ],
                        "summary": summary,
                    },
                    graph_config,
                    durability="sync",
                )
            answer = (
                result["response"].get("content")
                or "Ответ не содержит текста. Состояние поручения доступно в истории проекта."
            )
            with Session.begin() as db:
                source = db.get(Message, source_id)
                source.status = "delivered"
                answer_id = str(uuid5(NAMESPACE_URL, source_id + ":pm-answer"))
                if not db.get(Message, answer_id):
                    db.add(
                        Message(
                            id=answer_id,
                            project_id=project_id,
                            role="assistant",
                            content=answer,
                            status="delivered",
                        )
                    )
                db.add(
                    Event(
                        project_id=project_id,
                        kind="pm_reply",
                        payload={"message_id": answer_id},
                    )
                )
        finally:
            await client.close()

    async def pm_loop(self):
        while not self.stopping:
            if not ENABLED:
                await asyncio.sleep(2)
                continue
            try:
                model_settings()
            except Exception:
                await asyncio.sleep(2)
                continue
            with Session.begin() as db:
                project_id = db.scalar(
                    select(Project.id)
                    .where(
                        Project.archived.is_(False),
                        select(Message.id)
                        .where(
                            Message.project_id == Project.id,
                            Message.role == "user",
                            Message.status == "pending",
                        )
                        .exists(),
                    )
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                message = (
                    db.scalar(
                        select(Message)
                        .where(
                            Message.project_id == project_id,
                            Message.role == "user",
                            Message.status == "pending",
                        )
                        .order_by(Message.created)
                        .with_for_update()
                        .limit(1)
                    )
                    if project_id
                    else None
                )
                if message:
                    message.status = "processing"
            if not message:
                await asyncio.sleep(1)
                continue
            try:
                await self.pm_message(message)
            except (Exception, asyncio.CancelledError) as error:
                with Session.begin() as db:
                    item = db.get(Message, message.id)
                    if item:
                        item.status, item.error = (
                            "failed",
                            "Ответ PM прерван. Можно повторить обработку сообщения.",
                        )
                        db.add(
                            Event(
                                project_id=item.project_id,
                                kind="pm_error",
                                payload={
                                    "message_id": item.id,
                                    "error_type": type(error).__name__,
                                },
                            )
                        )
                if isinstance(error, asyncio.CancelledError):
                    raise

    def materialize(self, run, project):
        folder = RUNS_ROOT / run.id
        task_dir = folder / "task"
        task = task_dir / "task.md"
        if task.exists():
            return task
        task_dir.mkdir(parents=True, exist_ok=True)
        descriptions = [run.task_description]
        files = []
        for entry in run.inputs:
            origin = (
                DATA_ROOT
                / "projects"
                / run.project_id
                / "inputs"
                / entry["stored_name"]
            )
            name = f"{entry['id'][:8]}_{entry['name']}"
            destination = task_dir / name
            shutil.copy2(origin, destination)
            if hashlib.sha256(destination.read_bytes()).hexdigest() != entry["sha256"]:
                raise ValueError(
                    "Input file changed after upload; restore the original data"
                )
            destination.chmod(0o444)
            files.append(
                {
                    "original_name": entry["name"],
                    "path": str(destination),
                    "sha256": entry["sha256"],
                }
            )
            if entry["name"].lower().endswith(".md"):
                descriptions.append(destination.read_text(encoding="utf-8"))
        task.write_text(
            "\n\n".join(descriptions)
            + "\n\n# Provided immutable input files\n"
            + json.dumps(files, ensure_ascii=False, indent=2)
            + "\n\nUse these provided paths rather than historical paths in the task text. Produce candidate submission.csv in JOB_DIR and register it for independent review. The runtime publishes the approved file beside task.md; do not overwrite it from experiment code.\n",
            encoding="utf-8",
        )
        return task

    def resume_clock(self, store, run):
        with Session.begin() as db:
            current = db.get(Run, run.id, with_for_update=True)
            if not current.paused_at:
                return
            metadata = db.scalar(
                select(ResearchObject)
                .where(
                    ResearchObject.run_id == run.id,
                    ResearchObject.kind == "run",
                    ResearchObject.object_id == "current",
                )
                .with_for_update()
            )
            if metadata:
                stamp = (
                    current.paused_at.replace(tzinfo=timezone.utc)
                    if current.paused_at.tzinfo is None
                    else current.paused_at
                )
                delta = max(0, time.time() - stamp.timestamp())
                meta = copy.deepcopy(metadata.data)
                for key in ("started_wall", "deadline_wall", "checkpoint_wall"):
                    meta[key] += delta
                meta["status"] = "stopped"
                meta.pop("terminal_status", None)
                metadata.data = meta
                for record in db.scalars(
                    select(ResearchObject).where(
                        ResearchObject.run_id == run.id,
                        ResearchObject.kind.in_(["team", "portfolio"]),
                    )
                ).all():
                    item = copy.deepcopy(record.data)
                    for key in ("allocation_deadline_wall", "review_wall"):
                        if item.get(key):
                            item[key] += delta
                    for key in ("first_result_deadline_wall", "review_wall"):
                        if item.get("time_plan", {}).get(key):
                            item["time_plan"][key] += delta
                    record.data = item
            # Clearing the pause and shifting deadlines are one transaction: no double extension.
            current.paused_at = None

    async def research(self, ident):
        with Session() as db:
            run = db.get(Run, ident)
            project = db.get(Project, run.project_id)
        if run.status in ("paused", "stopped", "failed", "completed"):
            return
        folder = RUNS_ROOT / run.id
        store = PostgresStore(run.id, folder / "runtime")
        runtime = None
        client = None
        try:
            connection = model_settings(run.connection)
            task = self.materialize(run, project)
            self.resume_clock(store, run)
            limits = run.policy["limits"]
            config = Config(
                task,
                folder / "runtime",
                seconds=limits["duration_minutes"] * 60,
                cpus=limits["cpu_total"],
                ram_mb=limits["ram_gb"] * 1024,
                llm_concurrency=limits["parallel_model_calls"],
                max_agents=1 + limits["max_teams"] * 6,
                status_file=None,
                resume=bool(store.all("run")),
                **connection,
            )
            config.platform_parallel_jobs = limits["parallel_jobs"]
            client = self.client(config, run.id)
            async with httpx.AsyncClient(timeout=20) as http:
                response = await http.get(
                    os.environ["EXECUTOR_URL"] + "/capabilities",
                    headers={"X-Executor-Token": os.environ["EXECUTOR_TOKEN"]},
                )
                response.raise_for_status()
                capabilities = response.json()
            runtime = self.runtime = PlatformRuntime(
                config,
                client,
                store=store,
                saver=self.saver,
                policy=run.policy,
                run_id=run.id,
                capabilities=capabilities,
            )
            with Session.begin() as db:
                row = db.get(Run, ident)
                row.status = "running"
                row.paused_at = None
                row.updated = now()
                db.add(
                    Event(
                        project_id=run.project_id,
                        kind="run_started",
                        payload={"run_id": ident},
                    )
                )
            published = await runtime.run()
            with Session.begin() as db:
                row = db.get(Run, ident)
                if row.requested_action == "pause" and runtime.pause_requested:
                    row.status = "paused"
                    row.paused_at = now()
                    meta = store.get("run", "current")
                    meta["status"] = "stopped"
                    meta.pop("terminal_status", None)
                    store.put("run", "current", meta)
                elif self.stopping:
                    row.status = "recovering"
                    meta = store.get("run", "current")
                    meta["status"] = "stopped"
                    meta.pop("terminal_status", None)
                    store.put("run", "current", meta)
                else:
                    row.status = (
                        "stopped"
                        if row.requested_action == "stop"
                        else "completed"
                        if published
                        else "failed"
                    )
                    if row.status == "failed":
                        row.error = "Запуск завершён без evaluator-approved результата. Смотри журнал и артефакты."
                        latest_error = db.scalar(
                            select(ResearchEvent)
                            .where(
                                ResearchEvent.run_id == ident,
                                ResearchEvent.kind == "model_error",
                            )
                            .order_by(ResearchEvent.seq.desc())
                            .limit(1)
                        )
                        if latest_error:
                            row.error += (
                                " "
                                + str(
                                    latest_error.data.get(
                                        "detail", latest_error.data.get("category", "")
                                    )
                                )[:1200]
                            )
                row.publication = copy.deepcopy(published)
                row.elapsed_seconds = config.seconds - config.remaining
                row.updated = now()
                row.snapshot = runtime.snapshot()
                db.get(Project, row.project_id).status = row.status
                db.add(
                    Event(
                        project_id=row.project_id,
                        kind="run_state",
                        payload={"run_id": ident, "name": row.status},
                    )
                )
        except Exception as error:
            with Session.begin() as db:
                row = db.get(Run, ident)
                row.status = "failed"
                row.error = "Ошибка исполнения: " + type(error).__name__
                row.updated = now()
                db.add(
                    Event(
                        project_id=row.project_id,
                        kind="run_error",
                        payload={"run_id": ident, "error_type": type(error).__name__},
                    )
                )
        finally:
            store.close()
            if client:
                await client.close()
            self.runtime = None

    async def run(self):
        pm = asyncio.create_task(self.pm_loop())
        try:
            while not self.stopping:
                self.owner.execute(text("SELECT 1"))
                if pm.done():
                    await pm
                if self.active and self.active.done():
                    await self.active
                    self.active = None
                if ENABLED and self.active is None:
                    with Session() as db:
                        run_id = db.scalar(
                            select(Run.id)
                            .where(
                                Run.status.in_(
                                    [
                                        "queued",
                                        "recovering",
                                        "running",
                                        "pausing",
                                        "finalizing",
                                        "stopping",
                                    ]
                                )
                            )
                            .order_by(Run.created)
                            .limit(1)
                        )
                    if run_id:
                        self.active = asyncio.create_task(self.research(run_id))
                await asyncio.sleep(1)
        finally:
            pm.cancel()
            await asyncio.gather(pm, return_exceptions=True)
            if self.runtime:
                self.runtime.done = True
            if self.active:
                await self.active


async def main():
    # Dedicated DB advisory lock prevents two workers owning the same external actions.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as owner:
        if not owner.scalar(text("SELECT pg_try_advisory_lock(73002617)")):
            raise RuntimeError("Another platform worker already owns this workspace")
        uri = str(engine.url.render_as_string(hide_password=False)).replace(
            "postgresql+psycopg://", "postgresql://", 1
        )
        async with AsyncPostgresSaver.from_conn_string(uri) as saver:
            await saver.setup()
            with Session.begin() as db:
                for run in db.scalars(select(Run)):
                    usage = dict(run.usage or {})
                    if usage.get("pending_requests"):
                        usage["accounted_cost"] = usage.get(
                            "accounted_cost", 0.0
                        ) + usage.get("reserved_cost", 0.0)
                        usage["unknown_requests"] = (
                            usage.get("unknown_requests", 0) + usage["pending_requests"]
                        )
                        usage.update(pending_requests=0, reserved_cost=0.0)
                        run.usage = usage
                for message in db.scalars(
                    select(Message).where(Message.status == "processing")
                ):
                    message.status, message.error = (
                        "failed",
                        "Worker был перезапущен. Проверь поручение и повтори обработку при необходимости.",
                    )
            worker = Worker(saver, owner)
            loop = asyncio.get_running_loop()
            for event in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(
                    event, lambda: setattr(worker, "stopping", True)
                )
            print(
                "Worker ready; model calls " + ("enabled" if ENABLED else "disabled"),
                flush=True,
            )
            await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
