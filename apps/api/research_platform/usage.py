"""Conservative per-run admission. No output-token cap is sent to the provider."""

import asyncio

from pydantic import BaseModel, Field, ConfigDict
from research_llm import APIUnavailable, ResponseProblem
from .database import Run, Session


class Pricing(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    currency: str = Field(min_length=1, max_length=12)
    input_per_million: float = Field(ge=0)
    output_per_million: float = Field(ge=0)
    max_input_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)

    @property
    def reservation(self):
        return (
            1.1
            * (
                self.max_input_tokens * self.input_per_million
                + self.max_output_tokens * self.output_per_million
            )
            / 1_000_000
        )


def tracked(client, run_id):
    original = client.complete

    async def complete(*args, **kwargs):
        with Session() as db:
            run = db.get(Run, run_id)
            pricing = (
                Pricing.model_validate(run.connection["pricing"])
                if run.connection.get("pricing")
                else None
            )
            budget = run.policy["limits"].get("api_budget")
        reservation = pricing.reservation if pricing else 0.0
        while True:
            with Session.begin() as db:
                run = db.get(Run, run_id, with_for_update=True)
                usage = dict(run.usage or {})
                pending = usage.get("pending_requests", 0)
                if (
                    budget is None
                    or usage.get("accounted_cost", 0.0) + (pending + 1) * reservation
                    <= budget
                ):
                    usage["pending_requests"] = pending + 1
                    usage["reserved_cost"] = (pending + 1) * reservation
                    usage.setdefault("requests", 0)
                    usage.setdefault(
                        "currency", pricing.currency if pricing else "unavailable"
                    )
                    run.usage = usage
                    break
                if not pending:
                    raise APIUnavailable(
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
            rejected = isinstance(error, APIUnavailable) or getattr(
                error, "status_code", None
            ) in (400, 401, 402, 403, 404, 422, 429)
            raise
        finally:
            if (
                reported
                and pricing
                and all(
                    reported.get(k) is not None
                    for k in ("prompt_tokens", "completion_tokens")
                )
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
                ledger["pending_requests"] = max(
                    0, ledger.get("pending_requests", 1) - 1
                )
                ledger["reserved_cost"] = ledger["pending_requests"] * reservation
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
