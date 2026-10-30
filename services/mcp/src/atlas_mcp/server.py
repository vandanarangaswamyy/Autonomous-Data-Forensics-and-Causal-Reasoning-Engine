"""Official MCP SDK stdio servers. Application authorization is enforced inside each."""

import argparse
import asyncio
from pathlib import Path
from typing import Any

from atlas_database.analytics.postgres import PostgreSQLAnalyticsBackend
from atlas_database.schema import BUSINESS_TABLES
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from atlas_mcp.audit import AuditLog, Budget
from atlas_mcp.catalog import GROUPS, register_tools
from atlas_mcp.gateway import Gateway
from atlas_mcp.security import Authorizer, Permission, Principal
from atlas_mcp.services import EvidenceServices, PublicDocumentSearch


class MCPSettings(BaseSettings):
    # No .env file: subprocesses receive only explicitly provisioned application credentials.
    model_config = SettingsConfigDict(env_prefix="ATLAS_MCP_", extra="ignore")
    access_token: SecretStr
    reader_url: SecretStr | None = None
    principal: str = "local-observer"
    permissions: frozenset[Permission] = frozenset()
    audit_dir: Path = Path(".atlas-runtime")
    allowed_tables: frozenset[str] = frozenset(t.name for t in BUSINESS_TABLES)
    budget: Budget = Budget()


def create_server(name: str, gateway: Gateway) -> Server[Any, Any]:
    server: Server[Any, Any] = Server(f"atlas-{name}")

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_tools() -> list[types.Tool]:
        return gateway.tools()

    # Route invalid input through our validator so rejection is structured and audited.
    @server.call_tool(validate_input=False)  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        meta = server.request_context.meta
        token = (meta.model_extra or {}).get("atlas_token") if meta else None
        reply = await gateway.invoke(name, arguments, token if isinstance(token, str) else None)
        payload = reply.model_dump(mode="json")
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=reply.model_dump_json())],
            structuredContent=payload,
            isError=reply.status == "error",
        )

    return server


def make_gateway(group: str, settings: MCPSettings) -> Gateway:
    backend = None
    if group != "knowledge":
        if settings.reader_url is None:
            raise ValueError("Provision ATLAS_MCP_READER_URL with a SELECT-only observer URL")
        backend = PostgreSQLAnalyticsBackend(
            settings.reader_url.get_secret_value(), settings.allowed_tables
        )
    public = Path(__file__).parent / "public_documents"
    documents = {
        name: public / f"{name}.md"
        for name in ("payment-ingestion", "financial-reporting", "lineage-operations")
    }
    services = EvidenceServices(backend, PublicDocumentSearch(documents))
    gateway = Gateway(
        Authorizer(
            settings.access_token,
            Principal(id=settings.principal, permissions=settings.permissions),
        ),
        AuditLog(settings.audit_dir / f"{group}.jsonl", settings.budget),
    )
    register_tools(gateway, group, services)
    return gateway


async def serve(group: str, settings: MCPSettings) -> None:
    gateway = make_gateway(group, settings)
    server = create_server(group, gateway)
    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        await gateway.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="ATLAS authorized MCP stdio server")
    parser.add_argument("server", choices=tuple(GROUPS))
    args = parser.parse_args()
    asyncio.run(serve(args.server, MCPSettings()))


if __name__ == "__main__":
    main()
