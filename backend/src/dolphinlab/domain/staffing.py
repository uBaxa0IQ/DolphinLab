"""Staffing contracts; a role description cannot grant extra permissions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Specialist = Literal["ml_engineer", "data_engineer", "data_analyst", "evaluator", "report_editor"]


class RoleProfileRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    role: Literal[
        "tech_lead",
        "team_lead",
        "ml_engineer",
        "data_engineer",
        "data_analyst",
        "evaluator",
        "report_editor",
    ]
    revision: Literal[1, 2, 3]
    prompt: str = Field(min_length=1)
    tools: list[dict]


class StaffMember(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Specialist
    assignment: str = Field(min_length=1, max_length=12000)


class TeamComposition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    members: list[StaffMember] = Field(min_length=1, max_length=12)
    coordinator: bool = False
    reason: str = Field(min_length=1, max_length=2000)


class Handover(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=4000)
    results: list[str] = Field(default_factory=list, max_length=30)
    evidence: list[str] = Field(default_factory=list, max_length=30)
    unsuccessful_approaches: list[str] = Field(default_factory=list, max_length=30)
    open_questions: list[str] = Field(default_factory=list, max_length=30)
    next_actions: list[str] = Field(default_factory=list, max_length=30)
