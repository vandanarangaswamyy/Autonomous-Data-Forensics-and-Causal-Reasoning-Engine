# Architecture

Foundation status: FastAPI probes, contracts, configuration and error handling only.
Reserved directories explicitly identify unimplemented scope.

One uv-managed Python distribution packages atlas_api, atlas_core and atlas_contracts
from explicit source roots. Avoid independently versioned internal packages until
needed. Future orchestration depends on typed Protocol ports and contracts; database
adapters implement those ports. No orchestration imports of storage drivers.
Keep routes thin, application services responsible for transactions, repositories
responsible for storage access, and SQLAlchemy 2.0 schemas managed through Alembic.

The API and future Celery workers justify separate processes for distinct failure,
concurrency and scaling behavior. Other services remain independently testable modules
until operational needs justify deployment separation. services/documents is added to
the requested layout for explicit document ingestion/NLP ownership. Frontend, Kubernetes,
evaluation and remaining services have documentation only, no runnable implementation.

PostgreSQL will own investigations, hypotheses, evidence metadata, tool audits and
durable checkpoints; pgvector plus keyword indexing will support hybrid retrieval.
Neo4j owns long-term bitemporal knowledge, separate from execution state. Knowledge
assertions require valid-time and transaction-time intervals, immutable provenance and
supersession history. Current Evidence observation/recording timestamps do not implement
that graph. Redis is transport/cache, Kafka carries ingestion events, and S3-compatible
storage holds original documents and evidence. Teradata is behind a pluggable adapter.

Future diagnostic SQL must pass a dialect-aware parsed policy, use read-only credentials
and transactions, reject multiple statements/side effects, enforce statement timeouts
and bounded results. Every allowed, denied or failed tool call requires durable audit
with investigation/execution IDs. No diagnostic SQL executor is implemented now.

Evidence records include source URI, SHA-256 digest, timestamps and optional execution
provenance. Supported/rejected hypotheses require evidence IDs; future services must
resolve them, validate integrity and investigation ownership, and assess diagnostic
outcomes. LLM confidence is never verification. Lineage is not causal proof.

Future execution must persist intent before enqueueing with a transactional outbox,
use idempotency keys, bounded backoff retries, revision checks, durable checkpoints and
cooperative cancellation. Evaluation must use isolated reproducible faults with known
root causes; RAGAS supplements causal accuracy scoring.

Settings use ATLAS_ environment variables. Add SecretStr for future secret settings.
API errors share code, safe message and request_id with an X-Request-ID header.
Server-generated IDs prevent client-controlled correlation values. Validation inputs
and exception text are not reflected. Unexpected errors log correlation only.
ATLAS_LOG_LEVEL is reserved configuration; telemetry exporters are not implemented.

/health reports process liveness; /ready reports startup lifecycle completion and 503
when unavailable. Neither claims infrastructure connectivity: no external dependency
is used by this API yet. Add bounded dependency checks when required by real operations.
Authentication/authorization is a prerequisite for future enterprise APIs.

Compose is local development infrastructure with persistent named volumes, loopback
ports and health checks. PostgreSQL defaults to host port 55432 to avoid an existing
5432 listener. Kafka exposes localhost:9092 for host clients and kafka:29092 for
containers. Host ports are configurable. Redis/Kafka plaintext access, single-node
operation and unbounded resource allocation are development choices. Production needs
TLS, HA, backups, resource sizing and reviewed image digests. pgvector is available
in the image; extension activation requires a future migration. No migrations exist.
Unused AI/storage dependencies are deferred until their first implementation.

References: [uv project management](https://docs.astral.sh/uv/guides/projects/) and
[Apache Kafka Compose example](https://github.com/apache/kafka/blob/trunk/docker/examples/docker-compose-files/single-node/plaintext/docker-compose.yml).
