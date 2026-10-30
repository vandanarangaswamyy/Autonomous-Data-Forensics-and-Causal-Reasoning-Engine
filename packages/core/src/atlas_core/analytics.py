from typing import Protocol, runtime_checkable

from atlas_contracts.analytics import (
    BackendCapabilities,
    ColumnStatistics,
    Dependencies,
    QueryPlan,
    QueryRequest,
    QueryResult,
    SchemaSnapshot,
    TableMetadata,
    TableRef,
)


@runtime_checkable
class AnalyticsBackend(Protocol):
    async def execute_readonly_query(self, request: QueryRequest) -> QueryResult: ...
    async def get_schema(self) -> SchemaSnapshot: ...
    async def get_table_metadata(self, table: TableRef) -> TableMetadata: ...
    async def get_column_statistics(self, table: TableRef, column: str) -> ColumnStatistics: ...
    async def get_query_plan(self, request: QueryRequest) -> QueryPlan: ...
    async def get_table_dependencies(self, table: TableRef) -> Dependencies: ...
    async def get_supported_capabilities(self) -> BackendCapabilities: ...
