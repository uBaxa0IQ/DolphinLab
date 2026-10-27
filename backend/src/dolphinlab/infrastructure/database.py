"""PostgreSQL models and database session factory."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DATA_ROOT = Path(os.getenv("WORKSPACE_DATA_DIR", Path.cwd() / ".workspace-data")).resolve()
DATA_ROOT.mkdir(parents=True, exist_ok=True)
database_url = os.getenv("DATABASE_URL", "")
if not database_url.startswith("postgresql"):
    raise RuntimeError(
        "PostgreSQL DATABASE_URL is required. Start the platform with Docker Compose."
    )
if database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
engine = create_engine(database_url, pool_pre_ping=True)

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
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
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
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Directive(Base):
    __tablename__ = "directives"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), unique=True)
    state: Mapped[str] = mapped_column(String(24), default="received")
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    content: Mapped[str] = mapped_column(Text, default="")
    decision: Mapped[str] = mapped_column(Text, default="")
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


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
    pricing: Mapped[dict] = mapped_column(JSON, default=dict)


class ProjectPolicyRecord(Base):
    __tablename__ = "project_policies"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    policy: Mapped[dict] = mapped_column(JSON)


class GoalRevision(Base):
    __tablename__ = "project_goals"
    __table_args__ = (
        UniqueConstraint("project_id", "revision", name="uq_goal_revision"),
        UniqueConstraint("project_id", "id", name="uq_goal_project_id"),
        UniqueConstraint("project_id", "request_id", name="uq_goal_request"),
        CheckConstraint("revision > 0", name="ck_goal_revision_positive"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict] = mapped_column(JSON)
    origin: Mapped[str] = mapped_column(String(20), default="user")
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ResearchIteration(Base):
    __tablename__ = "research_iterations"
    __table_args__ = (
        UniqueConstraint("project_id", "sequence", name="uq_iteration_sequence"),
        UniqueConstraint("project_id", "id", name="uq_iteration_project_id"),
        UniqueConstraint("project_id", "request_id", name="uq_iteration_request"),
        CheckConstraint("sequence > 0", name="ck_iteration_sequence_positive"),
        ForeignKeyConstraint(
            ["project_id", "goal_revision_id"],
            ["project_goals.project_id", "project_goals.id"],
            name="fk_iteration_goal_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "parent_iteration_id"],
            ["research_iterations.project_id", "research_iterations.id"],
            name="fk_iteration_parent_project",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    goal_revision_id: Mapped[str] = mapped_column(String(36))
    parent_iteration_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    legacy: Mapped[bool] = mapped_column(Boolean, default=False)
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "iteration_id"],
            ["research_iterations.project_id", "research_iterations.id"],
            name="fk_run_iteration_project",
        ),
        UniqueConstraint("project_id", "request_id", name="uq_run_request"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    iteration_id: Mapped[str] = mapped_column(String(36), index=True)
    goal_snapshot: Mapped[dict] = mapped_column(JSON)
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    requested_action: Mapped[str] = mapped_column(String(20), default="run")
    policy: Mapped[dict] = mapped_column(JSON)
    inputs: Mapped[list] = mapped_column(JSON)
    connection: Mapped[dict] = mapped_column(JSON)
    task_description: Mapped[str] = mapped_column(Text, default="")
    elapsed_seconds: Mapped[float] = mapped_column(Float, default=0)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    publication: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResearchObject(Base):
    __tablename__ = "research_objects"
    __table_args__ = (UniqueConstraint("run_id", "kind", "object_id"),)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    object_id: Mapped[str] = mapped_column(String(120))
    data: Mapped[dict] = mapped_column(JSON)


class ResearchMail(Base):
    __tablename__ = "research_mail"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    recipient: Mapped[str] = mapped_column(String(120))
    sender: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(Text)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    created: Mapped[float] = mapped_column(Float)


class ResearchEvent(Base):
    __tablename__ = "research_events"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(80))
    data: Mapped[dict] = mapped_column(JSON)
    created: Mapped[float] = mapped_column(Float)


class ResearchReceipt(Base):
    __tablename__ = "research_receipts"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), primary_key=True)
    agent: Mapped[str] = mapped_column(String(120), primary_key=True)
    call_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    result: Mapped[dict] = mapped_column(JSON)
