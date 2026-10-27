"""Typed project policy; preferences and execution ceilings have different owners."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Strategy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exploration: Literal["focused", "balanced", "exploratory"] = "balanced"
    guidance: str = Field(default="", max_length=4000)


class RuntimeLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    max_teams: int = Field(default=6, ge=1, le=12)
    parallel_jobs: int = Field(default=2, ge=1, le=32)
    parallel_model_calls: int = Field(default=6, ge=1, le=32)
    cpu_total: int = Field(default=8, ge=1, le=64)
    ram_gb: int = Field(default=28, ge=1, le=256)
    duration_minutes: int = Field(default=180, ge=5, le=1440)
    api_budget: float | None = Field(default=None, gt=0)


class ProjectPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(default=0, ge=0)
    strategy: Strategy = Field(default_factory=Strategy)
    limits: RuntimeLimits = Field(default_factory=RuntimeLimits)


def context_message(raw: dict):
    policy = ProjectPolicy.model_validate(raw)
    preference = {
        "focused": "Prefer developing promising evidence-backed approaches; open a new direction when there is a concrete reason.",
        "balanced": "Balance improvements to the current result with genuinely different hypotheses; reconsider using evidence and time remaining.",
        "exploratory": "Actively seek distinct hypotheses and independent directions; retire unproductive directions and share useful findings.",
    }[policy.strategy.exploration]
    return {
        "role": "user",
        "content": (
            f"USER PROJECT POLICY, revision {policy.revision}. Snapshot for this run.\n"
            f"Research preference (guidance, not a fixed experimental pipeline): {preference}\n"
            "The tech lead chooses the actual team count and research allocation below the ceilings. "
            "Validation quality is required in every exploration mode. "
            "Runtime limits belong to the scheduler, not to manager discretion. Configured ceilings are not proof of available host resources.\n"
            f"Configured execution ceilings: {policy.limits.model_dump_json()}\n"
            f"Additional user guidance: {policy.strategy.guidance or '(none)'}"
        ),
    }
