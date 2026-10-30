from pathlib import Path
from uuid import uuid4

from atlas_mcp.client import server_parameters
from atlas_mcp.security import Permission
from atlas_mcp.transports import SessionTransport, local_transport
from pydantic import SecretStr


async def test_real_sdk_stdio_discovery_execution_and_shutdown(tmp_path: Path) -> None:
    token = SecretStr("stdio-test-token-" + "z" * 32)
    parameters = server_parameters(
        "knowledge", token, None, tmp_path / "audit", frozenset({Permission.KNOWLEDGE})
    )
    transport: SessionTransport
    async with local_transport(parameters, token) as transport:
        tools = await transport.list_tools()
        assert [tool.name for tool in tools] == ["search_technical_documents"]
        request = {
            "context": {"investigation_id": str(uuid4()), "request_id": str(uuid4())},
            "query": "schema",
        }
        reply = await transport.call_tool(tools[0].name, request)
        assert not reply.isError and reply.structuredContent
        assert reply.structuredContent["result"]["matches"]
        invalid = await transport.call_tool(tools[0].name, {"query": 1})
        assert invalid.isError and invalid.structuredContent
        assert invalid.structuredContent["error"]["code"] == "invalid_input"
        denied = await transport.session.call_tool(tools[0].name, request)
        assert denied.isError and denied.structuredContent
        assert denied.structuredContent["error"]["code"] == "unauthorized"
    # SDK context managers await session/task-group shutdown and reap the child process.
    assert (tmp_path / "audit" / "knowledge.jsonl").exists()


async def test_server_exits_cleanly_on_stdin_eof(tmp_path: Path) -> None:
    import asyncio
    import json

    from mcp.client.stdio import get_default_environment
    from mcp.types import LATEST_PROTOCOL_VERSION

    token = SecretStr("eof-review-token-" + "x" * 32)
    parameters = server_parameters("knowledge", token, None, tmp_path / "audit", frozenset())
    process = await asyncio.create_subprocess_exec(
        parameters.command,
        *parameters.args,
        env={**get_default_environment(), **(parameters.env or {})},
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin and process.stdout and process.stderr
    try:
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "review", "version": "1"},
            },
        }
        process.stdin.write((json.dumps(request) + "\n").encode())
        await process.stdin.drain()
        # Initialization includes importing the official SDK/dependencies. Match the
        # working client's startup allowance; the separate EOF shutdown bound stays 5s.
        response = json.loads(await asyncio.wait_for(process.stdout.readline(), 35))
        assert response["id"] == 1 and "result" in response
        process.stdin.close()
        await process.stdin.wait_closed()
        assert await asyncio.wait_for(process.wait(), 5) == 0
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
