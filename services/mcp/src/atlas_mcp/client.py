"""Trusted operator client demonstrating real discovery and gateway-controlled invocation."""

import argparse
import asyncio
import json
import secrets
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import dotenv_values
from mcp import StdioServerParameters
from pydantic import SecretStr

from atlas_mcp.audit import AuditLog, Budget
from atlas_mcp.catalog import GROUPS, TOOL_PERMISSIONS
from atlas_mcp.gateway import Gateway
from atlas_mcp.security import Authorizer, Permission, Principal
from atlas_mcp.transports import local_transport


def server_parameters(
    group: str,
    token: SecretStr,
    reader_url: str | None,
    audit_dir: Path,
    permissions: frozenset[Permission],
) -> StdioServerParameters:
    environment = {
        "ATLAS_MCP_ACCESS_TOKEN": token.get_secret_value(),
        "ATLAS_MCP_PRINCIPAL": "local-observer",
        "ATLAS_MCP_PERMISSIONS": json.dumps(sorted(permissions)),
        "ATLAS_MCP_AUDIT_DIR": str(audit_dir.resolve()),
    }
    if reader_url and group != "knowledge":
        environment["ATLAS_MCP_READER_URL"] = reader_url
    return StdioServerParameters(
        command=sys.executable, args=["-m", "atlas_mcp.server", group], env=environment
    )


async def demo(reader_url: str, audit_dir: Path) -> dict[str, Any]:
    token = SecretStr(secrets.token_urlsafe(32))
    permissions = frozenset(Permission)
    gateway = Gateway(
        Authorizer(token, Principal(id="operator-client", permissions=permissions)),
        AuditLog(audit_dir / "gateway.jsonl", Budget()),
    )
    investigation = uuid4()
    examples: dict[str, dict[str, Any]] = {
        "analytics.get_table_schema": {"table": {"table": "orders"}},
        "analytics.execute_readonly_sql": {
            "query": {
                "sql": (
                    "SELECT currency, COUNT(*) AS orders FROM orders "
                    "WHERE total > :minimum GROUP BY currency ORDER BY currency"
                ),
                "parameters": {"minimum": 0},
                "row_limit": 10,
            }
        },
        "analytics.profile_column": {"table": {"table": "orders"}, "column": "total"},
        "analytics.get_query_plan": {"query": {"sql": "SELECT id FROM orders", "row_limit": 5}},
        "lineage.trace_upstream_lineage": {"dataset": "daily_financials"},
        "lineage.trace_downstream_lineage": {"dataset": "payment_events"},
        "telemetry.get_pipeline_execution": {"pipeline_id": "ingest"},
        "telemetry.search_incident_history": {"query": "source"},
        "knowledge.search_technical_documents": {"query": "payment ingestion schema"},
    }
    responses: dict[str, Any] = {}
    try:
        async with AsyncExitStack() as stack:
            for group in GROUPS:
                transport = await stack.enter_async_context(
                    local_transport(
                        server_parameters(group, token, reader_url, audit_dir, permissions), token
                    )
                )
                await gateway.discover(group, transport, TOOL_PERMISSIONS)
            for name, arguments in examples.items():
                reply = await gateway.invoke(
                    name,
                    {
                        **arguments,
                        "context": {
                            "investigation_id": str(investigation),
                            "request_id": str(uuid4()),
                            "timeout_ms": 10000,
                        },
                    },
                    token.get_secret_value(),
                )
                responses[name] = reply.model_dump(mode="json")
    finally:
        await gateway.close()
    return {
        "investigation_id": str(investigation),
        "tools": list(gateway.registry),
        "responses": responses,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Trusted local MCP demo; grants read tools to child sessions"
    )
    parser.add_argument("--audit-dir", type=Path, default=Path(".atlas-runtime"))
    args = parser.parse_args()
    values = dotenv_values(".env")
    url = values.get("ATLAS_SIM_READER_URL")
    if not url:
        raise SystemExit(
            "Provision the observer with make sim-bootstrap and make sim-migrate first"
        )
    result = asyncio.run(demo(url, args.audit_dir))
    print(json.dumps(result, indent=2))
    if any(response["status"] != "ok" for response in result["responses"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
