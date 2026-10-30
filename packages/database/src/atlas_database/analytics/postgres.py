"""Asynchronous psycopg PostgreSQL adapter, with independent policy and role checks."""

import asyncio
from typing import Any

import psycopg
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
    TableDependency,
    TableMetadata,
    TableRef,
)
from sqlalchemy.engine import make_url

from atlas_database.analytics.common import OPERATIONS, bounded_result
from atlas_database.analytics.policy import SQLPolicy


class PostgreSQLAnalyticsBackend:
    def __init__(self, url: str, tables: frozenset[str], schema: str = "public") -> None:
        parsed = make_url(url)
        if parsed.get_backend_name() not in {"postgresql", "postgres"}:
            raise ValueError("A PostgreSQL observer URL is required")
        self._dsn = parsed.set(drivername="postgresql").render_as_string(hide_password=False)
        self.policy = SQLPolicy("postgres", schema, tables)
        self._verified: set[str] = set()

    async def _execute(
        self, sql: str, params: Any, timeout_ms: int = 3000, limit: int = 1000
    ) -> QueryResult:
        try:
            async with asyncio.timeout(timeout_ms / 1000 + 4):
                async with await psycopg.AsyncConnection.connect(
                    self._dsn, connect_timeout=3
                ) as conn:
                    async with conn.transaction():
                        await conn.execute("SET TRANSACTION READ ONLY")
                        await conn.execute(
                            "SELECT set_config('statement_timeout', %s, true)", (str(timeout_ms),)
                        )
                        await conn.execute(
                            "SELECT set_config('lock_timeout', %s, true)",
                            (str(min(timeout_ms, 1000)),),
                        )
                        await conn.execute("SET LOCAL search_path TO pg_catalog")
                        role = await (
                            await conn.execute(
                                """
                            SELECT rolsuper, rolcreaterole, rolcreatedb,
                            rolreplication, rolbypassrls,
                            has_schema_privilege(current_user, %s, 'CREATE'),
                            pg_has_role(current_user, 'pg_read_server_files', 'USAGE'),
                            pg_has_role(current_user, 'pg_write_server_files', 'USAGE'),
                            pg_has_role(current_user, 'pg_execute_server_program', 'USAGE')
                            FROM pg_roles WHERE rolname = current_user
                        """,
                                (self.policy.schema,),
                            )
                        ).fetchone()
                        if role is None or any(role):
                            raise AnalyticsError(
                                "unsafe_role", "Use a restricted, SELECT-only observer role"
                            )
                        privilege = await (
                            await conn.execute(
                                """
                            SELECT c.relname, c.relkind,
                                has_table_privilege(current_user, c.oid, 'SELECT'),
                                has_table_privilege(current_user, c.oid,
                                    'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                                OR has_any_column_privilege(current_user, c.oid,
                                    'INSERT,UPDATE,REFERENCES')
                            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                            WHERE n.nspname = %s AND c.relname = ANY(%s)
                        """,
                                (self.policy.schema, list(self.policy.tables)),
                            )
                        ).fetchall()
                        if len(privilege) != len(self.policy.tables) or any(
                            kind != "r" or not can_read or can_write
                            for _, kind, can_read, can_write in privilege
                        ):
                            raise AnalyticsError(
                                "unsafe_role", "Catalog must contain SELECT-only base tables"
                            )
                        async with conn.cursor() as cursor:
                            await cursor.execute(sql, params)
                            rows = await cursor.fetchmany(limit + 1)
                            columns = [column.name for column in cursor.description or []]
                            return bounded_result(columns, rows, limit)
        except AnalyticsError:
            raise
        except (psycopg.errors.QueryCanceled, psycopg.errors.LockNotAvailable, TimeoutError) as exc:
            raise AnalyticsError(
                "query_timeout", "Database query exceeded its deadline", True
            ) from exc
        except asyncio.CancelledError:
            raise
        except psycopg.Error as exc:
            raise AnalyticsError("database_error", "Database operation failed", True) from exc

    async def execute_readonly_query(self, request: QueryRequest) -> QueryResult:
        prepared = self.policy.prepare(request)
        result = await self._execute(
            prepared.sql, prepared.parameters, request.timeout_ms, request.row_limit
        )
        self._verified.add("execute_readonly_query")
        return result

    async def get_schema(self) -> SchemaSnapshot:
        tables = [
            await self.get_table_metadata(TableRef(schema_name=self.policy.schema, table=name))
            for name in sorted(self.policy.tables)
        ]
        self._verified.add("get_schema")
        return SchemaSnapshot(tables=tables)

    async def get_table_metadata(self, table: TableRef) -> TableMetadata:
        self.policy.check_table(table)
        columns = await self._execute(
            """
            SELECT column_name, data_type, is_nullable, ordinal_position
            FROM information_schema.columns
            WHERE table_schema = %(schema)s AND table_name = %(table)s
            ORDER BY ordinal_position
        """,
            {"schema": table.schema_name, "table": table.table},
            limit=2000,  # PostgreSQL permits up to 1600 columns on an ordinary table.
        )
        if columns.truncated:
            raise AnalyticsError("result_too_large", "Column metadata exceeds the safe bound")
        if not columns.rows:
            raise AnalyticsError("not_found", "Table is not available")
        keys = await self._execute(
            """
            SELECT a.attname FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS keys(attnum, position) ON true
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = keys.attnum
            WHERE i.indisprimary AND keys.position <= i.indnkeyatts
            AND n.nspname = %(schema)s AND c.relname = %(table)s
            ORDER BY keys.position
        """,
            {"schema": table.schema_name, "table": table.table},
        )
        indexes = await self._execute(
            """
            SELECT indexname FROM pg_indexes WHERE schemaname = %(schema)s AND tablename = %(table)s
            ORDER BY indexname
        """,
            {"schema": table.schema_name, "table": table.table},
        )
        if keys.truncated or indexes.truncated:
            raise AnalyticsError("result_too_large", "Table metadata exceeds the safe bound")
        self._verified.add("get_table_metadata")
        return TableMetadata(
            reference=table,
            columns=[
                ColumnMetadata(
                    name=str(n),
                    data_type=str(t),
                    nullable=nullable == "YES",
                    ordinal=int(str(position)),
                )
                for n, t, nullable, position in columns.rows
            ],
            primary_key=[str(row[0]) for row in keys.rows],
            indexes=[str(row[0]) for row in indexes.rows],
        )

    async def get_column_statistics(self, table: TableRef, column: str) -> ColumnStatistics:
        metadata = await self.get_table_metadata(table)
        field = next((c for c in metadata.columns if c.name == column), None)
        if field is None:
            raise AnalyticsError("column_denied", "Column is not in the authorized table")
        if field.data_type not in {
            "integer",
            "bigint",
            "smallint",
            "numeric",
            "real",
            "double precision",
            "character varying",
            "text",
            "date",
            "timestamp with time zone",
            "timestamp without time zone",
        }:
            raise AnalyticsError(
                "unsupported_capability", "Profiling this column type is unavailable"
            )
        # Identifier quoting is separate from value binding; names must resolve in trusted metadata.
        quoted = '"' + column.replace('"', '""') + '"'
        relation = ".".join(
            '"' + part.replace('"', '""') + '"' for part in (table.schema_name, table.table)
        )
        numeric = field.data_type in {
            "integer",
            "bigint",
            "smallint",
            "numeric",
            "real",
            "double precision",
        }
        average = f", AVG({quoted})" if numeric else ", NULL"
        query = (
            f"SELECT COUNT(*), COUNT(*) - COUNT({quoted}), COUNT(DISTINCT {quoted}), "
            f"MIN({quoted}), MAX({quoted}){average} FROM {relation}"
        )
        result = await self.execute_readonly_query(QueryRequest(sql=query, row_limit=1))
        total, nulls, distinct, minimum, maximum, mean = result.rows[0]
        self._verified.add("get_column_statistics")
        return ColumnStatistics(
            reference=table,
            column=column,
            total_rows=int(str(total)),
            null_rows=int(str(nulls)),
            distinct_values=int(str(distinct)),
            minimum=minimum,
            maximum=maximum,
            numeric_mean=str(mean) if mean is not None else None,
        )

    async def get_query_plan(self, request: QueryRequest) -> QueryPlan:
        prepared = self.policy.prepare(request, explain=True)
        result = await self._execute(prepared.sql, prepared.parameters, request.timeout_ms, 1)
        self._verified.add("get_query_plan")
        return QueryPlan(dialect="postgres", plan=result.rows[0][0])

    async def get_table_dependencies(self, table: TableRef) -> Dependencies:
        self.policy.check_table(table)
        result = await self._execute(
            """
            SELECT parent.relname, pa.attname, child.relname, ca.attname
            FROM pg_constraint fk JOIN pg_class child ON child.oid = fk.conrelid
            JOIN pg_namespace cn ON cn.oid = child.relnamespace
            JOIN pg_class parent ON parent.oid = fk.confrelid
            JOIN pg_namespace pn ON pn.oid = parent.relnamespace
            JOIN LATERAL unnest(fk.conkey, fk.confkey) AS keys(child_key, parent_key) ON true
            JOIN pg_attribute ca ON ca.attrelid = child.oid AND ca.attnum = keys.child_key
            JOIN pg_attribute pa ON pa.attrelid = parent.oid AND pa.attnum = keys.parent_key
            WHERE fk.contype = 'f' AND cn.nspname = %(schema)s AND pn.nspname = %(schema)s
            AND (child.relname = %(table)s OR parent.relname = %(table)s)
            ORDER BY parent.relname, child.relname, ca.attname
        """,
            {"schema": table.schema_name, "table": table.table},
        )
        if result.truncated:
            raise AnalyticsError("result_too_large", "Dependency metadata exceeds the safe bound")
        self._verified.add("get_table_dependencies")
        return Dependencies(
            edges=[
                TableDependency(
                    source=TableRef(schema_name=table.schema_name, table=str(parent)),
                    destination=TableRef(schema_name=table.schema_name, table=str(child)),
                    source_column=str(parent_column),
                    destination_column=str(child_column),
                )
                for parent, parent_column, child, child_column in result.rows
                if parent in self.policy.tables and child in self.policy.tables
            ]
        )

    async def get_supported_capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            backend="postgresql",
            dialect="postgres",
            capabilities={
                name: Capability(
                    implemented=True,
                    live_verified=name in self._verified,
                    detail="Implemented; requires a restricted PostgreSQL observer connection",
                )
                for name in OPERATIONS
            },
        )
