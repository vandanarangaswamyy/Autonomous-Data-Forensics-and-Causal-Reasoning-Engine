import os
from pathlib import Path
from uuid import uuid4

import pytest
from atlas_contracts.analytics import AnalyticsError, QueryRequest, TableRef
from atlas_core.analytics import AnalyticsBackend
from atlas_database.analytics.postgres import PostgreSQLAnalyticsBackend
from atlas_database.schema import BUSINESS_TABLES
from atlas_mcp.client import demo

pytestmark = pytest.mark.integration


@pytest.fixture
def backend() -> PostgreSQLAnalyticsBackend:
    url = os.environ.get("ATLAS_TEST_READER_URL")
    if not url:
        pytest.skip("Set ATLAS_TEST_READER_URL for live read-only backend tests")
    return PostgreSQLAnalyticsBackend(url, frozenset(t.name for t in BUSINESS_TABLES))


async def test_postgres_backend_contract(backend: PostgreSQLAnalyticsBackend) -> None:
    assert isinstance(backend, AnalyticsBackend)
    initial = await backend.get_supported_capabilities()
    assert all(c.implemented and not c.live_verified for c in initial.capabilities.values())
    table = TableRef(table="orders")
    metadata = await backend.get_table_metadata(table)
    assert {c.name for c in metadata.columns} >= {"id", "total", "account_id"}
    assert metadata.primary_key == ["id"] and metadata.indexes
    schema = await backend.get_schema()
    assert {t.reference.table for t in schema.tables} == backend.policy.tables
    profile = await backend.get_column_statistics(table, "total")
    assert profile.total_rows > 0 and profile.null_rows == 0 and profile.numeric_mean is not None
    dependencies = await backend.get_table_dependencies(table)
    assert any(
        edge.source.table == "accounts" and edge.destination.table == "orders"
        for edge in dependencies.edges
    )
    result = await backend.execute_readonly_query(
        QueryRequest(sql="SELECT id FROM orders ORDER BY id", row_limit=2)
    )
    assert result.row_count == 2 and result.truncated
    plan = await backend.get_query_plan(
        QueryRequest(sql="SELECT id FROM orders WHERE total > :minimum", parameters={"minimum": 10})
    )
    assert plan.plan and not plan.executes_query
    assert all(
        c.live_verified for c in (await backend.get_supported_capabilities()).capabilities.values()
    )


async def test_binding_injection_and_literal_percent(backend: PostgreSQLAnalyticsBackend) -> None:
    attack = "USD'; DELETE FROM orders;--"
    result = await backend.execute_readonly_query(
        QueryRequest(
            sql="SELECT id FROM orders WHERE currency = :currency", parameters={"currency": attack}
        )
    )
    assert result.rows == []
    literal = await backend.execute_readonly_query(
        QueryRequest(
            sql="SELECT '100% %(v)s', :v FROM orders", parameters={"v": "safe"}, row_limit=1
        )
    )
    assert literal.rows == [["100% %(v)s", "safe"]]
    for table, column in [
        (TableRef(table="event_outbox"), "id"),
        (TableRef(table="orders"), "total); DELETE FROM orders;--"),
    ]:
        with pytest.raises(AnalyticsError):
            await backend.get_column_statistics(table, column)
    with pytest.raises(AnalyticsError) as error:
        await backend.execute_readonly_query(QueryRequest(sql="SELECT pg_sleep(1)"))
    assert error.value.code == "sql_denied"


async def test_real_query_timeout_and_recovery(backend: PostgreSQLAnalyticsBackend) -> None:
    with pytest.raises(AnalyticsError) as error:
        await backend.execute_readonly_query(
            QueryRequest(
                sql="""
            SELECT COUNT(*) FROM orders a CROSS JOIN orders b
            CROSS JOIN orders c CROSS JOIN orders d
        """,
                timeout_ms=10,
            )
        )
    assert error.value.code == "query_timeout"
    assert (
        await backend.execute_readonly_query(QueryRequest(sql="SELECT COUNT(*) FROM orders"))
    ).rows


