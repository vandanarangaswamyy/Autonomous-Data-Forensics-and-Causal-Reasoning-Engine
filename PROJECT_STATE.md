# Project state

## Current milestone — Phase 3.1 (2026-11-02)

The user authorized staged Phase 3. Completed only the first independently testable
milestone: durable UTF-8 enterprise source ingestion and provenance segmentation.
Full NLP, hybrid retrieval and GraphRAG are **not complete**. Stop after 3.1.
Runbook, architecture, milestone matrix and exact limits: docs/phase-3.md.

Delivered:
- Typed source/version/job/chunk contracts and DocumentStore port; async PostgreSQL
  adapter, bounded worker CLI and a separate knowledge database/non-superuser owner.
- Independent knowledge_0001 migration history: documents, original source blobs,
  immutable versions, durable jobs and chunks. Simulator migration/schema unchanged.
- Deterministic IDs, byte/revision hashing, concurrent dedup, incremental updates,
  explicit source kind/version/URI/observation time and database ingestion time.
- Fenced leased jobs, SKIP LOCKED claims, bounded backoff/three attempts, expired-worker
  recovery, stale completion rejection, queue recovery that does not hide ready work.
- Exact-source Unicode offsets and stable chunk IDs; recursive and Markdown segmentation.
  Source/config hashes and immutable version references preserve provenance. No lossy
  normalization or statistical NLP is claimed.
- Logical deletion removes chunks and fences workers; retained source revisions support
  provenance and idempotent reprocessing. This is not privacy erasure or vector reindexing.
- Operator CLI/make targets and private atomic bootstrap. Source mutations/owner settings
  are not exposed to MCP. Existing Phase 2 tools/security implementation untouched.

Verification:
- `make format check`: Ruff format/lint passed (111 Python files); strict mypy passed
  for **80 source files**; default suite **117 passed, 27 skipped, 1 warning in 8.77s**.
- `uv run --locked python -m scripts.test_documents_integration --all`:
  **144 passed, 0 failed, 0 skipped, 1 warning in 29.69s**, including all existing Phase
  1/2 PostgreSQL/Kafka/MCP security cases and all nine new actual PostgreSQL tests.
- Final `make test-all` also passed **144 tests, 0 skipped, 1 warning in 28.85s**;
  its existing runner now includes knowledge tests when private settings are provisioned.
- `make documents-integration`: **9 passed in 1.70s**. Thirteen new unit/contract cases
  and nine live cases cover IDs, provenance, dedup, versions, parallel claim exclusivity,
  retries/attempt limits, cancellation recovery, deletion/restore and rebuild idempotency.
- One existing upstream Starlette/httpx deprecation warning remains. Default skips are
  the 18 prior live cases plus nine ingestion cases; all pass with real settings enabled.
- `make infra-up`: PostgreSQL, Redis, Neo4j and Kafka actually healthy. Neo4j graph
  operations were not implemented/tested; service health does not verify GraphRAG.
- Independent Alembic drift check, source/wheel build and Compose validation passed.
- Fresh temporary checkout/virtualenv + disposable PostgreSQL passed existing setup,
  simulator migration/generation/MCP demo/reset and knowledge bootstrap/migration,
  actual submission/deduplication/work/status. Disposable container/volume removed.
- Real ingestion benchmark: fixture ingestion-v1, 100 documents per strategy, 234,190
  source bytes, offset-v1, one worker, 512-character bound. Recursive: **42.78 docs/s**,
  600 chunks, p95 **31.77 ms**; Markdown: **33.12 docs/s**, 700 chunks, p95 **40.23 ms**.
  Exact dataset hash, UTC timestamp and runtime/package versions are recorded in
  evaluation/results/ingestion-v1.json. These are ingestion measurements only.
- Intermediate checks exposed missing SQLAlchemy asyncio/greenlet support and new test
  typing issues; fixed before final passes. No existing dependency versions were upgraded.

Remaining/unverified:
- PDF/OCR/S3/connectors, normalization with offset maps, spaCy/Hugging Face models,
  technical entity extraction/resolution/relationships/classification, sentence/SQL/
  incident-aware chunkers are deferred to 3.2. Eight source kinds are operator-declared,
  not specialized NLP parsers.
- VectorStore/pgvector/embeddings/index lifecycle/BM25/RRF/reranking/filtering/query
  expansion, retrieval MCP extensions and 50+ labeled queries are deferred to 3.3.
- Neo4j evidence graph, simulator lineage import, bounded graph traversal and graph
  expansion are deferred to 3.4. No bitemporal agent memory or investigator was added.
