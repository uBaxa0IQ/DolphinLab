import copy
import hashlib
import json
import os
import shutil
import time
from datetime import timezone
from pathlib import Path

import httpx
from sqlalchemy import select

from dolphinlab.agents.engine import PlatformRuntime
from dolphinlab.infrastructure.connections import model_settings
from dolphinlab.infrastructure.database import (
    DATA_ROOT,
    Event,
    Project,
    ResearchEvent,
    ResearchObject,
    Run,
    Session,
    now,
)
from dolphinlab.infrastructure.store import PostgresStore
from dolphinlab.research.config import Config

RUNS_ROOT = Path(os.environ.get("RUNS_ROOT", "/data/runs"))


class ResearchSession:
    """Materialize inputs, restore the session clock, and execute one research run."""

    def __init__(self, saver, clients):
        self.saver, self.clients = saver, clients
        self.runtime = None
        self.stopping = False

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
            origin = DATA_ROOT / "projects" / run.project_id / "inputs" / entry["stored_name"]
            name = f"{entry['id'][:8]}_{entry['name']}"
            destination = task_dir / name
            shutil.copy2(origin, destination)
            if hashlib.sha256(destination.read_bytes()).hexdigest() != entry["sha256"]:
                raise ValueError("Input file changed after upload; restore the original data")
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

    async def execute(self, ident):
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
                resume=bool(store.all("run")),
                **connection,
            )
            config.platform_parallel_jobs = limits["parallel_jobs"]
            client = self.clients.create(config, run.id)
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
