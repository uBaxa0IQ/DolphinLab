import asyncio
import os

from sqlalchemy import select

from dolphinlab.infrastructure.database import Run, Session, WorkspaceSettings
from dolphinlab.infrastructure.llm import APIUnavailable, ModelClient
from dolphinlab.infrastructure.usage import tracked


class ModelClientFactory:
    """Share model concurrency limits and validate credentials before each request."""

    def __init__(self):
        self.global_slots = asyncio.Semaphore(int(os.getenv("GLOBAL_MODEL_PARALLELISM", "6")))
        self.run_slots = {}

    def create_conversation(self, config, project_id):
        with Session() as db:
            active = db.scalar(
                select(Run.id)
                .where(
                    Run.project_id == project_id,
                    Run.status.not_in(["completed", "failed", "stopped"]),
                )
                .order_by(Run.created.desc())
                .limit(1)
            )
        return self.create(config, concurrency_run_id=active)

    def create(self, config, run_id=None, *, concurrency_run_id=None):
        client = ModelClient(config)
        original = client.complete
        with Session() as db:
            credential = db.get(WorkspaceSettings, "local").encrypted_api_key
        slot_id = run_id or concurrency_run_id
        if slot_id and slot_id not in self.run_slots:
            with Session() as db:
                limits = db.get(Run, slot_id).policy["limits"]
            self.run_slots[slot_id] = asyncio.Semaphore(limits["parallel_model_calls"])

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
            if slot_id:
                async with self.run_slots[slot_id]:
                    async with self.global_slots:
                        return await original(*args, **kwargs)
            async with self.global_slots:
                return await original(*args, **kwargs)

        client.complete = bounded
        return tracked(client, run_id) if run_id else client
