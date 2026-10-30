from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from pydantic import SecretStr


class SessionTransport:
    """Works with an authenticated SDK session; transport creation is separate from policy."""

    def __init__(self, session: ClientSession, token: SecretStr) -> None:
        self.session, self._token = session, token

    async def list_tools(self) -> list[types.Tool]:
        tools: list[types.Tool] = []
        cursor = None
        while True:
            page = await self.session.list_tools(cursor=cursor)
            tools.extend(page.tools)
            if not page.nextCursor:
                return tools
            cursor = page.nextCursor

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        return await self.session.call_tool(
            name,
            arguments,
            read_timeout_seconds=timedelta(seconds=35),
            meta={"atlas_token": self._token.get_secret_value()},
        )


@asynccontextmanager
async def local_transport(
    parameters: StdioServerParameters, token: SecretStr
) -> AsyncIterator[SessionTransport]:
    async with (
        stdio_client(parameters) as (read, write),
        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=35)) as session,
    ):
        await session.initialize()
        yield SessionTransport(session, token)


# Remote transports implement gateway.ToolTransport, or create an authenticated SDK
# ClientSession and wrap it in SessionTransport. No unauthenticated HTTP adapter is shipped.
