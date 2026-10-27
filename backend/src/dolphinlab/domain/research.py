"""Versioned research intent, independent of storage and model clients."""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class GoalSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20000)
    ]
    expected_outputs: tuple[Text, ...] = Field(default=(), max_length=30)
    definitions: dict[Text, Text] = Field(default_factory=dict, max_length=50)
    constraints: tuple[Text, ...] = Field(default=(), max_length=30)
    open_questions: tuple[Text, ...] = Field(default=(), max_length=30)

    def task_text(self) -> str:
        parts = [self.question]
        for title, values in (
            ("Expected results", self.expected_outputs),
            ("Definitions", [f"{key}: {value}" for key, value in self.definitions.items()]),
            ("Constraints", self.constraints),
            ("Open questions (not established facts)", self.open_questions),
        ):
            if values:
                parts.append(f"## {title}\n" + "\n".join(f"- {value}" for value in values))
        return "\n\n".join(parts)


def request_fingerprint(body: BaseModel) -> str:
    payload = body.model_dump(mode="json", exclude={"client_id"})
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