- Recall/MRR/nDCG, retrieval latency and entity precision/recall have **not been measured**;
  required components/labels do not exist yet. No invented retrieval benchmark results.
- No Teradata Vector Store integration or compatibility claim. Existing live Teradata
  Phase 2 limitations remain. Knowledge owner is local operator access, not tenant auth.
- Local batches require repeated invocation/scheduling for future retries. No heartbeat,
  production scheduler, object-store recovery, hard purge, retention/GC, global quota
  or collection-authorized retrieval role exists. Expensive NLP needs a reviewed lease
  renewal/resource-budget design before integration.

Next boundary: 3.2 extraction on immutable source revisions with explicit model versions,
confidence/method/offset maps and labeled precision/recall data. Do not start the next
milestone automatically. Phase 3.1 changes are recorded in the durable-ingestion milestone commit.

## Completed milestones

### Foundation and enterprise simulator

Foundation shipped as 2d41cc0; enterprise simulator shipped as 7214cfd on main.
Python 3.12 uv monorepo, FastAPI probes/settings/errors, shared investigation contracts,
quality tooling, Compose infrastructure, 30-table simulator, Alembic migration 0001,
deterministic profiles, real ingestion/finance/churn transformations, six faults,
private ground truth, reset/replay, three benchmarks and Kafka outbox/inbox are implemented.
See docs/simulator.md and docs/simulator-data-model.md for full scope and examples.

Reference small baseline: revenue USD 49,557.96, reported payments 681, churned 16.
Scenario A missing partitions lowers revenue to 45,009.98; B incorrect join inflates
churned to 280; C duplicate events raises payment count to 737. Small/medium/large source
generation was exercised; full medium/large database loading remains unbenchmarked.

### Database abstraction and custom MCP infrastructure

Implemented in the database/MCP milestone commit, together with the skeptical review fixes below.

- Storage-neutral async AnalyticsBackend protocol and Pydantic query/schema/statistics/
  plan/dependency/capability contracts in core/contracts packages.
- PostgreSQLAnalyticsBackend with async psycopg, actual metadata/keys/indexes, aggregate
  column profiling, non-executing EXPLAIN, foreign-key dependencies and per-operation
  runtime capability verification. All protocol methods exercised on real PostgreSQL.
- Conservative dialect-aware SQLGlot AST policy, scope-resolved physical sources,
  table/type/function allowlists, no multi-statements/DML/DDL/locks/recursive CTEs,
  bound values and correctly quoted identifiers. Native Teradata parameter order is
  derived from generated SQL, including CTEs, with a dedicated regression test.
- Independent PostgreSQL SELECT-only role checks, read-only transactions, fixed search
  path, statement/lock/connect timeouts, row bounds and encoded response-size limits.
- TeradataAnalyticsBackend uses the official optional teradatasql driver for query,
  ColumnsV metadata, aggregates and EXPLAIN paths; supports bounded fetch/timeouts and
  cancellation cleanup. It refuses execution without SELECT-only account attestation.
  All live_verified flags remain false. Dependency mapping is explicitly unsupported;
  unavailable primary-key/index fields are null. No fake Vantage success is returned.
- Four independent official MCP SDK stdio servers: analytics, lineage, telemetry and
  knowledge. Nine typed tools with versioned schema exports, structured errors,
  application request/investigation/trace IDs, deadlines, explicit permissions and audits.
- Application bearer-session authorization from request metadata, separate from MCP;
  default permissions empty, no caller-supplied grants or hint-based authorization.
- Gateway registry, transport discovery using trusted permission mappings, input/output
  validation, remote schema-reference rejection and response correlation checks.
- Durable lock-protected file audit/budget state, request replay protection, bounded
  concurrency, cancellation and graceful shutdown. State is separate from simulator
  evidence and evaluator oracle files. No new SQL schema/migration was needed.
- Working official-SDK client launching/discovering/invoking all four servers against
  the real synthetic dataset; child environments contain only provisioned observer/session
  settings, not owner credentials or evaluator files. Knowledge gets no DB credentials.
- Bounded lineage traversal, actual pipeline/incident queries and real keyword retrieval
  over three packaged public documents. No vector/RAG/graph implementation is implied.
- Typed remote transport extension interface; remote HTTP/TLS/OAuth deployment remains
  unimplemented. Local SDK sessions and stdio cleanup are tested end to end.

## Skeptical review: verified status

Full requirement matrix and findings: docs/reviews/database-mcp-audit.md.
Review fixes are included alongside the database/MCP phase in the same milestone commit.

