"""Vendor-neutral result encoding and capability names."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from atlas_contracts.analytics import AnalyticsError, QueryResult
from pydantic import JsonValue, TypeAdapter

JSON_VALUE: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)  # type: ignore[arg-type]
OPERATIONS = (
    "execute_readonly_query",
    "get_schema",
    "get_table_metadata",
    "get_column_statistics",
    "get_query_plan",
    "get_table_dependencies",
)


def encode(value: Any) -> JsonValue:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, memoryview)):
        return bytes(value).hex()
    if isinstance(value, Decimal):
        return str(value)
    return JSON_VALUE.validate_python(value)


def bounded_result(columns: list[str], rows: list[Any], limit: int) -> QueryResult:
    values = [[encode(value) for value in row] for row in rows[:limit]]
    result = QueryResult(
        columns=columns, rows=values, row_count=len(values), truncated=len(rows) > limit
    )
    if len(result.model_dump_json().encode()) > 1_000_000:
        raise AnalyticsError("result_too_large", "Result exceeds the one-megabyte response budget")
    return result
