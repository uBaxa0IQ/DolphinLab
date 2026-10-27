from zoneinfo import ZoneInfo

from sqlalchemy import select

from dolphinlab.api.dependencies import execution_enabled
from dolphinlab.infrastructure.database import (
    ConversationAccount,
    Event,
    GoalRevision,
    InputFile,
    ProjectPolicyRecord,
    Run,
)

MOSCOW = ZoneInfo("Europe/Moscow")


def time_label(value):
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
        "intent": message.intent,
        "reply_to_id": message.reply_to_id,
    }


def run_view(run):
    return {
        "id": run.id,
        "iteration_id": run.iteration_id,
        "goal_snapshot": run.goal_snapshot,
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


def goal_view(goal):
    return {
        "id": goal.id,
        "project_id": goal.project_id,
        "revision": goal.revision,
        "goal": goal.spec,
        "origin": goal.origin,
        "created": goal.created.isoformat(),
    }


def iteration_view(db, iteration, *, goal=None, runs=None):
    if goal is None:
        goal = db.get(GoalRevision, iteration.goal_revision_id)
    if runs is None:
        runs = list(
            db.scalars(
                select(Run)
                .where(Run.iteration_id == iteration.id)
                .order_by(Run.created.desc(), Run.id.desc())
            )
        )
    return {
        "id": iteration.id,
        "project_id": iteration.project_id,
        "sequence": iteration.sequence,
        "parent_iteration_id": iteration.parent_iteration_id,
        "goal": goal_view(goal),
        "legacy": iteration.legacy,
        "created": iteration.created.isoformat(),
        "status": runs[0].status if runs else "draft",
        "runs": [run_view(run) for run in runs],
    }


def project_view(db, project):
    files = db.scalars(
        select(InputFile).where(InputFile.project_id == project.id).order_by(InputFile.created)
    ).all()
    events = db.scalars(
        select(Event).where(Event.project_id == project.id).order_by(Event.seq.desc()).limit(30)
    ).all()
    titles = {
        "project_created": "Проект создан",
        "file_uploaded": "Добавлен файл",
        "message_saved": "Сообщение сохранено",
        "directive_received": "Получено поручение",
        "policy_updated": "Обновлены настройки проекта",
        "goal_created": "Сохранена цель исследования",
        "iteration_created": "Создана итерация",
    }
    latest = db.scalar(
        select(Run).where(Run.project_id == project.id).order_by(Run.created.desc()).limit(1)
    )
    snapshot = latest.snapshot if latest else {}
    goal = db.scalar(
        select(GoalRevision)
        .where(GoalRevision.project_id == project.id)
        .order_by(GoalRevision.revision.desc())
        .limit(1)
    )
    conversation = db.get(ConversationAccount, project.id)
    policy_record = db.get(ProjectPolicyRecord, project.id)
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
        "current_goal": goal_view(goal) if goal else None,
        "conversation_usage": conversation.usage if conversation else {},
        "conversation_budget": (
            policy_record.policy.get("conversation", {}).get("api_budget", 0)
            if policy_record
            else 0
        ),
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


def settings_view(settings):
    return {
        "display_name": settings.display_name if settings else "Исследователь",
        "base_url": settings.base_url if settings else "",
        "model": settings.model if settings else "",
        "key_configured": bool(settings and settings.encrypted_api_key),
        "execution_enabled": execution_enabled(),
        "pricing": settings.pricing if settings else {},
    }
