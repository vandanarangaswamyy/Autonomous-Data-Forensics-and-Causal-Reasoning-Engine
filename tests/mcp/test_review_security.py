"""Regression tests from the skeptical database/MCP review."""

import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from atlas_contracts.analytics import AnalyticsError
from atlas_contracts.tools import RequestContext, ToolError, ToolReply
from atlas_mcp.audit import AuditLog, Budget
from atlas_mcp.catalog import TOOL_PERMISSIONS
from atlas_mcp.security import Permission
from atlas_mcp.server import MCPSettings, make_gateway
from mcp import types
from pydantic import SecretStr

TOKEN = "review-token-" + "r" * 40


def gateway_at(directory: Path) -> Any:
    return make_gateway(
        "knowledge",
        MCPSettings(
            access_token=SecretStr(TOKEN),
            permissions=frozenset({Permission.KNOWLEDGE}),
            audit_dir=directory,
        ),
    )


def arguments(timeout_ms: int = 5000) -> dict[str, Any]:
    return {
        "context": {
            "investigation_id": str(uuid4()),
            "request_id": str(uuid4()),
            "timeout_ms": timeout_ms,
        },
        "query": "payment",
    }


async def test_blocking_preparation_cannot_execute_after_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = gateway_at(tmp_path / "audit")
    original = gateway.audit.reserve
    executed = False

    def slow_reserve(*args: Any, **kwargs: Any) -> None:
        time.sleep(0.04)
        original(*args, **kwargs)

    async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
        nonlocal executed
        executed = True
        return {"matches": [], "retrieval_method": "keyword"}

    spec = gateway.registry["search_technical_documents"]
    gateway.registry[spec.name] = replace(spec, execute=handler)
    monkeypatch.setattr(gateway.audit, "reserve", slow_reserve)
    reply = await gateway.invoke(spec.name, arguments(10), TOKEN)
    assert reply.error and reply.error.code == "tool_timeout"
    assert not executed


def test_live_owner_keeps_concurrency_slot_after_deadline(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit" / "calls.jsonl", Budget(concurrency=1))

    def record() -> dict[str, Any]:
        return {
            "trace_id": str(uuid4()),
            "request_id": str(uuid4()),
            "investigation_id": str(uuid4()),
            "principal": "review",
            "tool": "review",
        }

    audit.reserve(record(), timeout_ms=10, row_reservation=1)
    time.sleep(0.03)
    with pytest.raises(AnalyticsError) as error:
        audit.reserve(record(), timeout_ms=1000, row_reservation=1)
    assert error.value.code == "concurrency_limited"


async def test_remote_error_does_not_reflect_sensitive_message_or_code(tmp_path: Path) -> None:
    gateway = gateway_at(tmp_path / "audit")
    advertised = gateway.tools()[0]
    secret = "private-password-from-driver"

    class Endpoint:
        async def list_tools(self) -> list[types.Tool]:
            return [advertised]

        async def call_tool(self, name: str, args: dict[str, Any]) -> types.CallToolResult:
            context = RequestContext.model_validate(args["context"])
            reply = ToolReply(
                status="error",
                investigation_id=context.investigation_id,
                request_id=context.request_id,
                trace_id=uuid4(),
                error=ToolError(code=secret, message=secret),
            )
            return types.CallToolResult(
                content=[], structuredContent=reply.model_dump(mode="json"), isError=True
            )

    await gateway.discover("remote", Endpoint(), TOOL_PERMISSIONS)
    reply = await gateway.invoke("remote.search_technical_documents", arguments(), TOKEN)
    assert reply.error and reply.error.code == "remote_error"
    assert secret not in reply.model_dump_json()
    assert secret not in gateway.audit.path.read_text()


async def test_invalid_handler_output_is_not_blamed_on_input(tmp_path: Path) -> None:
    gateway = gateway_at(tmp_path / "audit")
    spec = gateway.registry["search_technical_documents"]

    async def invalid(args: dict[str, Any]) -> dict[str, Any]:
        return {"matches": "broken-output"}

    gateway.registry[spec.name] = replace(spec, execute=invalid)
    reply = await gateway.invoke(spec.name, arguments(), TOKEN)
    assert reply.error and reply.error.code == "invalid_output"


async def test_contended_audit_lock_fails_closed_without_hanging(tmp_path: Path) -> None:
    gateway = gateway_at(tmp_path / "audit")
    with gateway.audit._file():
        started = time.monotonic()
        reply = await gateway.invoke("search_technical_documents", arguments(10), TOKEN)
        assert time.monotonic() - started < 0.5
    assert reply.error and reply.error.code == "audit_unavailable" and reply.result is None


async def test_unknown_tool_name_is_hashed_in_audit(tmp_path: Path) -> None:
    gateway = gateway_at(tmp_path / "audit")
    malicious_name = "SELECT private_value FROM credentials"
    result = await gateway.invoke(malicious_name, arguments(), TOKEN)
    assert result.error and result.error.code == "unknown_tool"
    assert malicious_name not in gateway.audit.path.read_text()


def test_dead_owner_releases_slot_but_keeps_worst_case_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit = AuditLog(tmp_path / "audit" / "calls.jsonl", Budget(concurrency=1, max_calls=1))
    original = {
        "trace_id": str(uuid4()),
        "request_id": str(uuid4()),
        "investigation_id": str(uuid4()),
        "principal": "review",
        "tool": "review",
    }
    audit.reserve(original, 1000, 1)

    def gone(pid: int, signal: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", gone)
    other = {**original, "trace_id": str(uuid4()), "request_id": str(uuid4())}
    with pytest.raises(AnalyticsError) as error:
        audit.reserve(other, 1000, 1)
    assert error.value.code == "budget_exceeded"
    new_investigation = {**other, "investigation_id": str(uuid4())}
    audit.reserve(new_investigation, 1000, 1)