async def test_owner_role_rejected_and_response_byte_budget(
    backend: PostgreSQLAnalyticsBackend,
) -> None:
    owner = os.environ.get("ATLAS_TEST_POSTGRES_URL")
    if not owner:
        pytest.skip("Set ATLAS_TEST_POSTGRES_URL to test rejection of privileged credentials")
    unsafe = PostgreSQLAnalyticsBackend(owner, backend.policy.tables)
    with pytest.raises(AnalyticsError) as error:
        await unsafe.execute_readonly_query(QueryRequest(sql="SELECT COUNT(*) FROM orders"))
    assert error.value.code == "unsafe_role"
    with pytest.raises(AnalyticsError) as large:
        await backend.execute_readonly_query(
            QueryRequest(sql="SELECT '" + "x" * 3000 + "' FROM orders", row_limit=1000)
        )
    assert large.value.code == "result_too_large"


async def test_all_four_real_mcp_servers(
    backend: PostgreSQLAnalyticsBackend, tmp_path: Path
) -> None:
    url = os.environ["ATLAS_TEST_READER_URL"]
    result = await demo(url, tmp_path / "audit")
    from scripts.verify_clean_mcp_setup import verify_demo_contents

    verify_demo_contents(result)
    actual = result["responses"]["analytics.execute_readonly_sql"]["result"]
    expected = await backend.execute_readonly_query(
        QueryRequest(
            sql=(
                "SELECT currency, COUNT(*) AS orders FROM orders "
                "WHERE total > :minimum GROUP BY currency ORDER BY currency"
            ),
            parameters={"minimum": 0},
            row_limit=10,
        )
    )
    assert actual == expected.model_dump(mode="json")
    assert result["responses"]["analytics.profile_column"]["result"] == (
        await backend.get_column_statistics(TableRef(table="orders"), "total")
    ).model_dump(mode="json")
    assert result["responses"]["lineage.trace_upstream_lineage"]["result"]["edges"]
    assert result["responses"]["knowledge.search_technical_documents"]["result"]["matches"]
    assert (tmp_path / "audit" / "gateway.jsonl").stat().st_mode & 0o777 == 0o600
    for group in ("analytics", "lineage", "telemetry", "knowledge"):
        assert (tmp_path / "audit" / f"{group}.jsonl").exists()


async def test_live_mcp_timeout_denial_and_sql_safety(
    backend: PostgreSQLAnalyticsBackend, tmp_path: Path
) -> None:
    from atlas_mcp.client import server_parameters
    from atlas_mcp.security import Permission
    from atlas_mcp.transports import local_transport
    from pydantic import SecretStr

    token = SecretStr("live-mcp-test-" + "y" * 40)
    parameters = server_parameters(
        "analytics",
        token,
        os.environ["ATLAS_TEST_READER_URL"],
        tmp_path / "audit",
        frozenset({Permission.QUERY}),
    )
    async with local_transport(parameters, token) as transport:

        def context() -> dict[str, str | int]:
            return {
                "investigation_id": str(uuid4()),
                "request_id": str(uuid4()),
                "timeout_ms": 10000,
            }

        denied = await transport.call_tool(
            "get_table_schema", {"context": context(), "table": {"table": "orders"}}
        )
        assert denied.isError and denied.structuredContent
        assert denied.structuredContent["error"]["code"] == "permission_denied"
        unsafe = await transport.call_tool(
            "execute_readonly_sql",
            {"context": context(), "query": {"sql": "SELECT 1; DELETE FROM orders"}},
        )
        assert unsafe.isError and unsafe.structuredContent
        assert unsafe.structuredContent["error"]["code"] == "sql_denied"
        timeout = await transport.call_tool(
            "execute_readonly_sql",
            {
                "context": context(),
                "query": {
                    "sql": (
                        "SELECT COUNT(*) FROM orders a CROSS JOIN orders b "
                        "CROSS JOIN orders c CROSS JOIN orders d"
                    ),
                    "timeout_ms": 10,
                },
            },
        )
        assert timeout.isError and timeout.structuredContent
        assert timeout.structuredContent["error"]["code"] == "query_timeout"
        success = await transport.call_tool(
            "execute_readonly_sql",
            {"context": context(), "query": {"sql": "SELECT COUNT(*) FROM orders"}},
        )
        assert not success.isError


