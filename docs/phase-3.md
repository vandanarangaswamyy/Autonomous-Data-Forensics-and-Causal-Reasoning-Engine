# Phase 3: bounded milestones

The authorized Phase 3 scope is divided into independently testable milestones.
3.1 and increment 3.2a are implemented. See [3.2a verification and stopping point](phase-3.2a.md). The full NLP/hybrid/GraphRAG phase is **incomplete**.
There is no autonomous investigator or long-term bitemporal agent memory.

| Milestone | Deliverable | Status |
|---|---|---|
| 3.1 | Durable UTF-8 ingestion, source lifecycle, provenance chunks, asynchronous jobs | Implemented and tested |
| 3.2 | PDF extraction, spaCy/Transformers, technical entities/aliases/relationships/classification, all five chunkers and labeled corpus | Partial: source extraction/mapping 3.2a verified; NLP 3.2b deferred |
| 3.3 | Embeddings, VectorStore/pgvector index lifecycle, BM25/dense/RRF/filtering/budgets/reranker, MCP retrieval integration, 50+ labeled query benchmarks | Deferred |
| 3.4 | Neo4j evidence graph, simulator lineage import, bounded graph retrieval and four-way benchmark ablation | Deferred |

No empty classes, fake embeddings, canned graph results or invented retrieval scores
stand in for those deferred components. Existing Phase 2 MCP tools remain unchanged.

## Phase 3.1 baseline boundary (historical)

`atlas_contracts.documents` defines source descriptors, payloads, immutable ingestion
receipts, leased jobs and chunks. `atlas_core.documents.DocumentStore` is a typed port;
`atlas_database.documents.PostgreSQLDocumentStore` implements it with async SQLAlchemy
and psycopg. `atlas_documents` owns segmentation, worker orchestration and the CLI.
The worker depends on the port. No document service imports simulator/evaluator code.
No ingestion mutation is exposed to MCP or future investigation agents.

A dedicated `atlas_knowledge` database has an independently provisioned non-superuser
owner and its own `knowledge-alembic.ini` migration history. Simulator migrations,
observer credentials and API state are not reused. Five tables hold documents, source
blobs, immutable revisions, jobs and chunks. All SQL uses bound parameters/static ORM
expressions. The knowledge owner is a local operator credential, not a retrieval role.
A read-only projection/role and collection authorization must precede MCP integration.

Source identity is UUIDv5 of the exact `(collection, source_uri)` tuple under a fixed
namespace. Locator spelling is deliberately not guessed/canonicalized; aliases are
future NLP work. Credentials, query strings, fragments and control characters are
rejected in locators; nothing is fetched from a URL. UTF-8 Markdown, TXT and SQL source
bytes are accepted, at most 1 MB. The eight requested source kinds can be declared by
an operator: technical, sql_schema, sql_transformation, pipeline_log, incident,
quality_alert, migration and telemetry. This is a declared kind, **not NLP classification
or specialized parsing**. PDF, OCR, remote crawlers and S3 adapters are not implemented.

Original bytes are SHA-256-addressed in PostgreSQL, allowing cross-source byte dedup
without merging provenance. A transaction writes the blob, immutable version and queued
job together. Identical current bytes **and descriptor/configuration** return the same
version/job. `observed_at` and external `source_version` are caller-supplied provenance;
repeat them unchanged for an idempotent replay. Internal version numbers increase for
updates, including metadata/processor changes and restoring a tombstone. Reverting
content creates a new revision. `ingested_at` records database ingestion time. There
is no automatic resolution of conflicting source claims: different sources/versions
remain independent evidence, and are not interpreted as causal facts.

Document row locks serialize per-source updates. Claiming uses `FOR UPDATE SKIP LOCKED`
and a 60-second default lease, accepted bounds 5–300 seconds. Every attempt gets a new
random fencing token. Completion/failure must match the current document generation,
job token and live lease. Updates/deletion supersede old work. Failed transient attempts
retry after 2^attempt seconds, at most three claims; invalid text fails permanently.
Expired third attempts become failed without hiding later ready jobs. Recovery scans
at most 100 exhausted jobs per claim before a retryable queue_busy error. There is no
heartbeat: work exceeding its lease must be reclaimed rather than published. Increase
bounds/redesign heartbeat before introducing expensive PDF/NLP processing.

`atlas-documents work` processes a bounded batch and exits when no job is ready; it is
not a daemon or Celery scheduler. Retry delay requires a later invocation by an operator
or scheduler. SIGINT/async task cancellation does not acknowledge work. A worker that
dies leaves its lease for a later worker to reclaim. Duplicate execution is possible;
fenced atomic commits make effects idempotent. Storage failures use safe fixed errors;
raw exceptions, content, credentials and connection URLs are not persisted in job errors
or returned by the CLI. Pool checkout/connect/statement/lock operations are bounded.

The recursive segmenter prefers paragraph/line/space boundaries with hard character
limits. The Markdown segmenter also preserves ATX section hierarchy and ignores headings
inside backtick/tilde fences. Neither is sentence-aware or SQL-statement-aware. Text is
not normalized: offsets are zero-based Python Unicode character ranges `[start,end)`
in the exact UTF-8 decoded source, including BOM/CRLF if present. They are not byte or
PDF offsets. Chunk IDs derive from document/version/configuration and range. Chunks
carry their source and processor hashes; the original byte payload, descriptor, external
version, observation timestamp and ingestion timestamp resolve through the immutable
version. The adapter validates byte hash, processor identity, exact contiguous coverage,
range bounds, chunk IDs and text slices before commit. Processor section labels are not
a security boundary. Future normalization needs an explicit source-offset mapping.

