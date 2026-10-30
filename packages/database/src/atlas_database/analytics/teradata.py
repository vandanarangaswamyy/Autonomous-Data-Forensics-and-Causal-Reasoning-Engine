"""Official teradatasql adapter. Live integration and effective-role inspection are unverified."""

import asyncio
import importlib
import math
import threading
from typing import Any

from atlas_contracts.analytics import (
    AnalyticsError,
    BackendCapabilities,
    Capability,
    ColumnMetadata,
    ColumnStatistics,
    Dependencies,
    QueryPlan,
    QueryRequest,
    QueryResult,
    SchemaSnapshot,
    TableMetadata,
    TableRef,
)
from pydantic import SecretStr

from atlas_database.analytics.common import OPERATIONS, bounded_result
from atlas_database.analytics.policy import SQLPolicy


def _load_driver() -> Any:
    return importlib.import_module("teradatasql")


class TeradataAnalyticsBackend:
    def __init__(
        self,
        host: str,
        user: str,
        password: SecretStr,
        database: str,
        tables: frozenset[str],
        *,
        readonly_role_attested: bool = False,
    ) -> None:
        self._host, self._user, self._password = host, user, password
        self.policy = SQLPolicy("teradata", database, tables)
        # Effective Teradata role grants cannot be claimed verified without a live installation.
        # Explicit operator attestation is required; no credentials means no fake connection.
        self._attested = readonly_role_attested

    async def _execute(
        self, sql: str, params: Any, timeout_ms: int = 3000, limit: int = 1000
    ) -> QueryResult:
        if not self._attested:
            raise AnalyticsError(
                "readonly_role_unverified",
                "A Teradata administrator must provision and attest a SELECT-only account",
            )
        try:
            driver = _load_driver()
        except ImportError as exc:
            raise AnalyticsError(
                "driver_unavailable", "Install the optional teradata dependency"
            ) from exc
        connection_holder: list[Any] = []
        cancel_requested = threading.Event()

        def work() -> QueryResult:
            connection = driver.connect(
                host=self._host,
                user=self._user,
                password=self._password.get_secret_value(),
                database=self.policy.schema,
                tmode="ANSI",
                request_timeout=str(max(1, math.ceil(timeout_ms / 1000))),
                connect_timeout="3000",
                logon_timeout="5",
                runstartup="false",
                sslmode="VERIFY-FULL",
                encryptdata="true",
            )
            connection_holder.append(connection)
            try:
                if cancel_requested.is_set():
                    raise AnalyticsError("query_timeout", "Request cancelled before execution")
                with connection.cursor() as cursor:
                    cursor.execute(sql, params)
                    rows = cursor.fetchmany(limit + 1)
                    columns = [str(column[0]).strip() for column in cursor.description or []]
                    return bounded_result(columns, rows, limit)
            finally:
                connection.close()

        task = asyncio.create_task(asyncio.to_thread(work))
        try:
            async with asyncio.timeout(timeout_ms / 1000 + 8):
                return await asyncio.shield(task)
        except (asyncio.CancelledError, TimeoutError) as exc:
            cancel_requested.set()

            async def drain() -> None:
                if connection_holder:
                    try:
                        await asyncio.to_thread(connection_holder[0].cancel)
                    except Exception:
                        pass  # Cancellation may race with native completion/close.
                try:
                    await task
                except Exception:
                    pass

            cleanup = asyncio.create_task(drain())
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    # Shutdown and a request deadline may cancel the same task twice.
                    # Keep the concurrency slot until the native worker has closed.
                    continue
            await cleanup
            if isinstance(exc, TimeoutError):
                raise AnalyticsError(
                    "query_timeout", "Teradata request exceeded its deadline", True
                ) from exc
            raise
        except AnalyticsError:
            raise
        except Exception as exc:
            # Driver exceptions can contain SQL/credentials. Do not send them to tools or logs.
            raise AnalyticsError(
                "database_error", "Teradata operation failed; live validation required"
            ) from exc

    async def execute_readonly_query(self, request: QueryRequest) -> QueryResult:
        prepared = self.policy.prepare(request)
        return await self._execute(
            prepared.sql, prepared.parameters, request.timeout_ms, request.row_limit
        )

    async def get_schema(self) -> SchemaSnapshot:
        return SchemaSnapshot(
            tables=[
                await self.get_table_metadata(TableRef(schema_name=self.policy.schema, table=name))
                for name in sorted(self.policy.tables)
            ]
        )

    async def get_table_metadata(self, table: TableRef) -> TableMetadata:
        self.policy.check_table(table)
        result = await self._execute(
            """
            SELECT ColumnName, ColumnType, Nullable, ColumnId FROM DBC.ColumnsV
            WHERE DatabaseName = ? AND TableName = ? ORDER BY ColumnId
        """,
            [table.schema_name, table.table],
        )
        if result.truncated:
            raise AnalyticsError(
                "result_too_large", "Teradata column metadata exceeds the safe bound"
            )
        if not result.rows:
            raise AnalyticsError("not_found", "Table is not available")
        return TableMetadata(
            reference=table,
            columns=[
                ColumnMetadata(
                    name=str(n).strip(),
                    data_type=str(t).strip(),
                    nullable=str(nullable).strip() == "Y",
                    ordinal=int(str(ordinal)),
                )
                for n, t, nullable, ordinal in result.rows
            ],
        )

    async def get_column_statistics(self, table: TableRef, column: str) -> ColumnStatistics:
        metadata = await self.get_table_metadata(table)
        field = next((c for c in metadata.columns if c.name == column), None)
        if field is None:
            raise AnalyticsError("column_denied", "Column is not in the authorized table")
        if field.data_type not in {"I", "I1", "I2", "I8", "D", "F", "CF", "CV", "DA", "TS", "SZ"}:
            raise AnalyticsError(
                "unsupported_capability", "Teradata profiling type is not implemented"
            )
        q = '"' + column.replace('"', '""') + '"'
        relation = ".".join(
            '"' + part.replace('"', '""') + '"' for part in (table.schema_name, table.table)
        )
        result = await self.execute_readonly_query(
            QueryRequest(
                sql=(
                    f"SELECT COUNT(*), COUNT(*)-COUNT({q}), COUNT(DISTINCT {q}), "
                    f"MIN({q}), MAX({q}) FROM {relation}"
                ),
                row_limit=1,
            )
        )
        total, nulls, distinct, minimum, maximum = result.rows[0]
        return ColumnStatistics(
            reference=table,
            column=column,
            total_rows=int(str(total)),
            null_rows=int(str(nulls)),
            distinct_values=int(str(distinct)),
            minimum=minimum,
            maximum=maximum,
        )

    async def get_query_plan(self, request: QueryRequest) -> QueryPlan:
        prepared = self.policy.prepare(request, explain=True)
        result = await self._execute(prepared.sql, prepared.parameters, request.timeout_ms, 1000)
        return QueryPlan(dialect="teradata", plan=result.model_dump(mode="json"))

    async def get_table_dependencies(self, table: TableRef) -> Dependencies:
        self.policy.check_table(table)
        raise AnalyticsError(
            "unsupported_capability",
            "Teradata dependency catalog mapping requires validation on a live Vantage system",
        )

    async def get_supported_capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            backend="teradata",
            dialect="teradata",
            capabilities={
                name: Capability(
                    implemented=name != "get_table_dependencies",
                    live_verified=False,
                    detail=(
                        "Not implemented: requires a live validated dependency catalog mapping"
                        if name == "get_table_dependencies"
                        else (
                            "Driver path implemented; role attestation "
                            "and live Vantage validation required"
                        )
                    ),
                )
                for name in OPERATIONS
            },
        )
