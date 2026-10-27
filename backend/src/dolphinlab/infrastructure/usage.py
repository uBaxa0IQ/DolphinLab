"""Conservative per-run admission, with optional enforced request ceilings."""

import asyncio
import math

from pydantic import BaseModel, ConfigDict, Field

from dolphinlab.infrastructure.llm import (
    APIUnavailable,
    ModelClient,
    RequestBudgetExceeded,
    ResponseProblem,
)


def run_budget(run):
    return (run.usage or {}).get("budget_limit", run.policy["limits"].get("api_budget"))


class Pricing(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    currency: str = Field(min_length=1, max_length=12)
    input_per_million: float = Field(ge=0)
    output_per_million: float = Field(ge=0)
    max_input_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    enforce_request_limits: bool = False
    provider_cost_currency: str | None = Field(default=None, min_length=1, max_length=12)

    def reported_cost(self, usage):
        value = (usage or {}).get("cost")
        if (
            self.provider_cost_currency == self.currency
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value >= 0
        ):
            return float(value)
        return None

    @property
    def reservation(self):
        return self.reserve_input(self.max_input_tokens)

    def reserve_input(self, tokens):
        return (
            1.1
            * (tokens * self.input_per_million + self.max_output_tokens * self.output_per_million)
            / 1_000_000
        )

    def reservation_for(self, messages, tools=None):
        if not self.enforce_request_limits:
            return self.reservation
        return self.reserve_input(
            min(self.max_input_tokens, ModelClient.request_input_units(messages, tools))
        )


def tracked(client, run_id):
    from dolphinlab.infrastructure.database import Run, Session

    original = client.complete

    async def complete(*args, **kwargs):
        with Session() as db:
            run = db.get(Run, run_id)
            pricing = (
                Pricing.model_validate(run.connection["pricing"])
                if run.connection.get("pricing")
                else None
            )
        reservation = (
            pricing.reservation_for(
                args[0] if args else kwargs.get("messages", []),
                args[1] if len(args) > 1 else kwargs.get("tools"),
            )
            if pricing
            else 0.0
        )
        while True:
            with Session.begin() as db:
                run = db.get(Run, run_id, with_for_update=True)
                budget = run_budget(run)
                usage = dict(run.usage or {})
                pending = usage.get("pending_requests", 0)
                if (
                    budget is None
                    or usage.get("accounted_cost", 0.0)
                    + usage.get("reserved_cost", 0.0)
                    + reservation
                    <= budget
                ):
                    usage["pending_requests"] = pending + 1
                    usage["reserved_cost"] = usage.get("reserved_cost", 0.0) + reservation
                    usage.setdefault("requests", 0)
                    usage.setdefault("currency", pricing.currency if pricing else "unavailable")
                    run.usage = usage
                    break
                if not pending:
                    raise RequestBudgetExceeded(
                        "Project API budget has insufficient headroom for a complete provider response"
                    )
            await asyncio.sleep(0.2)
        reported = None
        rejected = False
        try:
            response, reported = await original(*args, **kwargs)
            return response, reported
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
            actual = pricing.reported_cost(reported) if pricing else None
            if actual is not None:
                cost, currency = actual, pricing.currency
            elif (
                reported
                and pricing
                and all(reported.get(k) is not None for k in ("prompt_tokens", "completion_tokens"))
            ):
                cost = (
                    reported["prompt_tokens"] * pricing.input_per_million
                    + reported["completion_tokens"] * pricing.output_per_million
                ) / 1_000_000
                currency = pricing.currency
            elif reported and reported.get("cost") is not None and budget is None:
                cost, currency = (
                    float(reported["cost"]),
                    reported.get("currency", "provider units"),
                )
            else:
                cost, currency = (
                    (0.0 if rejected else reservation),
                    pricing.currency if pricing else "unavailable",
                )
            with Session.begin() as db:
                run = db.get(Run, run_id, with_for_update=True)
                ledger = dict(run.usage or {})
                ledger["pending_requests"] = max(0, ledger.get("pending_requests", 1) - 1)
                ledger["reserved_cost"] = max(
                    0.0, ledger.get("reserved_cost", reservation) - reservation
                )
                ledger["requests"] = ledger.get("requests", 0) + 1
                ledger["prompt_tokens"] = ledger.get("prompt_tokens", 0) + (
                    (reported or {}).get("prompt_tokens") or 0
                )
                ledger["completion_tokens"] = ledger.get("completion_tokens", 0) + (
                    (reported or {}).get("completion_tokens") or 0
                )
                ledger["accounted_cost"] = ledger.get("accounted_cost", 0.0) + cost
                ledger["currency"] = currency
                ledger["unknown_requests"] = ledger.get("unknown_requests", 0) + int(
                    not reported and not rejected
                )
                run.usage = ledger

    client.complete = complete
    return client
