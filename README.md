# ATLAS

Autonomous Data Forensics and Causal Reasoning Engine. This milestone delivers the
engineering foundation only: typed contracts, configuration/error conventions,
FastAPI probes, quality tooling and local infrastructure. Investigation features
are not implemented. See [architecture](docs/architecture.md), [decisions](DECISIONS.md)
and [project state](PROJECT_STATE.md).

Requires Python 3.12, uv, Make and a running Docker daemon with Compose.

```sh
cd /Users/vandanarangaswamy/Documents/atlas
make setup
# Only if .env does not already exist:
cp .env.example .env
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