`reprocess` rebuilds chunks for the current source revision from preserved bytes. It
creates a new generation/job and atomically replaces that revision's chunks on success;
it is **not vector reindexing**. Existing successful chunks remain until commit. Future
retrieval must check active document/version/generation and successful job state.
`delete` immediately removes every chunk for that document, tombstones the source and
fences pending workers. Original blobs and revision metadata are intentionally retained
for provenance. This is **not a privacy-erasure/purge API**. Retention and blob garbage
collection are deferred; there is no tenant isolation, encryption-at-rest policy, S3,
global queue quota or production scheduler in this milestone.

## Runnable local setup

Requires the existing private local Compose settings. Do not put owner settings or
source-file mounts into MCP/agent subprocesses. No real credentials belong in examples.

```sh
make setup
make infra-up
make documents-bootstrap
make documents-migrate
uv run --locked atlas-documents submit \
  services/mcp/src/atlas_mcp/public_documents/payment-ingestion.md \
  --collection retail --source-uri atlas://docs/payments --source-version v1 \
  --kind technical --media-type text/markdown --strategy markdown \
  --observed-at 2025-01-01T00:00:00+00:00
make documents-work
# Substitute the IDs returned by submit:
uv run --locked atlas-documents status JOB_UUID
uv run --locked atlas-documents reprocess DOCUMENT_UUID
make documents-work
uv run --locked atlas-documents delete DOCUMENT_UUID
```

The bootstrap saves generated knowledge owner settings using the existing atomic
mode-0600 environment-file writer. It refuses an existing database owned by another
role and refuses to guess an existing owner's password. Re-running it does not reset
data or rotate passwords. Knowledge migrations run independently:

```sh
uv run --locked alembic -c knowledge-alembic.ini check
make documents-integration
uv run --locked python -m scripts.test_documents_integration --all
make format check
uv run --locked python -m scripts.benchmark_ingestion
uv run --locked python scripts/verify_clean_mcp_setup.py
```

The last command creates a fresh source copy/virtualenv and disposable PostgreSQL,
runs existing Phase 2 setup/demo plus knowledge bootstrap/migration, actually submits a
source twice, checks deduplication, runs the worker and verifies succeeded status. It
removes only its own container/volume. It does not establish a production deployment.

## Verification and evaluation limits

Unit/contract tests verify deterministic IDs/hashes, namespace separation, private
payload repr, locator/media/size/time validation, Unicode/CRLF offsets, deterministic
chunk bounds/coverage and fenced Markdown hierarchy. Actual PostgreSQL tests verify
migration upgrade/downgrade/drift, concurrent submit dedup, immutable versions, blob
sharing without identity merging, parallel claim exclusivity, retries/backoff/caps,
expired leases and stale-worker fencing, interrupted-worker recovery, provenance
rejection, logical deletion, restore and repeatable reprocessing.

Tests do not mock the store or PostgreSQL driver. One cancellation test uses an explicit
application pause at completion, and recovery tests deliberately expire DB lease times;
those are fault-injection seams, not claims that an actual process crash was induced.
Existing actual PostgreSQL/Kafka/MCP and Phase 2 security regressions are rerun too.
Neo4j is healthy in Compose but no graph integration or Neo4j query is claimed here.

The measured ingestion benchmark uses 100 deterministic synthetic sources, a single
worker, 512-character chunks, and actual local PostgreSQL submit/segment/commit/status
operations. It excludes setup/migrations. The fixture hash, exact package/runtime
versions, UTC run timestamp and results are committed in
[evaluation/results/ingestion-v1.json](../evaluation/results/ingestion-v1.json).
It records no NLP/model or retrieval-quality metrics. On this run recursive achieved
42.78 documents/s (p95 31.77 ms), Markdown 33.12 documents/s (p95 40.23 ms). This single
local sample does not establish retrieval quality or production throughput superiority.

Recall@5/@10, MRR, nDCG@10, retrieval latency, entity precision/recall, 50+ evaluation
queries, and lexical/vector/hybrid/graph ablations are **not available**: their index,
models, labels and graph are deferred. No statistical model was downloaded or run.
No Teradata Vector Store integration exists or is claimed. The next implementation
boundary is 3.2, operating on immutable revisions/chunks with explicit NLP model/version,
confidence, offset mappings and entity resolution; do not implement agent memory.

Final exact verification results:

| Command | Outcome |
|---|---|
| `make format check` | Ruff passes (111 files); strict mypy passes (80 source files); 117 passed, 27 skipped, 1 warning in 8.77s |
| `make test-all` | 144 passed, 0 skipped, 1 warning in 28.85s |
| `uv run --locked python -m scripts.test_documents_integration --all` | 144 passed, 0 skipped, 1 warning in 29.69s |
| `make documents-integration` | 9 passed in 1.70s |
| `uv run --locked alembic -c knowledge-alembic.ini check` | No new upgrade operations |
| `uv build` | Source and wheel built |
| `make compose-validate` / `make infra-up` | Valid; all four actual services healthy |
| Clean setup verifier | Existing setup/demo and new ingestion submit/dedup/work/status passed |

The default 27 skips are exactly the 18 existing opt-in integration cases plus the
nine `tests/integration/test_documents.py` cases. The full run enables all and skips none.
The single warning is the existing Starlette/httpx TestClient deprecation. Intermediate
checks caught missing greenlet support and new annotation/async test assertion errors;
these were corrected before the successful complete runs above. No final failure remains.
Local credential-content scanning passed across all 138 source files; private/runtime
files remain ignored. See docs/file-inventory.md for all source files. Changes are recorded in the Phase 3.1 durable-ingestion milestone commit.
