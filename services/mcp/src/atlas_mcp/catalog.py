from collections.abc import Awaitable, Callable
from typing import Any, cast

from atlas_contracts.analytics import ColumnStatistics, QueryPlan, QueryResult, TableMetadata
from atlas_contracts.tools import (
    DocumentInput,
    DocumentResult,
    IncidentInput,
    IncidentResult,
    LineageInput,
    LineageResult,
    PipelineInput,
    PipelineResult,
    ProfileInput,
    SQLInput,
    TableInput,
    ToolInput,
    ToolReply,
)
from pydantic import BaseModel, create_model

from atlas_mcp.gateway import Gateway, ToolSpec
from atlas_mcp.security import Permission
from atlas_mcp.services import EvidenceServices

# Server-advertised annotations never decide these application permissions.
TOOL_PERMISSIONS = {
    "get_table_schema": Permission.SCHEMA,
    "execute_readonly_sql": Permission.QUERY,
    "profile_column": Permission.PROFILE,
    "get_query_plan": Permission.PLAN,
    "trace_upstream_lineage": Permission.LINEAGE,
    "trace_downstream_lineage": Permission.LINEAGE,
    "get_pipeline_execution": Permission.TELEMETRY,
    "search_incident_history": Permission.TELEMETRY,
    "search_technical_documents": Permission.KNOWLEDGE,
}
GROUPS = {
    "analytics": tuple(TOOL_PERMISSIONS)[:4],
    "lineage": tuple(TOOL_PERMISSIONS)[4:6],
    "telemetry": tuple(TOOL_PERMISSIONS)[6:8],
    "knowledge": tuple(TOOL_PERMISSIONS)[8:],
}


def register_tools(gateway: Gateway, group: str, services: EvidenceServices) -> None:
    def add(
        name: str,
        input_model: type[ToolInput],
        output_model: type[BaseModel],
        handler: Callable[[ToolInput], Awaitable[BaseModel]],
        rows: int = 1000,
    ) -> None:
        reply_model = create_model(
            name + "Reply", __base__=ToolReply, result=(cast(Any, output_model | None), None)
        )

        async def execute(arguments: dict[str, Any]) -> dict[str, Any]:
            request = input_model.model_validate(arguments)
            response = output_model.model_validate(await handler(request))
            return response.model_dump(mode="json")

        gateway.register(
            ToolSpec(
                name=name,
                permission=TOOL_PERMISSIONS[name],
                input_schema=input_model.model_json_schema(),
                output_schema=reply_model.model_json_schema(),
                execute=execute,
                row_reservation=rows,
            )
        )

    async def schema(request: ToolInput) -> BaseModel:
        assert isinstance(request, TableInput)
        return await services.backend.get_table_metadata(request.table)

    async def query(request: ToolInput) -> BaseModel:
        assert isinstance(request, SQLInput)
        return await services.backend.execute_readonly_query(
            request.query.model_copy(
                update={"timeout_ms": min(request.query.timeout_ms, request.context.timeout_ms)}
            )
        )

    async def profile(request: ToolInput) -> BaseModel:
        assert isinstance(request, ProfileInput)
        return await services.backend.get_column_statistics(request.table, request.column)

    async def plan(request: ToolInput) -> BaseModel:
        assert isinstance(request, SQLInput)
        return await services.backend.get_query_plan(
            request.query.model_copy(
                update={"timeout_ms": min(request.query.timeout_ms, request.context.timeout_ms)}
            )
        )

    async def upstream(request: ToolInput) -> BaseModel:
        assert isinstance(request, LineageInput)
        return await services.lineage(request, True)

    async def downstream(request: ToolInput) -> BaseModel:
        assert isinstance(request, LineageInput)
        return await services.lineage(request, False)

    async def pipeline(request: ToolInput) -> BaseModel:
        assert isinstance(request, PipelineInput)
        return await services.pipeline(request)

    async def incidents(request: ToolInput) -> BaseModel:
        assert isinstance(request, IncidentInput)
        return await services.incidents(request)

    async def documents(request: ToolInput) -> BaseModel:
        assert isinstance(request, DocumentInput)
        return await services.knowledge.search(request.query, request.limit)

    definitions: dict[
        str,
        tuple[type[ToolInput], type[BaseModel], Callable[[ToolInput], Awaitable[BaseModel]], int],
    ] = {
        "get_table_schema": (TableInput, TableMetadata, schema, 100),
        "execute_readonly_sql": (SQLInput, QueryResult, query, 1000),
        "profile_column": (ProfileInput, ColumnStatistics, profile, 1),
        "get_query_plan": (SQLInput, QueryPlan, plan, 1),
        "trace_upstream_lineage": (LineageInput, LineageResult, upstream, 300),
        "trace_downstream_lineage": (LineageInput, LineageResult, downstream, 300),
        "get_pipeline_execution": (PipelineInput, PipelineResult, pipeline, 100),
        "search_incident_history": (IncidentInput, IncidentResult, incidents, 100),
        "search_technical_documents": (DocumentInput, DocumentResult, documents, 20),
    }
    for name in GROUPS[group]:
        input_model, output_model, handler, rows = definitions[name]
        add(name, input_model, output_model, handler, rows)
