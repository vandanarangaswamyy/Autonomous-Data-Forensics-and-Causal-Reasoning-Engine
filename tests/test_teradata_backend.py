"""Driver wiring tests only. These tests do not assert live Teradata integration."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from atlas_contracts.analytics import AnalyticsError, QueryRequest, TableRef
from atlas_core.analytics import AnalyticsBackend
from atlas_database.analytics.teradata import TeradataAnalyticsBackend
from pydantic import SecretStr


def adapter(attested: bool = False) -> TeradataAnalyticsBackend:
    return TeradataAnalyticsBackend(
        "unconfigured.invalid",
        "observer",
        SecretStr("not-a-real-password"),
        "retail",
        frozenset({"orders"}),
        readonly_role_attested=attested,
    )


async def test_teradata_reports_unverified_and_fails_closed() -> None:
    backend = adapter()
    assert isinstance(backend, AnalyticsBackend)
    capabilities = await backend.get_supported_capabilities()
    assert all(not c.live_verified for c in capabilities.capabilities.values())
    assert not capabilities.capabilities["get_table_dependencies"].implemented
    with pytest.raises(AnalyticsError) as error:
        await backend.execute_readonly_query(QueryRequest(sql="SELECT COUNT(*) FROM orders"))
    assert error.value.code == "readonly_role_unverified"
    with pytest.raises(AnalyticsError) as error:
        await backend.get_table_dependencies(TableRef(schema_name="retail", table="orders"))
    assert error.value.code == "unsupported_capability"


async def test_official_driver_binding_timeout_and_close_wiring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    calls: dict[str, Any] = {}

    class Cursor:
        description = [("value",)]

        def __enter__(self) -> "Cursor":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def execute(self, sql: str, parameters: object) -> None:
            calls["sql"], calls["parameters"] = sql, parameters

        def fetchmany(self, size: int) -> list[tuple[int]]:
            calls["fetch_size"] = size
            return [(1,)]

    class Connection:
        def cursor(self) -> Cursor:
            return Cursor()

        def close(self) -> None:
            calls["closed"] = True

    def connect(**kwargs: Any) -> Connection:
        calls["options"] = kwargs
        return Connection()

    monkeypatch.setattr(
        "atlas_database.analytics.teradata._load_driver",
        lambda: SimpleNamespace(connect=connect),
    )
    backend = adapter(True)
    result = await backend.execute_readonly_query(
        QueryRequest(
            sql="SELECT :value FROM orders", parameters={"value": 1}, row_limit=5, timeout_ms=1500
        )
    )
    assert result.rows == [[1]]
    assert calls["parameters"] == [1] and "?" in calls["sql"]
    assert calls["options"]["request_timeout"] == "2"
    assert calls["options"]["logon_timeout"] == "5"
    assert calls["options"]["sslmode"] == "VERIFY-FULL"
    assert calls["options"]["encryptdata"] == "true"
    assert calls["fetch_size"] == 6 and calls["closed"]
    assert all(
        not c.live_verified
        for c in (await backend.get_supported_capabilities()).capabilities.values()
    )


async def test_repeated_cancellation_waits_for_driver_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import threading

    started, cancelling, allow_release, query_release, closed = (
        threading.Event() for _ in range(5)
    )

    class Cursor:
        description = [("value",)]

        def __enter__(self) -> "Cursor":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def execute(self, sql: str, params: object) -> None:
            started.set()
            assert query_release.wait(2)

        def fetchmany(self, size: int) -> list[tuple[int]]:
            return [(1,)]

    class Connection:
        def cursor(self) -> Cursor:
            return Cursor()

        def cancel(self) -> None:
            cancelling.set()
            assert allow_release.wait(2)
            query_release.set()

        def close(self) -> None:
            closed.set()

    monkeypatch.setattr(
        "atlas_database.analytics.teradata._load_driver",
        lambda: SimpleNamespace(connect=lambda **kwargs: Connection()),
    )
    task = asyncio.create_task(
        adapter(True).execute_readonly_query(QueryRequest(sql="SELECT 1 FROM orders"))
    )
    try:
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        assert await asyncio.to_thread(cancelling.wait, 1)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done(), "Adapter released its slot while native work was still running"
        allow_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()
    finally:
        allow_release.set()
        query_release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert await asyncio.to_thread(closed.wait, 1)


async def test_gateway_retains_native_slot_until_query_and_close_terminate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Composed gateway/ledger/native-worker test; not live Vantage verification."""
    import asyncio
    import threading
    from uuid import uuid4

    from atlas_contracts.tools import SQLInput, ToolReply
    from atlas_mcp.audit import AuditLog, Budget
    from atlas_mcp.gateway import Gateway, ToolSpec
    from atlas_mcp.security import Authorizer, Permission, Principal

    started, cancelling, release_query, closing, release_close, closed = (
        threading.Event() for _ in range(6)
    )

    class Cursor:
        description = [("value",)]

        def __enter__(self) -> "Cursor":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def execute(self, sql: str, params: object) -> None:
            started.set()
            assert release_query.wait(5)

        def fetchmany(self, size: int) -> list[tuple[int]]:
            return [(1,)]

    class Connection:
        def cursor(self) -> Cursor:
            return Cursor()

        def cancel(self) -> None:
            cancelling.set()

        def close(self) -> None:
            closing.set()
            assert release_close.wait(5)
            closed.set()

    monkeypatch.setattr(
        "atlas_database.analytics.teradata._load_driver",
        lambda: SimpleNamespace(connect=lambda **kwargs: Connection()),
    )
    token = SecretStr("composed-native-test-" + "x" * 40)
    authorizer = Authorizer(
        token, Principal(id="native-review", permissions=frozenset({Permission.QUERY}))
    )
    path = tmp_path / "audit" / "calls.jsonl"
    gateway, contender = (
        Gateway(authorizer, AuditLog(path, Budget(concurrency=1))) for _ in range(2)
    )
    backend = adapter(True)

    async def execute(args: dict[str, Any]) -> dict[str, Any]:
        request = SQLInput.model_validate(args)
        return (await backend.execute_readonly_query(request.query)).model_dump(mode="json")

    spec = ToolSpec(
        "native_query",
        Permission.QUERY,
        SQLInput.model_json_schema(),
        ToolReply.model_json_schema(),
        execute,
    )
    gateway.register(spec)
    contender.register(spec)

    def arguments() -> dict[str, Any]:
        return {
            "context": {
                "investigation_id": str(uuid4()),
                "request_id": str(uuid4()),
                "timeout_ms": 100,
            },
            "query": {"sql": "SELECT 1 FROM orders"},
        }

    task = asyncio.create_task(gateway.invoke(spec.name, arguments(), token.get_secret_value()))
    shutdown = None
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        assert await asyncio.to_thread(cancelling.wait, 2)
        task.cancel()
        shutdown = asyncio.create_task(gateway.close())
        await asyncio.sleep(0.02)
        assert not task.done() and not shutdown.done()
        # Let the original request lease expire while execute remains blocked.
        await asyncio.sleep(0.12)
        for phase in ("execute", "close"):
            if phase == "close":
                release_query.set()
                assert await asyncio.to_thread(closing.wait, 2)
            denied = await contender.invoke(spec.name, arguments(), token.get_secret_value())
            assert denied.error and denied.error.code == "concurrency_limited"
            assert not task.done() and not closed.is_set()
            assert gateway._semaphore.locked()
            assert not any(r["event"] == "finished" for r in gateway.audit.records())
        release_close.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await shutdown
        assert closed.is_set() and not gateway._semaphore.locked()
        finished = [r for r in gateway.audit.records() if r["event"] == "finished"]
        assert len(finished) == 1 and finished[0]["error_code"] == "cancelled"
        accepted = await contender.invoke(spec.name, arguments(), token.get_secret_value())
        assert accepted.status == "ok" and accepted.result and accepted.result["rows"] == [[1]]
    finally:
        release_query.set()
        release_close.set()
        await asyncio.gather(task, *([shutdown] if shutdown else []), return_exceptions=True)
        await contender.close()
