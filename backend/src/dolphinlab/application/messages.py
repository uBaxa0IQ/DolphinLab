from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from dolphinlab.api.dependencies import require_project
from dolphinlab.api.presenters import message_view
from dolphinlab.api.schemas import CreateMessage
from dolphinlab.infrastructure.database import Directive, Event, Message, Project

from .base import Service


class MessageService(Service):
    def list_messages(self, project_id: str):
        with self.sessions() as db:
            require_project(db, project_id)
            decisions = {
                d.message_id: d
                for d in db.scalars(select(Directive).where(Directive.project_id == project_id))
            }
            return [
                {
                    **message_view(m),
                    **(
                        {
                            "directive_status": decisions[m.id].state,
                            "directive_decision": decisions[m.id].decision,
                        }
                        if m.id in decisions
                        else {}
                    ),
                }
                for m in db.scalars(
                    select(Message)
                    .where(Message.project_id == project_id)
                    .order_by(Message.created, Message.id)
                )
            ]

    def save_message(self, project_id: str, body: CreateMessage):
        # Persist the actual user message; do not invent an assistant reply in real projects.
        with self.sessions.begin() as db:
            project = db.scalar(select(Project).where(Project.id == project_id).with_for_update())
            if not project:
                raise HTTPException(404)
            if project.archived:
                raise HTTPException(409, "Верни проект из архива перед общением с PM")
            ident = str(body.client_id or uuid4())
            existing = db.get(Message, ident)
            if existing:
                if (
                    existing.project_id != project_id
                    or existing.content != body.content
                    or existing.directive != body.directive
                ):
                    raise HTTPException(409, "ID сообщения уже использован для другого содержимого")
                return message_view(existing)
            message = Message(
                id=ident,
                project_id=project_id,
                role="user",
                content=body.content,
                directive=body.directive,
            )
            db.add(message)
            db.flush()
            if body.directive:
                db.add(
                    Directive(
                        id=str(uuid4()),
                        project_id=project_id,
                        message_id=message.id,
                        content=body.content,
                        state="awaiting_pm",
                    )
                )
            db.add(
                Event(
                    project_id=project_id,
                    kind="directive_received" if body.directive else "message_saved",
                    payload={"message_id": message.id},
                )
            )
            return message_view(message)

    def retry_message(self, project_id: str, message_id: str):
        with self.sessions.begin() as db:
            message = db.get(Message, message_id)
            if not message or message.project_id != project_id or message.status != "failed":
                raise HTTPException(409, "Сообщение недоступно для повтора")
            message.status, message.error = "pending", None
            db.add(
                Event(
                    project_id=project_id,
                    kind="message_retry",
                    payload={"message_id": message_id},
                )
            )
            return message_view(message)
