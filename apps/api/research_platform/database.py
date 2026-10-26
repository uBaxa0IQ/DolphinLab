"""Application state. SQLite for the local preview; DATABASE_URL can select Postgres."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DATA_ROOT = Path(os.getenv("WORKSPACE_DATA_DIR", Path(__file__).resolve().parents[3] / ".workspace-data")).resolve()
DATA_ROOT.mkdir(parents=True, exist_ok=True)
database_url = os.getenv("DATABASE_URL", "sqlite:///" + str(DATA_ROOT / "workspace.sqlite3"))
if database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
engine = create_engine(database_url, connect_args={"check_same_thread": False, "timeout": 20} if database_url.startswith("sqlite") else {}, pool_pre_ping=True)

if database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def sqlite_settings(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")

Session = sessionmaker(engine, expire_on_commit=False)


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="draft")
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class InputFile(Base):
    __tablename__ = "input_files"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(240))
    stored_name: Mapped[str] = mapped_column(String(60))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    directive: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Directive(Base):
    __tablename__ = "directives"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), unique=True)
    state: Mapped[str] = mapped_column(String(24), default="received")
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Event(Base):
    __tablename__ = "project_events"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ToolReceipt(Base):
    __tablename__ = "tool_receipts"
    session_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    call_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    state: Mapped[str] = mapped_column(String(24), default="started")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class WorkspaceSettings(Base):
    __tablename__ = "workspace_settings"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(80), default="Исследователь")
    base_url: Mapped[str] = mapped_column(String(1000), default="")
    model: Mapped[str] = mapped_column(String(200), default="")
    encrypted_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProjectPolicyRecord(Base):
    __tablename__ = "project_policies"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    policy: Mapped[dict] = mapped_column(JSON)
