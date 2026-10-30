import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from atlas_mcp.audit import AuditLog, Budget
from atlas_mcp.gateway import Gateway
from atlas_mcp.security import Permission
from atlas_mcp.server import MCPSettings, make_gateway
from pydantic import SecretStr

TOKEN = "unit-session-secret-" + "a" * 32


def args() -> dict[str, Any]:
    return {
        "context": {"investigation_id": str(uuid4()), "request_id": str(uuid4())},
        "query": "payment",
    }


def knowledge_gateway(tmp_path: Path, permissions: frozenset[Permission] | None = None) -> Gateway:
    return make_gateway(
        "knowledge",
        MCPSettings(
            access_token=SecretStr(TOKEN),
            permissions=permissions
            if permissions is not None
            else frozenset({Permission.KNOWLEDGE}),
            audit_dir=tmp_path / "audit",
        ),
    )


async def test_discovery_execution_validation_and_audit(tmp_path: Path) -> None:
    gateway = knowledge_gateway(tmp_path)
    tools = gateway.tools()
    assert [t.name for t in tools] == ["search_technical_documents"]
    assert tools[0].inputSchema and tools[0].outputSchema
    request = args()
    response = await gateway.invoke(tools[0].name, request, TOKEN)
    assert response.status == "ok" and response.result and response.result["matches"]
    assert (
        response.request_id == request["context"]["request_id"]
        or str(response.request_id) == request["context"]["request_id"]
    )
    invalid = await gateway.invoke(tools[0].name, {**args(), "query": 123}, TOKEN)
    assert invalid.error and invalid.error.code == "invalid_input"
    unknown = await gateway.invoke("unregistered", args(), TOKEN)
    assert unknown.error and unknown.error.code == "unknown_tool"
    records = gateway.audit.records()
    assert [r["event"] for r in records] == ["started", "finished", "rejected", "rejected"]
    assert records[1]["trace_id"] == str(response.trace_id)
    serialized = gateway.audit.path.read_text()
    assert TOKEN not in serialized and '"query":' not in serialized and "payment" not in serialized
    await gateway.close()


async def test_authorization_and_no_argument_escalation(tmp_path: Path) -> None:
    gateway = knowledge_gateway(tmp_path, frozenset())
    unauthorized = await gateway.invoke("search_technical_documents", args(), "wrong-token")
    assert unauthorized.error and unauthorized.error.code == "unauthorized"
    denied = await gateway.invoke("search_technical_documents", args(), TOKEN)
    assert denied.error and denied.error.code == "permission_denied"
    permitted = knowledge_gateway(tmp_path / "other")
    forged = await permitted.invoke(
        "search_technical_documents", {**args(), "permissions": ["admin"]}, TOKEN
    )
    assert forged.error and forged.error.code == "invalid_input"


async def test_durable_budgets_duplicate_ids_and_restart(tmp_path: Path) -> None:
    gateway = knowledge_gateway(tmp_path)
    gateway.audit.budget = Budget(max_calls=1)
    request = args()
    assert (await gateway.invoke("search_technical_documents", request, TOKEN)).status == "ok"
    duplicate = await gateway.invoke("search_technical_documents", request, TOKEN)
    assert duplicate.error and duplicate.error.code == "duplicate_request"
    restarted = knowledge_gateway(tmp_path)
    restarted.audit.budget = Budget(max_calls=1)
    next_request = {**request, "context": {**request["context"], "request_id": str(uuid4())}}
    exhausted = await restarted.invoke("search_technical_documents", next_request, TOKEN)
    assert exhausted.error and exhausted.error.code == "budget_exceeded"


async def test_timeouts_concurrency_and_graceful_close(tmp_path: Path) -> None:
    gateway = knowledge_gateway(tmp_path)
    gateway._semaphore = asyncio.Semaphore(1)
    spec = gateway.registry["search_technical_documents"]
    running = 0
    maximum = 0
    entered = asyncio.Event()

    async def slow(arguments: dict[str, Any]) -> dict[str, Any]:
        nonlocal running, maximum
        running += 1
        maximum = max(maximum, running)
        entered.set()
        try:
            await asyncio.sleep(0.05)
            return {"matches": [], "retrieval_method": "keyword"}
        finally:
            running -= 1

    gateway.registry[spec.name] = replace(spec, execute=slow)
    first, second = await asyncio.gather(
        gateway.invoke(spec.name, args(), TOKEN), gateway.invoke(spec.name, args(), TOKEN)
    )
    assert first.status == second.status == "ok" and maximum == 1
    request = args()
    request["context"]["timeout_ms"] = 10
    expired = await gateway.invoke(spec.name, request, TOKEN)
    assert expired.error and expired.error.code == "tool_timeout"
    entered.clear()
    task = asyncio.create_task(gateway.invoke(spec.name, args(), TOKEN))
    await entered.wait()
    await gateway.close()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert running == 0
    assert gateway.audit.records()[-1]["error_code"] == "cancelled"
    closed = await gateway.invoke(spec.name, args(), TOKEN)
    assert closed.error and closed.error.code == "shutting_down"


async def test_unavailable_audit_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = knowledge_gateway(tmp_path)

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(gateway.audit, "append", fail)
    reply = await gateway.invoke("search_technical_documents", args(), TOKEN)
    assert reply.error and reply.error.code == "audit_unavailable" and reply.result is None


async def test_refuse_public_or_corrupt_audit(tmp_path: Path) -> None:
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    public.chmod(0o755)
    with pytest.raises(PermissionError):
        AuditLog(public / "audit.jsonl", Budget())
    gateway = knowledge_gateway(tmp_path / "private")
    gateway.audit.path.write_text("not json\n")
    gateway.audit.path.chmod(0o600)
    reply = await gateway.invoke("search_technical_documents", args(), TOKEN)
    assert reply.error and reply.error.code == "audit_unavailable"


async def test_transport_discovery_uses_explicit_permissions_and_checks_correlation(
    tmp_path: Path,
) -> None:
    from atlas_contracts.tools import ToolReply
    from atlas_mcp.catalog import TOOL_PERMISSIONS
    from mcp import types

    advertised = knowledge_gateway(tmp_path / "server").tools()[0]

    class Endpoint:
        async def list_tools(self) -> list[types.Tool]:
            return [advertised, advertised.model_copy(update={"name": "unapproved_admin_tool"})]

        async def call_tool(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
            reply = ToolReply(
                status="ok",
                investigation_id=uuid4(),
                request_id=uuid4(),
                trace_id=uuid4(),
                result={"matches": [], "retrieval_method": "keyword"},
            )
            return types.CallToolResult(content=[], structuredContent=reply.model_dump(mode="json"))

    gateway = knowledge_gateway(tmp_path / "client")
    names = await gateway.discover("remote", Endpoint(), TOOL_PERMISSIONS)
    assert names == ["remote.search_technical_documents"]
    reply = await gateway.invoke(names[0], args(), TOKEN)
    assert reply.error and reply.error.code == "transport_error"
    with pytest.raises(ValueError):
        gateway.register(
            replace(
                gateway.registry[names[0]],
                name="external-reference",
                input_schema={"$ref": "https://internal.invalid/secret"},
            )
        )
