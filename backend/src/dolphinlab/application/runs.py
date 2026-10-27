import os
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select

from dolphinlab.api.dependencies import execution_enabled, require_project
from dolphinlab.api.presenters import run_view
from dolphinlab.api.schemas import ControlRun, StartRun
from dolphinlab.domain.policy import ProjectPolicy
from dolphinlab.domain.research import GoalSpec, request_fingerprint
from dolphinlab.infrastructure.database import (
    Event,
    GoalRevision,
    InputFile,
    Project,
    ProjectPolicyRecord,
    ResearchEvent,
    ResearchIteration,
    Run,
    WorkspaceSettings,
    now,
)
from dolphinlab.research.staffing import freeze_profiles

from .base import Service
from .knowledge import run_context
from .research import append_iteration, latest_goal, latest_iteration, replay


class RunService(Service):
    def start_run(self, project_id: str, body: StartRun | None = None):
        body = body or StartRun()
        with self.sessions.begin() as db:
            project = db.scalar(select(Project).where(Project.id == project_id).with_for_update())
            if not project:
                raise HTTPException(404, "Проект не найден")
            if body.client_id:
                existing = replay(
                    db.scalar(
                        select(Run).where(
                            Run.project_id == project_id,
                            Run.request_id == str(body.client_id),
                        )
                    ),
                    body,
                )
                if existing:
                    return run_view(existing)
            if not execution_enabled():
                raise HTTPException(
                    409,
                    "Исполнение отключено. Включи ENABLE_MODEL_CALLS в настройках Docker, когда будешь готов к платным запросам.",
                )
            if project.archived:
                raise HTTPException(409, "Верни проект из архива перед запуском")
            if db.scalar(
                select(Run.id).where(
                    Run.project_id == project_id,
                    Run.status.not_in(["completed", "stopped", "failed"]),
                )
            ):
                raise HTTPException(409, "У проекта уже есть активный или приостановленный запуск")
            settings = db.get(WorkspaceSettings, "local")
            if (
                not settings
                or not settings.encrypted_api_key
                or not settings.base_url
                or not settings.model
            ):
                raise HTTPException(409, "Укажи API-ключ, Base URL и модель в настройках")
            history = db.scalars(
                select(InputFile)
                .where(InputFile.project_id == project_id)
                .order_by(InputFile.created)
            ).all()
            files = list({file.name: file for file in history}.values())
            if not files:
                raise HTTPException(409, "Добавь входные файлы перед запуском")
            record = db.get(ProjectPolicyRecord, project_id)
            policy = (
                ProjectPolicy(revision=record.revision, **record.policy)
                if record
                else ProjectPolicy()
            )
            if policy.limits.api_budget is not None and not settings.pricing:
                raise HTTPException(
                    409,
                    "Для денежного лимита настрой цены и ёмкости запросов в подключении API",
                )
            if policy.limits.api_budget is not None:
                from dolphinlab.infrastructure.usage import Pricing

                if policy.limits.api_budget < Pricing.model_validate(settings.pricing).reservation:
                    raise HTTPException(
                        409,
                        "Бюджет меньше резерва на один полный ответ модели. Проверь тариф или увеличь лимит.",
                    )
            if policy.limits.cpu_total > int(
                os.getenv("EXECUTOR_CPUS", "8")
            ) or policy.limits.ram_gb > int(os.getenv("EXECUTOR_MEMORY_GB", "28")):
                raise HTTPException(
                    409,
                    "Ресурсы проекта превышают выделенные executor. Уменьши их в настройках проекта.",
                )
            if body.iteration_id:
                iteration = db.scalar(
                    select(ResearchIteration).where(
                        ResearchIteration.id == str(body.iteration_id),
                        ResearchIteration.project_id == project_id,
                    )
                )
                if not iteration:
                    raise HTTPException(404, "Итерация не найдена в этом проекте")
                goal = db.get(GoalRevision, iteration.goal_revision_id)
            else:
                goal = latest_goal(db, project_id)
                prepared = latest_iteration(db, project_id)
                if (
                    prepared
                    and prepared.goal_revision_id == goal.id
                    and not db.scalar(
                        select(Run.id).where(Run.iteration_id == prepared.id).limit(1)
                    )
                ):
                    iteration = prepared
                else:
                    iteration = append_iteration(db, project_id, goal.id)
            snapshot = GoalSpec.model_validate(goal.spec)
            run = Run(
                id=str(uuid4()),
                project_id=project_id,
                iteration_id=iteration.id,
                goal_snapshot=snapshot.model_dump(mode="json"),
                role_catalog=freeze_profiles(),
                project_context=run_context(
                    db,
                    project_id,
                    goal.id,
                    {file.id for file in files},
                    iteration.parent_iteration_id,
                ),
                request_id=str(body.client_id) if body.client_id else None,
                request_hash=request_fingerprint(body) if body.client_id else None,
                task_description=snapshot.task_text(),
                policy=policy.model_dump(),
                inputs=[
                    {
                        "id": f.id,
                        "name": f.name,
                        "stored_name": f.stored_name,
                        "sha256": f.sha256,
                        "size": f.size,
                    }
                    for f in files
                ],
                connection={
                    "model": settings.model,
                    "base_url": settings.base_url,
                    "pricing": settings.pricing,
                },
            )
            db.add(run)
            db.flush()
            project.status = "queued"
            db.add(
                Event(
                    project_id=project_id,
                    kind="run_created",
                    payload={"run_id": run.id, "name": "Запуск добавлен в очередь"},
                )
            )
            return run_view(run)

    def list_runs(self, project_id: str):
        with self.sessions() as db:
            require_project(db, project_id)
            return [
                run_view(r)
                for r in db.scalars(
                    select(Run).where(Run.project_id == project_id).order_by(Run.created.desc())
                )
            ]

    def control_run(self, project_id: str, run_id: str, body: ControlRun):
        with self.sessions.begin() as db:
            run = db.scalar(
                select(Run).where(Run.id == run_id, Run.project_id == project_id).with_for_update()
            )
            if not run:
                raise HTTPException(404, "Запуск не найден")
            transitions = {
                "pause": ({"queued", "running", "recovering"}, "pausing"),
                "resume": ({"paused"}, "queued"),
                "finish": ({"running", "recovering"}, "finalizing"),
                "stop": (
                    {
                        "queued",
                        "running",
                        "pausing",
                        "paused",
                        "recovering",
                        "finalizing",
                        "stopping",
                    },
                    "stopping",
                ),
            }
            if body.action not in transitions:
                raise HTTPException(400, "Неизвестное действие")
            allowed, next_state = transitions[body.action]
            if run.status not in allowed:
                raise HTTPException(409, "Действие недоступно в текущем состоянии")
            if body.action == "resume" and not execution_enabled():
                raise HTTPException(409, "Вызовы модели отключены")
            previous = run.status
            run.requested_action = "run" if body.action == "resume" else body.action
            run.status = next_state
            if previous in ("queued", "paused") and body.action in ("pause", "stop"):
                run.status = "paused" if body.action == "pause" else "stopped"
                if body.action == "pause":
                    run.paused_at = now()
            run.updated = now()
            db.add(
                Event(
                    project_id=project_id,
                    kind="run_control",
                    payload={"run_id": run.id, "name": body.action},
                )
            )
            return run_view(run)

    def research_events(self, project_id: str, run_id: str, after: int = 0):
        with self.sessions() as db:
            run = db.get(Run, run_id)
            if not run or run.project_id != project_id:
                raise HTTPException(404)
            return [
                {"id": e.seq, "kind": e.kind, "data": e.data, "created": e.created}
                for e in db.scalars(
                    select(ResearchEvent)
                    .where(ResearchEvent.run_id == run_id, ResearchEvent.seq > after)
                    .order_by(ResearchEvent.seq)
                    .limit(100)
                )
            ]

    def run_artifacts(self, project_id: str, run_id: str):
        with self.sessions() as db:
            run = db.get(Run, run_id)
            if not run or run.project_id != project_id:
                raise HTTPException(404)
        root = Path(os.environ.get("RUNS_ROOT", "/data/runs")) / run_id
        if not root.exists():
            return []
        return [
            {"path": str(path.relative_to(root)), "size": path.stat().st_size}
            for path in root.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and path.resolve().is_relative_to(root.resolve())
        ][:1000]

    def run_artifact(self, project_id: str, run_id: str, path: str):
        with self.sessions() as db:
            run = db.get(Run, run_id)
            if not run or run.project_id != project_id:
                raise HTTPException(404)
        root = (Path(os.environ.get("RUNS_ROOT", "/data/runs")) / run_id).resolve()
        target = (root / path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise HTTPException(404)
        return FileResponse(
            target,
            filename=target.name,
            media_type="application/octet-stream",
            headers={"X-Content-Type-Options": "nosniff"},
        )
