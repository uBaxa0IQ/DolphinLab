"""Separate, conservative project budget for PM calls, including compaction."""

from sqlalchemy import select

from dolphinlab.domain.policy import ProjectPolicy
from dolphinlab.infrastructure.database import (
    ConversationAccount,
    Event,
    Project,
    ProjectPolicyRecord,
    Session,
    WorkspaceSettings,
)
from dolphinlab.infrastructure.llm import APIUnavailable, ResponseProblem, request_purpose
from dolphinlab.infrastructure.usage import Pricing


class ConversationBudgetError(APIUnavailable):
    """A safe, user-visible budget explanation."""


def tracked_conversation(client, project_id, sessions=Session, *, message_id=None):
    original = client.complete

    async def complete(*args, **kwargs):
        with sessions.begin() as db:
            project = db.scalar(select(Project).where(Project.id == project_id).with_for_update())
            if not project or project.archived:
                raise ConversationBudgetError("Проект недоступен для общения с моделью")
            record = db.get(ProjectPolicyRecord, project_id)
            policy = (
                ProjectPolicy(revision=record.revision, **record.policy)
                if record
                else ProjectPolicy()
            )
            budget = policy.conversation.api_budget
            settings = db.get(WorkspaceSettings, "local")
            if not budget:
                raise ConversationBudgetError(
                    "Задай отдельный бюджет общения PM в настройках проекта"
                )
            if not settings or not settings.pricing:
                raise ConversationBudgetError(
                    "Для бюджета общения настрой тариф и ёмкости запросов модели"
                )
            pricing = Pricing.model_validate(settings.pricing)
            reservation = pricing.reservation_for(
                args[0] if args else kwargs.get("messages", []),
                args[1] if len(args) > 1 else kwargs.get("tools"),
            )
            account = db.get(ConversationAccount, project_id)
            if not account:
                account = ConversationAccount(project_id=project_id, usage={})
                db.add(account)
            usage = dict(account.usage or {})
            if usage.get("currency", pricing.currency) != pricing.currency:
                raise ConversationBudgetError(
                    "Валюта тарифа отличается от валюты расходов общения проекта"
                )
            if (
                usage.get("accounted_cost", 0) + usage.get("reserved_cost", 0) + reservation
                > budget
            ):
                raise ConversationBudgetError(
                    "Бюджет общения PM исчерпан или недостаточен для полного ответа"
                )
            usage.update(
                currency=pricing.currency,
                pending_requests=usage.get("pending_requests", 0) + 1,
                reserved_cost=usage.get("reserved_cost", 0) + reservation,
            )
            account.usage = usage
        reported, rejected = None, False
        try:
            answer, reported = await original(*args, **kwargs)
            return answer, reported
        except ResponseProblem as error:
            reported = error.usage
            raise
        except Exception as error:
            rejected = isinstance(error, APIUnavailable) or getattr(error, "status_code", None) in (
                400,
                401,
                402,
                403,
                404,
                422,
                429,
            )
            raise
        finally:
            actual = pricing.reported_cost(reported)
            known = bool(
                actual is not None
                or (
                    reported
                    and all(
                        reported.get(key) is not None
                        for key in ("prompt_tokens", "completion_tokens")
                    )
                )
            )
            cost = (
                actual
                if actual is not None
                else (
                    reported["prompt_tokens"] * pricing.input_per_million
                    + reported["completion_tokens"] * pricing.output_per_million
                )
                / 1_000_000
                if known
                else 0
                if rejected
                else reservation
            )
            with sessions.begin() as db:
                account = db.get(ConversationAccount, project_id, with_for_update=True)
                usage = dict(account.usage or {})
                usage.update(
                    requests=usage.get("requests", 0) + 1,
                    pending_requests=max(0, usage.get("pending_requests", 1) - 1),
                    reserved_cost=max(0, usage.get("reserved_cost", reservation) - reservation),
                    accounted_cost=usage.get("accounted_cost", 0) + cost,
                    unknown_requests=usage.get("unknown_requests", 0)
                    + int(not known and not rejected),
                    prompt_tokens=usage.get("prompt_tokens", 0)
                    + ((reported or {}).get("prompt_tokens") or 0),
                    completion_tokens=usage.get("completion_tokens", 0)
                    + ((reported or {}).get("completion_tokens") or 0),
                )
                account.usage = usage
                db.add(
                    Event(
                        project_id=project_id,
                        kind="conversation_usage",
                        payload={
                            "cost": cost,
                            "currency": pricing.currency,
                            "known": known,
                            "message_id": message_id,
                            "purpose": request_purpose.get(),
                            "pricing": pricing.model_dump(),
                        },
                    )
                )

    client.complete = complete
    return client


def recover_reservations(usage):
    result = dict(usage or {})
    if result.get("pending_requests"):
        result["requests"] = result.get("requests", 0) + result["pending_requests"]
        result["accounted_cost"] = result.get("accounted_cost", 0) + result.get("reserved_cost", 0)
        result["unknown_requests"] = result.get("unknown_requests", 0) + result["pending_requests"]
        result.update(pending_requests=0, reserved_cost=0)
    return result
