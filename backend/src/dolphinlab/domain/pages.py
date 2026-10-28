"""Code-free page composition. A page never grants a scientific assessment."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_serializer, model_validator


class PageBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    run_id: UUID
    result_id: str = Field(min_length=1, max_length=120)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    caption: str = Field(default="", max_length=1000)
    width: Literal["full", "half"] = "full"
    required: bool = True
    filter_text: str = Field(default="", max_length=200)
    origin_revision_id: UUID | None = None


class PageSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    title: str = Field(min_length=1, max_length=200)
    blocks: list[PageBlock] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def unique_blocks(self):
        if not self.title.strip():
            raise ValueError("Page title cannot be blank")
        if len({b.id for b in self.blocks}) != len(self.blocks):
            raise ValueError("Page block IDs must be unique")
        return self


class PlotView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["bar", "line", "scatter"]
    x: str = Field(min_length=1, max_length=120)
    y: str = Field(min_length=1, max_length=120)
    x_label: str = Field(min_length=1, max_length=200)
    y_label: str = Field(min_length=1, max_length=200)


class BlockPresentation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    heading: str = Field(default="", max_length=200)
    view: Literal["auto", "summary", "chart"] = "auto"
    emphasis: Literal["normal", "hero", "quiet"] = "normal"
    chart: PlotView | None = None

    @model_validator(mode="after")
    def chart_required(self):
        if (self.view == "chart") != (self.chart is not None):
            raise ValueError("A chart view requires chart axes")
        return self


class EditorialBlock(PageBlock):
    presentation: BlockPresentation | None = None

    @model_serializer(mode="wrap")
    def omit_empty_presentation(self, handler):
        value = handler(self)
        if self.presentation is None:
            value.pop("presentation", None)
        return value


class EditorialPage(PageSpec):
    schema_version: Literal[2] = 2
    lead: str = Field(default="", max_length=4000)
    layout: Literal["story", "dashboard"] = "story"
    blocks: list[EditorialBlock] = Field(default_factory=list, max_length=20)


def parse_page(value):
    return TypeAdapter(PageSpec | EditorialPage).validate_python(value)


class PageSave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: UUID
    expected_revision: int = Field(ge=0)
    kind: Literal["main", "team"] = "main"
    run_id: UUID | None = None
    team_id: str | None = Field(default=None, min_length=1, max_length=120)
    page: PageSpec

    @model_validator(mode="after")
    def valid_owner(self):
        if (self.kind == "team") != (self.run_id is not None and self.team_id is not None):
            raise ValueError("A team page requires run_id and team_id")
        if self.kind == "main" and (self.run_id or self.team_id):
            raise ValueError("The main page belongs to the project")
        return self


class PagePublish(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: UUID
    expected_revision: int = Field(ge=1)
    expected_feedback_revision: int = Field(ge=0)
    revision_id: UUID
    outcome: Literal["complete", "inconclusive"] = "complete"


class EditorialSave(PageSave):
    page: EditorialPage


@dataclass(frozen=True)
class EditorIdentity:
    """Internal capability; never accepted from HTTP/agent tool arguments."""

    job_id: str


class ComposePage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["main", "team"]
    team_id: str | None = None
    run_id: UUID | None = None
    page: EditorialPage
    outcome: Literal["complete", "inconclusive"] = "complete"


class EditorPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pages: list[ComposePage] = Field(min_length=1, max_length=13)


class ComposeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: UUID
    instruction: str = Field(default="", max_length=4000)


class StartEditor(ComposeRequest):
    client_id: UUID


class PageFeedbackChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: UUID
    expected_revision: int = Field(ge=0)
    action: Literal["pin", "unpin", "comment", "delete_comment"]
    block_id: UUID | None = None
    comment_id: UUID | None = None
    text: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def complete_action(self):
        if self.action in {"pin", "unpin"} and not self.block_id:
            raise ValueError("Select a block")
        if self.action in {"comment", "delete_comment"} and not self.comment_id:
            raise ValueError("A comment requires an ID")
        if self.action == "comment" and not self.text.strip():
            raise ValueError("Comment cannot be blank")
        return self
