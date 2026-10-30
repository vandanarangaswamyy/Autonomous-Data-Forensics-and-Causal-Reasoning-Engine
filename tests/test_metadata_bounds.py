from typing import Any

import pytest
from atlas_contracts.analytics import AnalyticsError, QueryResult, TableRef
from atlas_database.analytics.postgres import PostgreSQLAnalyticsBackend


@pytest.mark.parametrize("operation", ["columns", "indexes", "dependencies"])
async def test_truncated_metadata_is_not_reported_as_complete(
    operation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = PostgreSQLAnalyticsBackend(
        "postgresql://observer@127.0.0.1/unconfigured", frozenset({"orders"})
    )

    async def execute(sql: str, params: Any, **kwargs: Any) -> QueryResult:
        columns = "information_schema.columns" in sql
        indexes = "pg_indexes" in sql
        dependency = "pg_constraint" in sql
        rows: list[list[Any]] = [["id", "integer", "NO", 1]] if columns else []
        truncated = (
            (operation == "columns" and columns)
            or (operation == "indexes" and indexes)
            or (operation == "dependencies" and dependency)
        )
        return QueryResult(columns=[], rows=rows, row_count=len(rows), truncated=truncated)

    monkeypatch.setattr(backend, "_execute", execute)
    with pytest.raises(AnalyticsError) as error:
        if operation == "dependencies":
            await backend.get_table_dependencies(TableRef(table="orders"))
        else:
            await backend.get_table_metadata(TableRef(table="orders"))
    assert error.value.code == "result_too_large"
