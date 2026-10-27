"""Single-owner scheduling for project conversations."""

import asyncio
import os

from sqlalchemy import select

from dolphinlab.agents.pm_session import PMContextError, PMConversation
from dolphinlab.infrastructure.connections import model_settings
from dolphinlab.infrastructure.conversation_usage import ConversationBudgetError
from dolphinlab.infrastructure.database import Event, Message, Project, Session

ENABLED = os.getenv("ENABLE_MODEL_CALLS", "false").lower() == "true"


class ProjectManager:
    def __init__(self, saver, clients, sessions=Session, connection_provider=model_settings):
        self.saver, self.clients, self.sessions = saver, clients, sessions
        self.connection_provider = connection_provider
        self.stopping = False

    async def handle(self, message):
        await PMConversation(
            self.saver, self.clients, self.sessions, self.connection_provider
        ).handle(message)

    async def run(self):
        while not self.stopping:
            if not ENABLED:
                await asyncio.sleep(2)
                continue
            try:
                self.connection_provider()
            except Exception:
                await asyncio.sleep(2)
                continue
            with self.sessions.begin() as db:
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
                with self.sessions.begin() as db:
                    item = db.get(Message, message.id)
                    if item:
                        item.status, item.error = (
                            "failed",
                            str(error)
                            if isinstance(error, (ConversationBudgetError, PMContextError))
                            else "Ответ PM прерван. Можно повторить обработку сообщения.",
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