@pytest.mark.parametrize("column_write", [False, True])
async def test_column_write_grant_and_composite_key_order(
    postgres_db: object, column_write: bool
) -> None:
    import secrets

    import psycopg
    from psycopg import sql
    from sqlalchemy import Engine
    from sqlalchemy.engine import make_url

    assert isinstance(postgres_db, Engine)
    role = "review_column_" + uuid4().hex
    password = secrets.token_urlsafe(32)
    with postgres_db.begin() as connection:
        raw = connection.connection.driver_connection
        assert isinstance(raw, psycopg.Connection)
        schema_row = raw.execute("SELECT current_schema()").fetchone()
        assert schema_row is not None
        schema = str(schema_row[0])
        extras = ", ".join(f"extra_{i} integer" for i in range(1001))
        raw.execute(
            "CREATE TABLE privilege_probe (id integer, value integer, auxiliary integer, "
            + extras
            + ", PRIMARY KEY(value, id) INCLUDE(auxiliary))"
        )
        raw.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
        database_row = raw.execute("SELECT current_database()").fetchone()
        assert database_row is not None
        database = str(database_row[0])
        raw.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(database), sql.Identifier(role)
            )
        )
        raw.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(schema), sql.Identifier(role)
            )
        )
        raw.execute(sql.SQL("GRANT SELECT ON privilege_probe TO {}").format(sql.Identifier(role)))
        if column_write:
            raw.execute(
                sql.SQL("GRANT UPDATE(value) ON privilege_probe TO {}").format(sql.Identifier(role))
            )
            rights = raw.execute(
                "SELECT has_table_privilege(%s, 'privilege_probe', 'UPDATE'), "
                "has_any_column_privilege(%s, 'privilege_probe', 'UPDATE')",
                (role, role),
            ).fetchone()
            assert rights == (False, True)
    url = make_url(os.environ["ATLAS_TEST_POSTGRES_URL"]).set(username=role, password=password)
    probe = PostgreSQLAnalyticsBackend(
        url.render_as_string(hide_password=False), frozenset({"privilege_probe"}), schema=schema
    )
    try:
        if column_write:
            with pytest.raises(AnalyticsError) as error:
                await probe.execute_readonly_query(
                    QueryRequest(sql="SELECT * FROM privilege_probe")
                )
            assert error.value.code == "unsafe_role"
        else:
            metadata = await probe.get_table_metadata(
                TableRef(schema_name=schema, table="privilege_probe")
            )
            assert metadata.primary_key == ["value", "id"]
            assert len(metadata.columns) == 1004
    finally:
        with postgres_db.begin() as connection:
            raw = connection.connection.driver_connection
            assert isinstance(raw, psycopg.Connection)
            raw.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            raw.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


@pytest.mark.parametrize("failure", ["query", "authentication"])
async def test_live_mcp_errors_do_not_disclose_driver_or_connection_details(
    backend: PostgreSQLAnalyticsBackend, tmp_path: Path, failure: str
) -> None:
    import json

    from atlas_mcp.client import server_parameters
    from atlas_mcp.security import Permission
    from atlas_mcp.transports import local_transport
    from pydantic import SecretStr
    from sqlalchemy.engine import make_url

    token = SecretStr("sanitization-test-" + "q" * 40)
    canary = "private_marker_" + uuid4().hex
    reader = os.environ["ATLAS_TEST_READER_URL"]
    parsed = make_url(reader)
    if failure == "authentication":
        reader = parsed.set(username=canary, password=canary).render_as_string(hide_password=False)
    query = f"SELECT {canary} FROM orders" if failure == "query" else "SELECT id FROM orders"
    audit_dir = tmp_path / "audit"
    parameters = server_parameters(
        "analytics", token, reader, audit_dir, frozenset({Permission.QUERY})
    )
    async with local_transport(parameters, token) as transport:
        reply = await transport.call_tool(
            "execute_readonly_sql",
            {
                "context": {
                    "investigation_id": str(uuid4()),
                    "request_id": str(uuid4()),
                    "timeout_ms": 10000,
                },
                "query": {"sql": query},
            },
        )
        assert reply.isError and reply.structuredContent
        assert reply.structuredContent["result"] is None
        assert reply.structuredContent["error"] == {
            "code": "database_error",
            "message": "Database operation failed",
            "retryable": True,
        }
        response_and_audit = reply.model_dump_json() + (audit_dir / "analytics.jsonl").read_text()
        sensitive = (
            canary,
            reader,
            parsed.password,
            token.get_secret_value(),
            query,
            "psycopg",
            "Traceback",
            "connection failed",
            "does not exist",
        )
        for value in sensitive:
            if value:
                assert value not in response_and_audit
        # Verify the text and structured MCP representations agree and are both sanitized.
        from mcp.types import TextContent

        content = reply.content[0]
        assert isinstance(content, TextContent)
        assert json.loads(content.text) == reply.structuredContent
