"""Storage-neutral analytical contracts. Decimal results are encoded as strings."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

Scalar = str | int | float | bool | None


class AnalyticsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class QueryRequest(AnalyticsModel):
    sql: str = Field(min_length=1, max_length=16000)
    parameters: dict[str, Scalar] = Field(default_factory=dict, max_length=100)
    timeout_ms: int = Field(default=3000, ge=10, le=30000)
    row_limit: int = Field(default=100, ge=1, le=1000)


class QueryResult(AnalyticsModel):
    columns: list[str]
    rows: list[list[JsonValue]]
    truncated: bool = False
    row_count: int = Field(ge=0)


class TableRef(AnalyticsModel):
    schema_name: str = Field(default="public", min_length=1, max_length=128)
    table: str = Field(min_length=1, max_length=128)


class ColumnMetadata(AnalyticsModel):
    name: str
    data_type: str
    nullable: bool
    ordinal: int


class TableMetadata(AnalyticsModel):
    reference: TableRef
    columns: list[ColumnMetadata]
    primary_key: list[str] | None = None
    indexes: list[str] | None = None
    estimated_rows: int | None = None


class SchemaSnapshot(AnalyticsModel):
    tables: list[TableMetadata]


class ColumnStatistics(AnalyticsModel):
    reference: TableRef
    column: str
    total_rows: int
    null_rows: int
    distinct_values: int
    minimum: JsonValue = None
    maximum: JsonValue = None
    numeric_mean: str | None = None


class QueryPlan(AnalyticsModel):
    dialect: str
    plan: JsonValue
    executes_query: Literal[False] = False


class TableDependency(AnalyticsModel):
    source: TableRef
    destination: TableRef
    source_column: str
    destination_column: str
    relationship: Literal["foreign_key"] = "foreign_key"


class Dependencies(AnalyticsModel):
    edges: list[TableDependency]


class Capability(AnalyticsModel):
    implemented: bool
    live_verified: bool
    detail: str


class BackendCapabilities(AnalyticsModel):
    backend: str
    dialect: str
    capabilities: dict[str, Capability]


class AnalyticsError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable
