"""Project intent and iteration history; all writes serialize on the project row."""

from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from dolphinlab.api.dependencies import require_project
from dolphinlab.api.presenters import goal_view, iteration_view
from dolphinlab.api.schemas import CreateGoal, CreateIteration
from dolphinlab.domain.research import GoalSpec, request_fingerprint
from dolphinlab.infrastructure.database import Event, GoalRevision, Project, ResearchIteration, Run

from .base import Service


def latest_goal(db, project_id):
    return db.scalar(
        select(GoalRevision)
        .where(GoalRevision.project_id == project_id)
        .order_by(GoalRevision.revision.desc())
        .limit(1)
    )


def latest_iteration(db, project_id):
    return db.scalar(
        select(ResearchIteration)
        .where(ResearchIteration.project_id == project_id)
        .order_by(ResearchIteration.sequence.desc())
        .limit(1)
    )


def append_goal(db, project, goal: GoalSpec, *, origin="user", request_id=None, request_hash=None):
    from .document_context import selected_documents
    from .result_context import validate_prior_results

    validate_prior_results(db, project.id, goal.prior_results)
    selected_documents(db, project.id, goal.document_sources)
    previous = latest_goal(db, project.id)
    record = GoalRevision(
        id=str(uuid4()),
        project_id=project.id,
        revision=previous.revision + 1 if previous else 1,
        spec=goal.model_dump(mode="json"),
        origin=origin,
        request_id=request_id,
        request_hash=request_hash,
    )
    db.add(record)
    db.flush()
    db.add(
        Event(
            project_id=project.id,
            kind="goal_created",
            payload={
                "goal_revision_id": record.id,
                "revision": record.revision,
                "name": f"Версия цели {record.revision}",
            },
        )
    )
    return record


def append_iteration(db, project_id, goal_id, *, request_id=None, request_hash=None):
    previous = latest_iteration(db, project_id)
    record = ResearchIteration(
        id=str(uuid4()),
        project_id=project_id,
        sequence=previous.sequence + 1 if previous else 1,
        goal_revision_id=goal_id,
        parent_iteration_id=previous.id if previous else None,
        request_id=request_id,
        request_hash=request_hash,
    )
    db.add(record)
    db.flush()
    db.add(
        Event(
            project_id=project_id,
            kind="iteration_created",
            payload={"iteration_id": record.id, "name": f"Итерация {record.sequence}"},
        )
    )
    return record


def replay(record, body):
    if record and record.request_hash != request_fingerprint(body):
        raise HTTPException(409, "ID операции уже использован для другого содержимого")
    return record


class ResearchService(Service):
    def list_goals(self, project_id):
        with self.sessions() as db:
            require_project(db, project_id)
            return [
                goal_view(record)
                for record in db.scalars(
                    select(GoalRevision)
                    .where(GoalRevision.project_id == project_id)
                    .order_by(GoalRevision.revision.desc())
                )
            ]

    def create_goal(self, project_id, body: CreateGoal):
        with self.sessions.begin() as db:
            project = self._lock(db, project_id)
            existing = replay(
                db.scalar(
                    select(GoalRevision).where(
                        GoalRevision.project_id == project_id,
                        GoalRevision.request_id == str(body.client_id),
                    )
                ),
                body,
            )
            if existing:
                return goal_view(existing)
            self._editable(project)
            previous = latest_goal(db, project_id)
            if body.expected_revision != (previous.revision if previous else 0):
                raise HTTPException(409, "Цель изменилась. Обнови историю перед сохранением.")
            record = append_goal(
                db,
                project,
                body.goal,
                request_id=str(body.client_id),
                request_hash=request_fingerprint(body),
            )
            project.description = body.goal.question
            return goal_view(record)

    def list_iterations(self, project_id):
        with self.sessions() as db:
            require_project(db, project_id)
            goals = {
                g.id: g
                for g in db.scalars(
                    select(GoalRevision).where(GoalRevision.project_id == project_id)
                )
            }
            runs = {}
            for run in db.scalars(
                select(Run)
                .where(Run.project_id == project_id)
                .order_by(Run.created.desc(), Run.id.desc())
            ):
                runs.setdefault(run.iteration_id, []).append(run)
            return [
                iteration_view(
                    db, item, goal=goals[item.goal_revision_id], runs=runs.get(item.id, [])
                )
                for item in db.scalars(
                    select(ResearchIteration)
                    .where(ResearchIteration.project_id == project_id)
                    .order_by(ResearchIteration.sequence.desc())
                )
            ]

    def get_iteration(self, project_id, iteration_id):
        with self.sessions() as db:
            require_project(db, project_id)
            item = db.scalar(
                select(ResearchIteration).where(
                    ResearchIteration.project_id == project_id,
                    ResearchIteration.id == iteration_id,
                )
            )
            if not item:
                raise HTTPException(404, "Итерация не найдена")
            return iteration_view(db, item)

    def create_iteration(self, project_id, body: CreateIteration):
        with self.sessions.begin() as db:
            project = self._lock(db, project_id)
            existing = replay(
                db.scalar(
                    select(ResearchIteration).where(
                        ResearchIteration.project_id == project_id,
                        ResearchIteration.request_id == str(body.client_id),
                    )
                ),
                body,
            )
            if existing:
                return iteration_view(db, existing)
            self._editable(project)
            goal = db.scalar(
                select(GoalRevision).where(
                    GoalRevision.project_id == project_id,
                    GoalRevision.id == str(body.goal_revision_id),
                )
            )
            if not goal:
                raise HTTPException(404, "Версия цели не найдена в этом проекте")
            previous = latest_iteration(db, project_id)
            if body.expected_sequence != (previous.sequence if previous else 0) or (
                str(body.parent_iteration_id) if body.parent_iteration_id else None
            ) != (previous.id if previous else None):
                raise HTTPException(409, "История изменилась. Обнови её перед созданием итерации.")
            record = append_iteration(
                db,
                project_id,
                goal.id,
                request_id=str(body.client_id),
                request_hash=request_fingerprint(body),
            )
            return iteration_view(db, record, goal=goal, runs=[])

    @staticmethod
    def _lock(db, project_id):
        project = db.scalar(select(Project).where(Project.id == project_id).with_for_update())
        if not project:
            raise HTTPException(404, "Проект не найден")
        return project

    @staticmethod
    def _editable(project):
        if project.archived:
            raise HTTPException(409, "Верни проект из архива перед изменением исследования")
