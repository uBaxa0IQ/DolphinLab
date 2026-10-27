"""PostgreSQL models and database session factory."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
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
    __table_args__ = (UniqueConstraint("project_id", "id", name="uq_message_project_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    directive: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    intent: Mapped[str] = mapped_column(String(20), default="question")
    context_binding: Mapped[dict] = mapped_column(JSON, default=dict)
    reply_to_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


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
    iteration_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


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
    project_context: Mapped[dict] = mapped_column(JSON, default=dict)
    role_catalog: Mapped[list] = mapped_column(JSON, default=list)
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


class PMAction(Base):
    __tablename__ = "pm_actions"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_pm_action_project_id"),
        ForeignKeyConstraint(
            ["project_id", "message_id"],
            ["messages.project_id", "messages.id"],
            name="fk_pm_action_message_project",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    message_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    arguments: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ProjectMemory(Base):
    __tablename__ = "project_memory"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_memory_project_id"),
        ForeignKeyConstraint(
            ["project_id", "message_id"],
            ["messages.project_id", "messages.id"],
            name="fk_memory_message_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "goal_revision_id"],
            ["project_goals.project_id", "project_goals.id"],
            name="fk_memory_goal_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "source_action_id"],
            ["pm_actions.project_id", "pm_actions.id"],
            name="fk_memory_action_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "supersedes_id"],
            ["project_memory.project_id", "project_memory.id"],
            name="fk_memory_supersedes_project",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    message_id: Mapped[str] = mapped_column(String(36))
    goal_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    scope: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    search_text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20))
    source_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_action_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    input_ids: Mapped[list] = mapped_column(JSON, default=list)
    document_refs: Mapped[list] = mapped_column(JSON, default=list)
    supersedes_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ContextManifest(Base):
    __tablename__ = "context_manifests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), index=True)
    snapshot: Mapped[dict] = mapped_column(JSON)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ConversationAccount(Base):
    __tablename__ = "conversation_accounts"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("project_id", "id", name="uq_document_project_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(240))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_document_version_project_id"),
        UniqueConstraint("document_id", "revision", name="uq_document_version_revision"),
        UniqueConstraint("project_id", "request_id", name="uq_document_version_request"),
        ForeignKeyConstraint(
            ["project_id", "document_id"], ["documents.project_id", "documents.id"]
        ),
        CheckConstraint("revision > 0", name="ck_document_revision_positive"),
        CheckConstraint(
            "state IN ('queued','processing','ready','failed','deleted')", name="ck_document_state"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    document_id: Mapped[str] = mapped_column(String(36), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(240))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(36))
    request_hash: Mapped[str] = mapped_column(String(64))
    pipeline: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    lease_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "version_id"], ["document_versions.project_id", "document_versions.id"]
        ),
        UniqueConstraint("version_id", "ordinal", name="uq_chunk_ordinal"),
        Index("ix_document_chunk_search", "search_vector", postgresql_using="gin"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    version_id: Mapped[str] = mapped_column(String(36), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    search_text: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    locator: Mapped[dict] = mapped_column(JSON)
    search_vector: Mapped[object] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('russian'::regconfig, search_text) || to_tsvector('english'::regconfig, search_text) || to_tsvector('simple'::regconfig, search_text)",
            persisted=True,
        ),
    )