Confirmed fixes: preparation deadlines, live-owner concurrency leases, bounded audit
locks, safe remote error/name handling, distinct invalid_output, column-level write
privilege checks, composite PK/INCLUDE ordering, wide/truncated metadata handling,
repeated native cancellation cleanup, strict Teradata TLS options, atomic private
bootstrap files and vendor-neutral result utilities. Driver doubles now patch only
the driver loader rather than global import machinery. No agents were added.

- `make test-all`: **122 passed, 0 failed, 0 skipped, 1 warning** with real PostgreSQL,
  Kafka and MCP subprocess integrations enabled. Latest recorded run: 28.04 seconds.
- Final `make check`: **104 passed, 18 skipped, 1 warning** (8.54 seconds); these skips
  are the live cases that all passed under `make test-all`. Strict mypy passed for **62 source files**;
  Ruff lint/format passed. Detailed command evidence is in the review report.
- Live cases include genuine database timeouts/recovery, binding/injection resistance,
  all nine MCP tools, permission denial, 1004-column metadata, PK(value,id) with a covering
  column, column-only UPDATE grant rejection, plus all existing simulator/Kafka scenarios.
- Regression tests cover late synchronous admission, busy file locks, live/dead owner
  leases, sensitive remote errors, malformed outputs, unknown audit names, repeated
  cancellation, private-file replacement/symlinks and actual SDK EOF exit code zero.
- Real clean-source setup passed in a temporary checkout/disposable PostgreSQL container:
  setup, sim-bootstrap, sim-migrate, sim-generate, mcp-demo (all 9 tools), sim-reset.
  The test container/volume were removed. Reproduce with
  `uv run --locked python scripts/verify_clean_mcp_setup.py`.
- Existing-checkout setup/bootstrap/migration/MCP demo passed; `make dev` served /health
  and /ready with HTTP 200 and was stopped afterward. Alembic drift check passed.
- Schema export, source/wheel builds, pre-commit config, whitespace and Compose validation
  passed; all four actual development containers remain healthy. Runtime files are ignored.
- The official optional Teradata driver installs/imports; driver wiring/cancellation/TLS
  option tests use explicit doubles. **No live Vantage connection or grant/certificate/
  timeout/cancellation verification occurred.** Unsupported dependency lookup remains explicit.
- The new EOF test initially used a too-short 5s startup timeout. Startup now matches
  the production SDK client's 35s allowance, with a separate unchanged 5s zero-exit EOF
  shutdown bound and the production filtered child environment. Three consecutive
  focused runs and the latest full suite passed afterward.
- Only warning: existing upstream Starlette/httpx TestClient deprecation, not a test failure.

Part A is complete for the supported PostgreSQL subset and partial/live-unverified for
Teradata. Parts B/C are verified for trusted local stdio operation, not multi-tenant or
remote deployment. Part D is verified with the above exception for unavailable live
Teradata. No hidden production mocks were found; the public keyword corpus is deliberately
limited. Do not equate that scope with a production autonomous investigation platform.

## Phase 2 security re-verification — 2026-10-30

Reviewed committed source at 21711a9 with an initially clean working tree; all seven
requested fixes were present. Rechecked gateway/audit/native cleanup together rather
than relying on isolated regression successes. No further production defect confirmed;
this pass adds seven test cases and strengthens setup/demo assertions. Changes are included in the Phase 2 re-verification commit; Phase 3 has not started.

Exact final verification command: `make format check test-all mcp-integration`.

- Format/check: 92 files; Ruff lint passed; strict mypy passed for 62 source files.
- Default: **104 passed, 18 skipped, 1 warning in 8.54s**.
- Complete suite, real services: **122 passed, 0 failed, 0 skipped, 1 warning in 28.04s**.
- Separate actual PostgreSQL/Kafka/MCP integration rerun: **18 passed, no skips or
  warnings in 19.46s**. Breakdown: 10 analytics/MCP, 1 Kafka, 7 simulator/observer.
- Default skips are those same 18 opt-in cases (test environment URLs unset by default),
  not untested failures. Warning remains upstream Starlette/httpx TestClient deprecation.
- Composed Teradata adapter/gateway/shared-ledger test: repeated cancellation, request
  expiry and gateway shutdown keep slots occupied during blocked execute AND blocked
  close. Finish audit/re-admission occur after close terminates. Uses an explicit
  controlled driver double; **actual Vantage/native-driver behavior is unverified**.
- Added cumulative time/row budget persistence, synchronous-handler deadline and known
  remote-error-code sanitization coverage. Existing token/permission/replay/audit-lock/
  output-validation/EOF shutdown tests all remain green.
