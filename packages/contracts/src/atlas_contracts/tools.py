from typing import Literal, Self
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from atlas_contracts.analytics import AnalyticsModel, QueryRequest, TableRef


class RequestContext(AnalyticsModel):
    investigation_id: UUID
    request_id: UUID
    timeout_ms: int = Field(default=5000, ge=10, le=30000)


class ToolInput(AnalyticsModel):
    context: RequestContext


class TableInput(ToolInput):
    table: TableRef


class SQLInput(ToolInput):
    query: QueryRequest


class ProfileInput(TableInput):
    column: str = Field(min_length=1, max_length=128)


class LineageInput(ToolInput):
    dataset: str = Field(min_length=1, max_length=128)
    column: str | None = Field(default=None, min_length=1, max_length=128)
    max_depth: int = Field(default=5, ge=1, le=10)


class PipelineInput(ToolInput):
    pipeline_id: str = Field(min_length=1, max_length=80)
    execution_id: UUID | None = None
    limit: int = Field(default=10, ge=1, le=100)


class IncidentInput(ToolInput):
    query: str = Field(min_length=1, max_length=200)
    severity: Literal["info", "warning", "error"] | None = None
    limit: int = Field(default=20, ge=1, le=100)


class DocumentInput(ToolInput):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=20)


class ToolError(AnalyticsModel):
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=500)
    retryable: bool = False


class ToolReply(AnalyticsModel):
    status: Literal["ok", "error"]
    investigation_id: UUID | None
    request_id: UUID
    trace_id: UUID
    result: dict[str, JsonValue] | None = None
    error: ToolError | None = None

    @model_validator(mode="after")
    def consistent_status(self) -> Self:
        if self.status == "ok" and (
            self.result is None or self.error is not None or self.investigation_id is None
        ):
            raise ValueError("Successful replies require correlated results and no error")
        if self.status == "error" and (self.result is not None or self.error is None):
            raise ValueError("Failed replies require an error and no result")
        return self


class LineageEdge(AnalyticsModel):
    pipeline_id: str
    source_dataset: str
    destination_dataset: str
    source_column: str
    destination_column: str
    expression: str


class LineageResult(AnalyticsModel):
    edges: list[LineageEdge]
    truncated: bool


class PipelineExecution(AnalyticsModel):
    id: UUID
    pipeline_id: str
    schema_version_id: str
    started_at: str
    finished_at: str
    status: str
    input_rows: int
    output_rows: int
    quality: dict[str, JsonValue]


class PipelineResult(AnalyticsModel):
    executions: list[PipelineExecution]
    truncated: bool


class Incident(AnalyticsModel):
    id: str
    occurred_at: str
    pipeline_id: str | None
    severity: str
    code: str
    details: dict[str, JsonValue]


class IncidentResult(AnalyticsModel):
    incidents: list[Incident]
    truncated: bool


class DocumentMatch(AnalyticsModel):
    document_id: str
    title: str
    source_uri: str
    excerpt: str
    score: float


class DocumentResult(AnalyticsModel):
    matches: list[DocumentMatch]
    retrieval_method: Literal["keyword"] = "keyword"
