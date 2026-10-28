import asyncio
import os
import signal

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import select, text

from dolphinlab.agents.project_manager import ProjectManager
from dolphinlab.agents.session import ResearchSession
from dolphinlab.application.documents import DocumentService
from dolphinlab.application.pages import PageService
from dolphinlab.infrastructure.conversation_usage import recover_reservations
from dolphinlab.infrastructure.database import ConversationAccount, Message, Run, Session, engine
from dolphinlab.infrastructure.model_clients import ModelClientFactory

ENABLED = os.getenv("ENABLE_MODEL_CALLS", "false").lower() == "true"


class Worker:
    """Single-owner scheduler for PM messages and research sessions."""

    def __init__(self, saver, owner):
        self.owner = owner
        self.stopping = False
        self.active = None
        clients = ModelClientFactory()
        self.manager = ProjectManager(saver, clients)
        self.researcher = ResearchSession(saver, clients)

    async def run(self):
        pm = asyncio.create_task(self.manager.run())
        ingestion = asyncio.create_task(self.ingest())
        try:
            while not self.stopping:
                self.owner.execute(text("SELECT 1"))
                if pm.done():
                    await pm
                if ingestion.done():
                    await ingestion
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
                        self.active = asyncio.create_task(self.researcher.execute(run_id))
                await asyncio.sleep(1)
        finally:
            self.manager.stopping = True
            self.researcher.stopping = True
            pm.cancel()
            ingestion.cancel()
            await asyncio.gather(pm, ingestion, return_exceptions=True)
            if self.researcher.runtime:
                self.researcher.runtime.done = True
            if self.active:
                await self.active

    async def ingest(self):
        documents = DocumentService()
        pages = PageService()
        while not self.stopping:
            await asyncio.to_thread(documents.process_next)
            await asyncio.to_thread(pages.refresh_sources)
            await asyncio.sleep(1)


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
                for account in db.scalars(select(ConversationAccount)):
                    account.usage = recover_reservations(account.usage)
                for run in db.scalars(select(Run)):
                    usage = dict(run.usage or {})
                    if usage.get("pending_requests"):
                        usage["accounted_cost"] = usage.get("accounted_cost", 0.0) + usage.get(
                            "reserved_cost", 0.0
                        )
                        usage["unknown_requests"] = (
                            usage.get("unknown_requests", 0) + usage["pending_requests"]
                        )
                        usage.update(pending_requests=0, reserved_cost=0.0)
                        run.usage = usage
                for message in db.scalars(select(Message).where(Message.status == "processing")):
                    message.status, message.error = (
                        "failed",
                        "Worker был перезапущен. Проверь поручение и повтори обработку при необходимости.",
                    )
            worker = Worker(saver, owner)
            loop = asyncio.get_running_loop()
            for event in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(event, lambda: setattr(worker, "stopping", True))
            print(
                "Worker ready; model calls " + ("enabled" if ENABLED else "disabled"),
                flush=True,
            )
            await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