- Real PostgreSQL query and authentication errors over actual MCP return fixed safe
  errors in both text and structured content. Canary secrets, SQL, connection URL,
  observer password and driver diagnostics were absent from responses and audit.
- Standalone `make mcp-demo` executed via captured subprocess and all nine bodies checked:
  orders schema 6 columns; 4 currency groups; 605 orders / 0 null totals; real EXPLAIN;
  15 upstream / 10 downstream lineage edges; 1 execution; 1 incident; 3 public documents.
  IDs, provenance and body relationships checked; live tests also compare query/profile
  to direct backend results. No status-only verification claim.
- Clean source + fresh virtualenv + disposable PostgreSQL again passed make setup,
  sim-bootstrap, sim-migrate, sim-generate, mcp-demo (strengthened content checks),
  sim-reset; disposable container/volume removed. Existing four Compose services healthy.
- `make compose-validate`, `make mcp-schemas`, `uv build`,
  `uv run --locked alembic check`, `git diff --check` passed. Schema exports unchanged.

Closure: **PostgreSQL/trusted local-MCP scope VERIFIED; overall Phase 2 PARTIAL**.
Teradata dependencies are MISSING; keys/indexes/numeric mean are MISSING within partial
metadata/profiling paths. Live connectivity, effective grants, dictionary compatibility,
parameters, TLS certificates, timeout and native cancellation/cleanup are UNVERIFIED.
Do not mark the entire phase complete without accepting those exceptions or supplying
live verification and missing implementation. Remote HTTP deployment and production
multi-tenant admission remain outside delivered local scope. The full per-requirement
VERIFIED/PARTIAL/UNVERIFIED/MISSING matrix, exact command evidence and transient
verification-code failures are in docs/reviews/database-mcp-audit.md.

Next steps: obtain a real Vantage test environment and resolve unsupported mappings;
authorize scoped principal/investigation admission plus durable quota/audit operations
before untrusted multi-user use. That Phase 2 review did not authorize Phase 3; the later user request authorized staged Phase 3 above.

## Current local state

PostgreSQL host port 55432; Kafka 127.0.0.1:9092. Simulator remains on its seed-42 small
baseline. `.env` holds private local simulator owner/observer and separate knowledge-owner credentials. `.atlas-private` holds
evaluator records. `.atlas-runtime` contains mode-0600 application audit/budget ledgers
inside a mode-0700 directory. All are ignored; no secrets/runtime state belong in Git.
No unrelated project was modified. Source inventory: docs/file-inventory.md.

## Explicit limits

- No live Vantage validation: connectivity, dictionary compatibility, effective role
  privileges and native timeout/cancellation behavior remain unverified. Operator role
  attestation is not effective-role inspection. Teradata keys/indexes/numeric mean and
  dependency catalog mapping are not implemented.
- PostgreSQL currently admits ordinary base tables, not views/foreign/partitioned parent
  relations. SQL policy is deliberately a reviewed subset; denied syntax is not silently
  passed through. Encoded byte limits apply after cells have been decoded.
- Gateway budgets are per principal/investigation per ledger/hop, not distributed cluster
  quotas. Audit ledger is capped at 20 MB and requires controlled rotation that preserves
  budget/replay state. Live owners keep slots during cleanup; PID reuse and legacy unfinished
  records conservatively require operator recovery. Local locks are not distributed coordination.
- The trusted operator provisions local tokens and permissions. Multi-user authentication,
  investigation ownership/admission, token lifecycle and remote OAuth/TLS are future work;
  unlimited caller-created investigation IDs must not become a budget escape in production.
- Same-OS-user filesystem access is not sandboxed. Future agents need a separate identity,
  no evaluator/owner mounts or commands, and only scoped read tools. Current tool APIs
  expose no filesystem/evaluator lookup and the public corpus omits benchmark answers.
- Knowledge retrieval is a small keyword index, not pgvector/hybrid/GraphRAG. No agents,
  LangGraph orchestration, hypothesis execution, temporal graph memory, Celery scheduler,
  frontend, Kubernetes runtime or multi-user API was added.

## Next integration boundary

Phase 3.2 is the next staged implementation boundary; see docs/phase-3.md. Add typed
NLP outputs and source-offset maps before embeddings/retrieval or graph construction.
Keep investigation state, source knowledge and evaluator ground truth separate.
Phase 2 admission/audit/remote/Teradata gaps remain explicit and require separate work.
Stop after the completed Phase 3.1 milestone; no autonomous investigation engine or
long-term bitemporal agent memory is implemented.
