import os
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError

from dolphinlab.api.dependencies import require_project
from dolphinlab.api.presenters import project_view
from dolphinlab.api.schemas import CreateProject, EditProject
from dolphinlab.domain.policy import ProjectPolicy
from dolphinlab.domain.research import GoalSpec
from dolphinlab.infrastructure.database import (
    DATA_ROOT,
    ContextManifest,
    ConversationAccount,
    Directive,
    Document,
    DocumentChunk,
    DocumentVersion,
    Event,
    GoalRevision,
    InputFile,
    Message,
    PageFeedback,
    PageRelease,
    PageRevision,
    PMAction,
    Project,
    ProjectMemory,
    ProjectPage,
    ProjectPolicyRecord,
    ResearchEvent,
    ResearchIteration,
    ResearchMail,
    ResearchObject,
    ResearchReceipt,
    Run,
)

from .base import Service
from .research import append_goal, latest_goal


class ProjectService(Service):
    def list_projects(self):
        with self.sessions() as db:
            return [
                project_view(db, project)
                for project in db.scalars(select(Project).order_by(Project.created.desc()))
            ]

    def create_project(self, body: CreateProject):
        with self.sessions.begin() as db:
            project = Project(id=str(uuid4()), name=body.name, description=body.description)
            db.add(project)
            db.flush()
            append_goal(
                db,
                project,
                GoalSpec(question=body.description.strip() or body.name),
                origin="project",
            )
            db.add(
                Event(
                    project_id=project.id,
                    kind="project_created",
                    payload={"name": project.name},
                )
            )
            db.flush()
            return project_view(db, project)

    def get_project(self, project_id: str):
        with self.sessions() as db:
            return project_view(db, require_project(db, project_id))

    def edit_project(self, project_id: str, body: EditProject):
        if not body.name.strip():
            raise HTTPException(400, "Название пустое")
        with self.sessions.begin() as db:
            project = db.scalar(select(Project).where(Project.id == project_id).with_for_update())
            if not project:
                raise HTTPException(404)
            if body.archived and db.scalar(
                select(Run.id).where(
                    Run.project_id == project_id,
                    Run.status.not_in(["completed", "stopped", "failed"]),
                )
            ):
                raise HTTPException(409, "Сначала заверши или останови исследование")
            if project.description != body.description:
                previous = latest_goal(db, project_id)
                if (
                    body.expected_goal_revision is not None
                    and body.expected_goal_revision != previous.revision
                ):
                    raise HTTPException(409, "Цель изменилась. Открой настройки проекта заново.")
                append_goal(
                    db,
                    project,
                    GoalSpec.model_validate(
                        {
                            **previous.spec,
                            "question": body.description.strip() or body.name,
                        }
                    ),
                    origin="project",
                )
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

    def delete_project(self, project_id: str):
        import shutil

        roots = []
        with self.sessions.begin() as db:
            project = db.scalar(select(Project).where(Project.id == project_id).with_for_update())
            if not project:
                raise HTTPException(404)
            if db.scalar(
                select(Run.id).where(
                    Run.project_id == project_id,
                    Run.status.not_in(["completed", "stopped", "failed"]),
                )
            ):
                raise HTTPException(409, "Сначала останови активный или приостановленный запуск")
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
            for model in (PageFeedback, PageRelease, PageRevision, ProjectPage):
                db.execute(delete(model).where(model.project_id == project_id))
            for model in (ProjectMemory, ContextManifest, PMAction, ConversationAccount):
                db.execute(delete(model).where(model.project_id == project_id))
            db.execute(delete(Directive).where(Directive.project_id == project_id))
            for model in (Message, InputFile, Event, ProjectPolicyRecord, Run):
                db.execute(delete(model).where(model.project_id == project_id))
            for model in (ResearchIteration, GoalRevision):
                db.execute(delete(model).where(model.project_id == project_id))
            for model in (DocumentChunk, DocumentVersion, Document):
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

                def remove_readonly(function, path, error):
                    if not isinstance(error, PermissionError) or not Path(
                        path
                    ).resolve().is_relative_to(target):
                        raise error
                    os.chmod(path, 0o700)
                    function(path)

                shutil.rmtree(target, onexc=remove_readonly)
            except FileNotFoundError:
                pass
            except OSError:
                removed = False
        return {"deleted": True, "files_removed": removed}

    def get_project_settings(self, project_id: str):
        with self.sessions() as db:
            require_project(db, project_id)
            record = db.get(ProjectPolicyRecord, project_id)
            return (
                ProjectPolicy(revision=record.revision, **record.policy)
                if record
                else ProjectPolicy()
            )

    def update_project_settings(self, project_id: str, body: ProjectPolicy):
        payload = body.model_dump(exclude={"revision"})
        next_revision = body.revision + 1
        try:
            with self.sessions.begin() as db:
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
