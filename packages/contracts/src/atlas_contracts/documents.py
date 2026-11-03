"""Source ingestion contracts. Provenance is observational, never a causal assertion."""

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Self
from urllib.parse import urlsplit
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DOCUMENT_NAMESPACE = UUID("8a572983-b563-4ce4-b7e8-7837a48fc6b5")
MAX_SOURCE_BYTES = 1_000_000


class DocumentKind(StrEnum):
    TECHNICAL = "technical"
    SQL_SCHEMA = "sql_schema"
    SQL_TRANSFORMATION = "sql_transformation"
    PIPELINE_LOG = "pipeline_log"
    INCIDENT = "incident"
    QUALITY_ALERT = "quality_alert"
    MIGRATION = "migration"
    TELEMETRY = "telemetry"


class IngestionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceDescriptor(IngestionModel):
    collection: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    source_uri: str = Field(min_length=1, max_length=1000)
    source_version: str = Field(min_length=1, max_length=200)
    kind: DocumentKind
    media_type: str = Field(pattern=r"^(text/plain|text/markdown|application/sql)$")
    observed_at: datetime

    @field_validator("source_uri")
    @classmethod
    def safe_locator(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            not parsed.scheme
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or any(ord(c) < 33 for c in value)
        ):
            raise ValueError("Use an absolute source locator without credentials/query/fragment")
        return value

    @field_validator("observed_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Source observation timestamp must be timezone aware")
        return value

    @property
    def document_id(self) -> UUID:
        return uuid5(DOCUMENT_NAMESPACE, json.dumps([self.collection, self.source_uri]))


class ChunkConfig(IngestionModel):
    strategy: str = Field(default="recursive", pattern=r"^(recursive|markdown)$")
    max_chars: int = Field(default=1000, ge=64, le=8000)
    algorithm: str = Field(default="offset-v1", pattern=r"^offset-v1$")

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class SourceSubmission(IngestionModel):
    descriptor: SourceDescriptor
    content: bytes = Field(min_length=1, max_length=MAX_SOURCE_BYTES, repr=False)
    chunking: ChunkConfig = Field(default_factory=ChunkConfig)

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    @property
    def revision_sha256(self) -> str:
        return hashlib.sha256(
            json.dumps(
                [
                    self.descriptor.model_dump(mode="json"),
                    self.content_sha256,
                    self.chunking.model_dump(mode="json"),
                ],
                sort_keys=True,
            ).encode()
        ).hexdigest()


class Chunk(IngestionModel):
    id: UUID
    document_id: UUID
    version: int = Field(ge=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str
    section_path: list[str]
    content_sha256: str
    processor_sha256: str

    @model_validator(mode="after")
    def offsets(self) -> Self:
        if self.end <= self.start or len(self.text) != self.end - self.start:
            raise ValueError("Chunk text and source offsets disagree")
        return self


class IngestionReceipt(IngestionModel):
    document_id: UUID
    version: int
    job_id: UUID
    deduplicated: bool


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRY = "retry"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class Job(IngestionModel):
    id: UUID
    document_id: UUID
    version: int
    generation: int
    token: UUID
    attempts: int
    descriptor: SourceDescriptor
    content: bytes = Field(repr=False)
    chunking: ChunkConfig


class IngestionError(Exception):
    """Codes/messages are fixed by application code, never raw exception strings."""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable
