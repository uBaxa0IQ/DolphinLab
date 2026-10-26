"""Local, model-free project API. No model clients are created by this server."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, SecretStr, field_validator
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from .database import Base, DATA_ROOT, Directive, Event, InputFile, Message, Project, Session, WorkspaceSettings, engine
from .secrets import encrypt_key

MAX_FILE_BYTES = 100 * 1024 * 1024
ALLOWED_EXTENSIONS = {".md", ".txt", ".csv", ".tsv", ".parquet", ".json", ".jsonl", ".xlsx", ".zip", ".gz", ".npy", ".npz", ".feather"}
MOSCOW = ZoneInfo("Europe/Moscow")


@asynccontextmanager
async def lifespan(_):
    Base.metadata.create_all(engine)
    yield
    engine.dispose()


app = FastAPI(title="Sonder workspace", version="0.1.0", lifespan=lifespan)


class UpdateSettings(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(default="", max_length=1000)
    model: str = Field(default="", max_length=200)
    api_key: SecretStr | None = None
    clear_key: bool = False

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
        if parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("Нужен HTTP(S) Base URL без ключей, логина и параметров")
        return value


def settings_view(settings):
    return {"display_name": settings.display_name if settings else "Исследователь",
            "base_url": settings.base_url if settings else "", "model": settings.model if settings else "",
            "key_configured": bool(settings and settings.encrypted_api_key), "execution_enabled": False}


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
            raise HTTPException(500, "Не удалось зашифровать ключ. Изменения не сохранены.") from None
    with Session.begin() as db:
        settings = db.get(WorkspaceSettings, "local")
        if settings is None:
            settings = WorkspaceSettings(id="local")
            db.add(settings)
        settings.display_name = body.display_name
        settings.base_url = body.base_url
        settings.model = body.model.strip()
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
    return value.replace(tzinfo=timezone.utc).astimezone(MOSCOW).strftime("%H:%M") if value.tzinfo is None else value.astimezone(MOSCOW).strftime("%H:%M")


def file_view(file):
    return {"id": file.id, "name": file.name, "size": file.size, "sha256": file.sha256,
            "kind": "Задача" if file.name.lower().endswith(".md") else "Данные"}


def message_view(message):
    return {"id": message.id, "role": message.role, "content": message.content, "time": time_label(message.created),
            "directive": message.directive, "status": message.status}


def project_view(db, project):
    files = db.scalars(select(InputFile).where(InputFile.project_id == project.id).order_by(InputFile.created)).all()
    events = db.scalars(select(Event).where(Event.project_id == project.id).order_by(Event.seq.desc()).limit(30)).all()
    titles = {"project_created": "Проект создан", "file_uploaded": "Добавлен файл", "message_saved": "Сообщение сохранено", "directive_received": "Получено поручение"}
    return {"id": project.id, "name": project.name, "description": project.description, "status": project.status,
            "demo": False, "teams": [], "experiments": [], "files": [file_view(f) for f in files],
            "events": [{"id": str(e.seq), "time": time_label(e.created), "title": titles.get(e.kind, e.kind),
                        "detail": e.payload.get("name", ""), "type": "info"} for e in events]}


@app.get("/api/health")
def health():
    return {"status": "ok", "execution_enabled": False, "mode": "local-preview"}


@app.get("/api/projects")
def list_projects():
    with Session() as db:
        return [project_view(db, project) for project in db.scalars(select(Project).order_by(Project.created.desc()))]


@app.post("/api/projects", status_code=201)
def create_project(body: CreateProject):
    with Session.begin() as db:
        project = Project(id=str(uuid4()), name=body.name, description=body.description)
        db.add(project)
        db.flush()
        db.add(Event(project_id=project.id, kind="project_created", payload={"name": project.name}))
        db.flush()
        return project_view(db, project)


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    with Session() as db:
        return project_view(db, require_project(db, project_id))


@app.get("/api/projects/{project_id}/messages")
def list_messages(project_id: str):
    with Session() as db:
        require_project(db, project_id)
        return [message_view(m) for m in db.scalars(select(Message).where(Message.project_id == project_id).order_by(Message.created, Message.id))]


@app.post("/api/projects/{project_id}/messages", status_code=201)
def save_message(project_id: str, body: CreateMessage):
    # Persist the actual user message; do not invent an assistant reply in real projects.
    with Session.begin() as db:
        require_project(db, project_id)
        message = Message(id=str(uuid4()), project_id=project_id, role="user", content=body.content, directive=body.directive)
        db.add(message)
        db.flush()
        if body.directive:
            db.add(Directive(id=str(uuid4()), project_id=project_id, message_id=message.id))
        db.add(Event(project_id=project_id, kind="directive_received" if body.directive else "message_saved", payload={"message_id": message.id}))
        return message_view(message)


@app.post("/api/projects/{project_id}/files", status_code=201)
async def upload_file(project_id: str, file: UploadFile = File(...)):
    with Session() as db:
        require_project(db, project_id)
    name = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not name or len(name) > 240 or re.search(r"[\x00-\x1f\x7f]", name) or Path(name).suffix.lower() not in ALLOWED_EXTENSIONS:
        await file.close()
        raise HTTPException(400, "Допустимы файлы данных, task.md и ZIP; имя до 240 символов")
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
            record = InputFile(id=file_id, project_id=project_id, name=name, stored_name=file_id, size=total, sha256=digest.hexdigest())
            db.add(record)
            db.add(Event(project_id=project_id, kind="file_uploaded", payload={"file_id": file_id, "name": name}))
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
        return FileResponse(path, filename=file.name, media_type="application/octet-stream", headers={"X-Content-Type-Options": "nosniff"})


def events_after(project_id, cursor):
    with Session() as db:
        return [{"id": e.seq, "kind": e.kind, "data": e.payload} for e in db.scalars(select(Event).where(Event.project_id == project_id, Event.seq > cursor).order_by(Event.seq).limit(100))]


@app.get("/api/projects/{project_id}/events")
async def stream_events(project_id: str, request: Request, last_event_id: str | None = Header(default=None)):
    with Session() as db:
        require_project(db, project_id)
    try:
        cursor = max(0, int(last_event_id or 0))
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

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/projects/{project_id}/runs")
def start_run(project_id: str):
    with Session() as db:
        require_project(db, project_id)
    raise HTTPException(409, "Исполнение агентов отключено в версии для просмотра дизайна. API модели не вызывается.")
