"""Bounded document operations and exact, versioned citations."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

PIPELINE = "pypdf6.21.3-docx1.1.2/split1.1.3/struct2400-160-v1"
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_TEXT = 2_000_000
MAX_CHUNKS = 5000
FORMATS = {".md", ".txt", ".pdf", ".docx"}


class DocumentSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version_id: UUID
    chunk_id: UUID
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=1, max_length=2000)


class SearchKnowledge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=500)
    kind: Literal["documents", "memory", "results", "all"] = "all"
    version_id: UUID | None = None
    include_historical: bool = False
    limit: int = Field(default=5, ge=1, le=10)


class ReadSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(min_length=1, max_length=240)
    neighbors: int = Field(default=0, ge=0, le=2)


class DocumentControl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


class RetryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: UUID
