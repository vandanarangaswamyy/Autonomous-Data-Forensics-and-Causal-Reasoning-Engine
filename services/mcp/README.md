# Authorized MCP infrastructure

Implemented: four official-SDK stdio servers (analytics, lineage, telemetry, knowledge),
nine typed read tools, permission/budget/concurrency gateway, durable audit traces,
local transport and working client. See [the runbook](../../docs/database-mcp.md).

Run `make mcp-demo` against the local synthetic dataset. Remote transports have an
extensible typed interface; remote authentication/deployment is not implemented.
