"""Local, model-free project API. No model clients are created by this server."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, SecretStr, field_validator
from sqlalchemy import select, update, delete, text
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .database import (
    Base,
    DATA_ROOT,
    Directive,
    Event,
    InputFile,
    Message,
    Project,
    ProjectPolicyRecord,
    ResearchEvent,
    ResearchMail,
    ResearchObject,
    ResearchReceipt,
    Run,
    Session,
    WorkspaceSettings,
    engine,
    now,
)
from .project_policy import ProjectPolicy
from .secrets import encrypt_key

MAX_FILE_BYTES = 100 * 1024 * 1024
ALLOWED_EXTENSIONS = {
    ".md",
    ".txt",
    ".csv",
    ".tsv",
    ".parquet",
    ".json",
    ".jsonl",
    ".xlsx",
    ".zip",
    ".gz",
    ".npy",
    ".npz",
    ".feather",
}
MOSCOW = ZoneInfo("Europe/Moscow")


@asynccontextmanager
async def lifespan(_):
    if os.getenv("PLATFORM_ENV") != "docker":
        Base.metadata.create_all(engine)
    yield
    engine.dispose()


app = FastAPI(title="Sonder workspace", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,api").split(","),
)


def execution_enabled():
    return os.getenv("ENABLE_MODEL_CALLS", "false").lower() == "true"


class UpdateSettings(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(default="", max_length=1000)
    model: str = Field(default="", max_length=200)
    api_key: SecretStr | None = None
    clear_key: bool = False
    pricing: dict = Field(default_factory=dict)

    @field_validator("display_name")
    @classmethod
    def nonempty_name(cls, value):
        if not value.strip():
            raise ValueError("Укажи имя")
        return value.strip()

    @field_validator("base_url")
    @classmethod
    def connection_url(cls, value):
        from urllib.parse import urlsplit

        value = value.strip().rstrip("/")
        if not value:
            return value
        try:
            parts = urlsplit(value)
            _ = parts.port
        except ValueError:
            raise ValueError("Укажи корректный Base URL")
        if (
            parts.scheme not in ("https", "http")
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError("Нужен HTTP(S) Base URL без ключей, логина и параметров")
        return value


def settings_view(settings):
    return {
        "display_name": settings.display_name if settings else "Исследователь",
        "base_url": settings.base_url if settings else "",
        "model": settings.model if settings else "",
        "key_configured": bool(settings and settings.encrypted_api_key),
        "execution_enabled": execution_enabled(),
        "pricing": settings.pricing if settings else {},
    }


@app.get("/api/settings")
def get_settings():
    with Session() as db:
        return settings_view(db.get(WorkspaceSettings, "local"))


@app.put("/api/settings")
def update_settings(body: UpdateSettings):
    # The encrypted value is never returned to the browser or included in events.
    ciphertext = None
    supplied = body.api_key.get_secret_value().strip() if body.api_key else ""
    if supplied and body.clear_key:
        raise HTTPException(400, "Выбери замену или удаление ключа")
    if supplied:
        if len(supplied) > 8192:
            raise HTTPException(400, "Слишком длинный ключ")
        try:
            ciphertext = encrypt_key(supplied)
        except Exception:
            raise HTTPException(
                500, "Не удалось зашифровать ключ. Изменения не сохранены."
            ) from None
    with Session.begin() as db:
        settings = db.get(WorkspaceSettings, "local")
        if settings is None:
            settings = WorkspaceSettings(id="local")
            db.add(settings)
        settings.display_name = body.display_name
        settings.base_url = body.base_url
        settings.model = body.model.strip()
        from .usage import Pricing

        if body.pricing:
            try:
                settings.pricing = Pricing.model_validate(body.pricing).model_dump()
            except Exception:
                raise HTTPException(
                    400,
                    "Для бюджета нужны валюта, цены за миллион токенов и подтверждённые ёмкости ввода/вывода",
                ) from None
        else:
            settings.pricing = {}
        if body.clear_key:
            settings.encrypted_api_key = None
        elif ciphertext:
            settings.encrypted_api_key = ciphertext
        return settings_view(settings)


class CreateProject(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=20000)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        if not value.strip():
            raise ValueError("Укажи название проекта")
        return value.strip()


class CreateMessage(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    directive: bool = False
    client_id: UUID | None = None

    @field_validator("content")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Сообщение пустое")
        return value.strip()


def require_project(db, project_id):
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Проект не найден")
    return project


def time_label(value):
    # SQLite returns naive UTC; Postgres keeps the UTC timezone.
    from datetime import timezone

    return (
        value.replace(tzinfo=timezone.utc).astimezone(MOSCOW).strftime("%H:%M")
        if value.tzinfo is None
        else value.astimezone(MOSCOW).strftime("%H:%M")
    )


def file_view(file):
    return {
        "id": file.id,
        "name": file.name,
        "size": file.size,
        "sha256": file.sha256,
        "kind": "Задача" if file.name.lower().endswith(".md") else "Данные",
    }


def message_view(message):
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "time": time_label(message.created),
        "directive": message.directive,
        "status": message.status,
        "error": message.error,
    }


def run_view(run):
    return {
        "id": run.id,
        "status": run.status,
        "elapsed_seconds": run.elapsed_seconds,
        "policy": run.policy,
        "created": run.created.isoformat(),
        "updated": run.updated.isoformat(),
        "error": run.error,
        "usage": run.usage,
        "publication": {
            "candidate_id": run.publication.get("candidate_id"),
            "sha256": run.publication.get("sha256"),
        }
        if run.publication
        else None,
    }


def project_view(db, project):
    files = db.scalars(
        select(InputFile)
        .where(InputFile.project_id == project.id)
        .order_by(InputFile.created)
    ).all()
    events = db.scalars(
        select(Event)
        .where(Event.project_id == project.id)
        .order_by(Event.seq.desc())
        .limit(30)
    ).all()
    titles = {
        "project_created": "Проект создан",
        "file_uploaded": "Добавлен файл",
        "message_saved": "Сообщение сохранено",
        "directive_received": "Получено поручение",
        "policy_updated": "Обновлены настройки проекта",
    }
    latest = db.scalar(
        select(Run)
        .where(Run.project_id == project.id)
        .order_by(Run.created.desc())
        .limit(1)
    )
    snapshot = latest.snapshot if latest else {}
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "archived": project.archived,
        "status": latest.status if latest else project.status,
        "demo": False,
        "teams": snapshot.get("teams", []),
        "experiments": snapshot.get("experiments", []),
        "files": [file_view(f) for f in files],
        "latest_run": run_view(latest) if latest else None,
        "events": [
            {
                "id": str(e.seq),
                "time": time_label(e.created),
                "title": titles.get(e.kind, e.kind),
                "detail": e.payload.get("name", ""),
                "type": "info",
            }
            for e in events
        ],
    }


@app.get("/api/health")
def health():
    from sqlalchemy import text

    with Session() as db:
        db.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "execution_enabled": execution_enabled(),
        "mode": "docker" if os.getenv("PLATFORM_ENV") == "docker" else "local-preview",
    }


@app.get("/api/projects")
def list_projects():
    with Session() as db:
        return [
            project_view(db, project)
            for project in db.scalars(select(Project).order_by(Project.created.desc()))
        ]


@app.post("/api/projects", status_code=201)
def create_project(body: CreateProject):
    with Session.begin() as db:
        project = Project(id=str(uuid4()), name=body.name, description=body.description)
        db.add(project)
        db.flush()
        db.add(
            Event(
                project_id=project.id,
                kind="project_created",
                payload={"name": project.name},
            )
        )
        db.flush()
        return project_view(db, project)


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    with Session() as db:
        return project_view(db, require_project(db, project_id))


class EditProject(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=20000)
    archived: bool = False


@app.patch("/api/projects/{project_id}")
def edit_project(project_id: str, body: EditProject):
    if not body.name.strip():
        raise HTTPException(400, "Название пустое")
    with Session.begin() as db:
        project = db.scalar(
            select(Project).where(Project.id == project_id).with_for_update()
        )
        if not project:
            raise HTTPException(404)
        if body.archived and db.scalar(
            select(Run.id).where(
                Run.project_id == project_id,
                Run.status.not_in(["completed", "stopped", "failed"]),
            )
        ):
            raise HTTPException(409, "Сначала заверши или останови исследование")
        project.name, project.description, project.archived = (
            body.name.strip(),
            body.description,
            body.archived,
        )
        db.add(
            Event(
                project_id=project_id,
                kind="project_updated",
                payload={"name": project.name},
            )
        )
        db.flush()
        return project_view(db, project)


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str):
    import shutil

    roots = []
    with Session.begin() as db:
        project = db.scalar(
            select(Project).where(Project.id == project_id).with_for_update()
        )
        if not project:
            raise HTTPException(404)
        if db.scalar(
            select(Run.id).where(
                Run.project_id == project_id,
                Run.status.not_in(["completed", "stopped", "failed"]),
            )
        ):
            raise HTTPException(
                409, "Сначала останови активный или приостановленный запуск"
            )
        if db.scalar(
            select(Message.id).where(
                Message.project_id == project_id, Message.status == "processing"
            )
        ):
            raise HTTPException(409, "Дождись текущего ответа PM перед удалением")
        run_ids = list(db.scalars(select(Run.id).where(Run.project_id == project_id)))
        # Checkpoint namespace is immutable: PM/project and research run/agent UUIDs.
        parameters = {"pm": "pm:" + project_id}
        condition = "thread_id = :pm"
        for i, ident in enumerate(run_ids):
            parameters[f"r{i}"] = ident + ":%"
            condition += f" OR thread_id LIKE :r{i}"
        for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            if db.scalar(text("SELECT to_regclass(:name)"), {"name": table}):
                db.execute(text(f"DELETE FROM {table} WHERE {condition}"), parameters)
        for model in (ResearchReceipt, ResearchMail, ResearchEvent, ResearchObject):
            db.execute(delete(model).where(model.run_id.in_(run_ids)))
        db.execute(delete(Directive).where(Directive.project_id == project_id))
        for model in (Message, InputFile, Event, ProjectPolicyRecord, Run):
            db.execute(delete(model).where(model.project_id == project_id))
        db.delete(project)
        upload_root = (DATA_ROOT / "projects").resolve()
        run_root = Path(os.environ.get("RUNS_ROOT", "/data/runs")).resolve()
        roots = [
            (upload_root, upload_root / project_id),
            *[(run_root, run_root / ident) for ident in run_ids],
        ]
    removed = True
    for root, candidate in roots:
        target = candidate.resolve()
        if not target.is_relative_to(root) or target == root:
            removed = False
            continue
        try:
            shutil.rmtree(target)
        except FileNotFoundError:
            pass
        except OSError:
            removed = False
    return {"deleted": True, "files_removed": removed}


@app.get("/api/projects/{project_id}/settings")
def get_project_settings(project_id: str):
    with Session() as db:
        require_project(db, project_id)
        record = db.get(ProjectPolicyRecord, project_id)
        return (
            ProjectPolicy(revision=record.revision, **record.policy)
            if record
            else ProjectPolicy()
        )


@app.put("/api/projects/{project_id}/settings")
def update_project_settings(project_id: str, body: ProjectPolicy):
    payload = body.model_dump(exclude={"revision"})
    next_revision = body.revision + 1
    try:
        with Session.begin() as db:
            require_project(db, project_id)
            if body.revision == 0:
                db.add(
                    ProjectPolicyRecord(
                        project_id=project_id, revision=next_revision, policy=payload
                    )
                )
                db.flush()
            else:
                changed = db.execute(
                    update(ProjectPolicyRecord)
                    .where(
                        ProjectPolicyRecord.project_id == project_id,
                        ProjectPolicyRecord.revision == body.revision,
                    )
                    .values(revision=next_revision, policy=payload)
                )
                if changed.rowcount != 1:
                    raise HTTPException(
                        409, "Настройки изменены в другом окне. Открой их заново."
                    )
            db.add(
                Event(
                    project_id=project_id,
                    kind="policy_updated",
                    payload={"revision": next_revision},
                )
            )
    except IntegrityError:
        raise HTTPException(
            409, "Настройки изменены в другом окне. Открой их заново."
        ) from None
    return ProjectPolicy(revision=next_revision, **payload)


@app.get("/api/projects/{project_id}/messages")
def list_messages(project_id: str):
    with Session() as db:
        require_project(db, project_id)
        decisions = {
            d.message_id: d
            for d in db.scalars(
                select(Directive).where(Directive.project_id == project_id)
            )
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


@app.post("/api/projects/{project_id}/messages", status_code=201)
def save_message(project_id: str, body: CreateMessage):
    # Persist the actual user message; do not invent an assistant reply in real projects.
    with Session.begin() as db:
        project = db.scalar(
            select(Project).where(Project.id == project_id).with_for_update()
        )
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
                raise HTTPException(
                    409, "ID сообщения уже использован для другого содержимого"
                )
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


@app.post("/api/projects/{project_id}/files", status_code=201)
async def upload_file(project_id: str, file: UploadFile = File(...)):
    with Session() as db:
        require_project(db, project_id)
    name = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if (
        not name
        or len(name) > 240
        or re.search(r"[\x00-\x1f\x7f]", name)
        or Path(name).suffix.lower() not in ALLOWED_EXTENSIONS
    ):
        await file.close()
        raise HTTPException(
            400, "Допустимы файлы данных, task.md и ZIP; имя до 240 символов"
        )
    file_id = str(uuid4())
    folder = DATA_ROOT / "projects" / project_id / "inputs"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / file_id
    total = 0
    digest = hashlib.sha256()
    try:
        # Incoming names are metadata only; they never select a filesystem path.
        with path.open("xb") as target:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_FILE_BYTES:
                    raise HTTPException(413, "Файл больше 100 МБ")
                target.write(chunk)
                digest.update(chunk)
        if total == 0:
            raise HTTPException(400, "Файл пустой")
        with Session.begin() as db:
            require_project(db, project_id)
            record = InputFile(
                id=file_id,
                project_id=project_id,
                name=name,
                stored_name=file_id,
                size=total,
                sha256=digest.hexdigest(),
            )
            db.add(record)
            db.add(
                Event(
                    project_id=project_id,
                    kind="file_uploaded",
                    payload={"file_id": file_id, "name": name},
                )
            )
            result = file_view(record)
        return result
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@app.get("/api/projects/{project_id}/files/{file_id}")
def download_file(project_id: str, file_id: str):
    with Session() as db:
        require_project(db, project_id)
        file = db.get(InputFile, file_id)
        if file is None or file.project_id != project_id:
            raise HTTPException(404, "Файл не найден")
        path = DATA_ROOT / "projects" / project_id / "inputs" / file.stored_name
        if not path.is_file():
            raise HTTPException(404, "Файл отсутствует в хранилище")
        return FileResponse(
            path,
            filename=file.name,
            media_type="application/octet-stream",
            headers={"X-Content-Type-Options": "nosniff"},
        )


def events_after(project_id, cursor):
    with Session() as db:
        return [
            {"id": e.seq, "kind": e.kind, "data": e.payload}
            for e in db.scalars(
                select(Event)
                .where(Event.project_id == project_id, Event.seq > cursor)
                .order_by(Event.seq)
                .limit(100)
            )
        ]


@app.get("/api/projects/{project_id}/events")
async def stream_events(
    project_id: str,
    request: Request,
    after: int = 0,
    last_event_id: str | None = Header(default=None),
):
    with Session() as db:
        require_project(db, project_id)
    try:
        cursor = max(0, int(last_event_id or after))
    except ValueError:
        raise HTTPException(400, "Некорректный ID события")

    async def stream():
        nonlocal cursor
        ticks = 0
        while not await request.is_disconnected():
            events = await run_in_threadpool(events_after, project_id, cursor)
            for item in events:
                cursor = item["id"]
                yield f"id: {cursor}\nevent: update\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
            ticks += 1
            if ticks % 15 == 0:
                yield ": keepalive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/projects/{project_id}/runs")
def start_run(project_id: str):
    if not execution_enabled():
        raise HTTPException(
            409,
            "Исполнение отключено. Включи ENABLE_MODEL_CALLS в настройках Docker, когда будешь готов к платным запросам.",
        )
    with Session.begin() as db:
        project = db.scalar(
            select(Project).where(Project.id == project_id).with_for_update()
        )
        if not project:
            raise HTTPException(404, "Проект не найден")
        if project.archived:
            raise HTTPException(409, "Верни проект из архива перед запуском")
        if db.scalar(
            select(Run.id).where(
                Run.project_id == project_id,
                Run.status.not_in(["completed", "stopped", "failed"]),
            )
        ):
            raise HTTPException(
                409, "У проекта уже есть активный или приостановленный запуск"
            )
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
            from .usage import Pricing

            if (
                policy.limits.api_budget
                < Pricing.model_validate(settings.pricing).reservation
            ):
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
        run = Run(
            id=str(uuid4()),
            project_id=project_id,
            task_description=project.description,
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


@app.get("/api/projects/{project_id}/runs")
def list_runs(project_id: str):
    with Session() as db:
        require_project(db, project_id)
        return [
            run_view(r)
            for r in db.scalars(
                select(Run)
                .where(Run.project_id == project_id)
                .order_by(Run.created.desc())
            )
        ]


class ControlRun(BaseModel):
    action: str


@app.post("/api/projects/{project_id}/runs/{run_id}/control")
def control_run(project_id: str, run_id: str, body: ControlRun):
    with Session.begin() as db:
        run = db.scalar(
            select(Run)
            .where(Run.id == run_id, Run.project_id == project_id)
            .with_for_update()
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


@app.get("/api/projects/{project_id}/runs/{run_id}/events")
def research_events(project_id: str, run_id: str, after: int = 0):
    with Session() as db:
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


@app.get("/api/projects/{project_id}/runs/{run_id}/artifacts")
def run_artifacts(project_id: str, run_id: str):
    with Session() as db:
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


@app.get("/api/projects/{project_id}/runs/{run_id}/artifact")
def run_artifact(project_id: str, run_id: str, path: str):
    with Session() as db:
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


@app.post("/api/projects/{project_id}/messages/{message_id}/retry")
def retry_message(project_id: str, message_id: str):
    with Session.begin() as db:
        message = db.get(Message, message_id)
        if (
            not message
            or message.project_id != project_id
            or message.status != "failed"
        ):
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
