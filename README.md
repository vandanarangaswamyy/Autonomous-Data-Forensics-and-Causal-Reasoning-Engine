# ATLAS

Autonomous Data Forensics and Causal Reasoning Engine. Implemented milestones:
engineering foundation, enterprise simulator/fault-injection engine, and database/MCP infrastructure. The simulator
provides 30 related tables, deterministic business data, executable pipelines, six faults,
three benchmarks and Kafka telemetry with replay/deduplication. Investigation agents
and RAG remain unimplemented. PostgreSQL diagnostics and nine authorized MCP tools are implemented;
Teradata driver paths remain live unverified.

See [database/MCP setup](docs/database-mcp.md), [simulator setup and examples](docs/simulator.md), [architecture](docs/architecture.md),
[decisions](DECISIONS.md) and [project state](PROJECT_STATE.md).

Requires Python 3.12, uv, Make and a running Docker daemon with Compose.

```sh
cd /Users/vandanarangaswamy/Documents/atlas
make setup
# Only if .env does not already exist:
cp .env.example .env
chmod 600 .env
# Generate two independent passwords and put them in .env:
openssl rand -hex 32
openssl rand -hex 32
# Set POSTGRES_PASSWORD and NEO4J_PASSWORD to those values.
make compose-validate
make infra-up
make dev
```

A private .env with generated credentials was created during foundation verification;
keep it local. On a fresh checkout follow the steps above. API development runs on
the host and does not require infrastructure for its two probe endpoints.

```sh
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
make format
make check
make hooks
make infra-down
```

`infra-down` preserves named volumes. Changing PostgreSQL/Neo4j passwords in .env
will not rotate credentials already persisted in those volumes. Swagger is at
http://127.0.0.1:8000/docs. This foundation is not production deployable.

Initialize the dedicated simulator after infrastructure starts:

```sh
make sim-bootstrap
make sim-migrate
make sim-generate
uv run --locked atlas-sim scenario evaluation/scenarios/a_missing_partitions.json
make sim-reset
make sim-integration
```

Run the authorized MCP client against the simulator:

```sh
make mcp-demo
make mcp-integration
make test-all
```

The [database/MCP review](docs/reviews/database-mcp-audit.md) records confirmed fixes,
requirement coverage and remaining production boundaries.
