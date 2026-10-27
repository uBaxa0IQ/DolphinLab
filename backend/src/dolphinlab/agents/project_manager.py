import asyncio
import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import select

from dolphinlab.agents.graph import build_agent_graph
from dolphinlab.infrastructure.connections import model_settings
from dolphinlab.infrastructure.database import Directive, Event, Message, Project, Run, Session
from dolphinlab.research.config import Config
from dolphinlab.research.context import blocks, summarize

RUNS_ROOT = Path(os.environ.get("RUNS_ROOT", "/data/runs"))
ENABLED = os.getenv("ENABLE_MODEL_CALLS", "false").lower() == "true"

PM_PROMPT = "You are the project manager of an autonomous research workspace. Speak the user's language.\nAnswer status questions from observed project state; distinguish measurements, researcher claims and unknowns.\nProject metadata may be edited for a future run. The active run's task, inputs and policy are its own snapshot.\nYour only research-command recipient is the tech lead. Relay additional user instructions with send_to_tech_lead,\nthen report delivery honestly; a queued directive is not an accepted or completed change. Never claim you trained,\npaused, stopped or verified something merely because the user asked. Runtime control buttons own lifecycle changes.\nUse project_status to inspect teams, current run, artifacts and directive decisions. You cannot execute code or assign\nspecialists directly. Ask a focused question when an instruction is ambiguous. Preserve scope and evidence links.\nIf no research run exists, help clarify the task and explain that the user starts a run explicitly in the interface.\n"


def project_status(project_id):
    with Session() as db:
        project = db.get(Project, project_id)
        run = db.scalar(
            select(Run).where(Run.project_id == project_id).order_by(Run.created.desc()).limit(1)
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
                    {"name": item["name"], "sha256": item["sha256"]} for item in run.inputs
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


class ProjectManager:
    """Own the user conversation and relay directives exclusively to the tech lead."""

    def __init__(self, saver, clients):
        self.saver, self.clients = saver, clients
        self.stopping = False

    async def handle(self, message):
        config = Config(
            Path("/tmp/task.md"),
            Path("/tmp/pm"),
            seconds=600,
            **model_settings(),
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
        client = self.clients.create(config, budget_run)
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
                            "content": "Working memory, verify against evidence:\n" + summary,
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
                db.add(Event(project_id=project_id, kind="pm_usage", payload={"usage": usage}))
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
                directive = db.scalar(select(Directive).where(Directive.message_id == source_id))
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
                history = list(previous.values.get("history", [])) if previous.values else []
                summary = previous.values.get("summary", "") if previous.values else ""
                # Recover an incomplete tool exchange conservatively; directive effects are idempotent by message_id.
                pending = {c["id"] for item in history for c in item.get("tool_calls", [])} - {
                    item.get("tool_call_id") for item in history if item["role"] == "tool"
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
                            history = [item for group in exchanges[-3:] for item in group]
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

    async def run(self):
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
                await self.handle(message)
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
