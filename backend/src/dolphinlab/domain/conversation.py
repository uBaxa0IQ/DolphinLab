"""Contracts for bounded data questions and attributed working memory."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dolphinlab.domain.research import GoalSpec


class MessageRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: Literal["question", "instruction", "followup", "clarification"]
    reason: str = Field(min_length=1, max_length=1000)


class FilterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str = Field(min_length=1, max_length=200)
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "is_null", "not_null"] = "eq"
    value: str | None = Field(default=None, max_length=4000)


class DataQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_id: UUID
    operation: Literal["schema", "preview", "aggregate"]
    delimiter: Literal[",", ";", "\t"] | None = None
    metric: Literal["count", "sum", "mean", "min", "max"] = "count"
    column: str | None = Field(default=None, min_length=1, max_length=200)
    group_by: list[str] = Field(default_factory=list, max_length=3)
    filters: list[FilterSpec] = Field(default_factory=list, max_length=10)
    limit: int = Field(default=10, ge=1, le=30)

    @model_validator(mode="after")
    def validate_metric(self):
        if self.operation == "aggregate" and self.metric != "count" and not self.column:
            raise ValueError("A numeric metric requires a column")
        return self


class MemoryNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)
    kind: Literal["user_statement", "hypothesis", "observation", "open_question"]
    scope: Literal["goal", "project"] = "goal"
    source_quote: str | None = Field(default=None, min_length=1, max_length=4000)
    query_id: UUID | None = None
    supersedes_id: UUID | None = None


class FollowupSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: GoalSpec

    @field_validator("goal", mode="before")
    @classmethod
    def structured_goal(cls, value):
        # Compatible providers sometimes encode a nested tool object as a JSON string.
        # Decode only this structured field, then apply every GoalSpec validation normally.
        if isinstance(value, str):
            import json

            value = json.loads(value)
        return value


class ResearchInstruction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str = Field(min_length=1, max_length=20000)
