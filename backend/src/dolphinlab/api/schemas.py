"""Validated HTTP request contracts."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from dolphinlab.domain.research import GoalSpec


class UpdateSettings(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(default="", max_length=1000)
    model: str = Field(default="", max_length=200)
    api_key: SecretStr | None = None
    clear_key: bool = False
    pricing: dict = Field(default_factory=dict)

    @field_validator("display_name")
    @classmethod
    def nonempty_name(cls, value):
        if not value.strip():
            raise ValueError("Укажи имя")
        return value.strip()

    @field_validator("base_url")
    @classmethod
    def connection_url(cls, value):
        from urllib.parse import urlsplit

        value = value.strip().rstrip("/")
        if not value:
            return value
        try:
            parts = urlsplit(value)
            _ = parts.port
        except ValueError:
            raise ValueError("Укажи корректный Base URL")
        if (
            parts.scheme not in ("https", "http")
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError("Нужен HTTP(S) Base URL без ключей, логина и параметров")
        return value


class CreateProject(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=20000)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        if not value.strip():
            raise ValueError("Укажи название проекта")
        return value.strip()


class CreateMessage(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    directive: bool = False
    client_id: UUID | None = None
    intent: Literal["auto", "question", "instruction", "followup"] | None = None

    @property
    def resolved_intent(self):
        return self.intent or ("instruction" if self.directive else "auto")

    @field_validator("content")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Сообщение пустое")
        return value.strip()


class EditProject(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=20000)
    archived: bool = False
    expected_goal_revision: int | None = Field(default=None, ge=1)


class ControlRun(BaseModel):
    action: str


class UpdateRunBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    api_budget: float = Field(gt=0)
    expected_revision: int = Field(ge=0)
    client_id: UUID


class CreateGoal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: UUID
    expected_revision: int = Field(ge=0)
    goal: GoalSpec


class CreateIteration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: UUID
    expected_sequence: int = Field(ge=0)
    goal_revision_id: UUID
    parent_iteration_id: UUID | None = None


class StartRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: UUID | None = None
    iteration_id: UUID | None = None
