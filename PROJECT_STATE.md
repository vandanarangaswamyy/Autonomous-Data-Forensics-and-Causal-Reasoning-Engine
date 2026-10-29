# Project state

## Completed milestones

### Foundation

Python 3.12 uv monorepo, FastAPI factory and probes, shared settings/errors and typed
investigation contracts, Ruff/mypy/pytest/pre-commit, Make targets and local infrastructure.
The API still has no business endpoints or external-storage dependency.
Foundation was pushed to main as 2d41cc0. The simulator milestone follows it;
consult Git history and the remote branch for publication status.

### Enterprise simulator and fault-injection engine

- Dedicated `atlas_simulator` PostgreSQL database; 30 SQLAlchemy 2.0 Core tables and
  explicit Alembic migration 0001 with FKs, uniqueness/check constraints and indexes.
- Customers/accounts, orders/payments/events, subscriptions/renewals, products/inventory,
  support, marketing attribution, financial reports, schema history, lineage, pipelines,
  operational telemetry and durable event delivery bookkeeping.
- Storage-independent seeded generator, configurable dates/sizes, small/medium/large
  profiles, weekly/annual patterns, regional/product differences, churn, renewals and
  late arrivals. No LLM dependencies.
- Three real transformations: parsed/watermarked payment ingestion, SQL financial
  aggregation with FX join, and SQL cancellation/subscription churn join. Persisted DAG,
  dependencies/downstream consumers, table/column lineage, schema contracts, run status,
  counts, timings and quality metrics. Cycles rejected before execution.
- Six configurable deterministic faults: schema drift, delayed arrivals, duplicate
  transactions, incorrect joins, missing partitions, incorrect currency conversion.
  All affect actual inputs/transformation behavior and downstream reports.
- Operator `atlas-sim` CLI for generation, injection, reset, replay, scenarios,
  evaluator ground truth, schema export, Kafka publication and consumption.
- Three benchmark JSON scenarios plus six fault examples; actual affected datasets,
  expected evidence, known causes and deterministic reset procedures.
- Evaluator-only private file registry, stable fault IDs, wall-clock injection history,
  metric impacts, pending-intent recovery, serialized reset/replay and private permissions.
  No oracle tables, IDs or labels in public telemetry; observer role is SELECT-only.
- Four versioned public Kafka event types, checked-in JSON Schema, transactional outbox,
  acknowledged publication, stable-ID replay, durable consumer receipts and atomic handlers.
- Bootstrap/integration scripts, Make targets, runbook and complete schema documentation.

## Verification results

- Ruff formatting/lint: passed. Strict mypy: passed, 33 source files.
- `make check`: 29 passed; 8 opt-in live tests skipped in this invocation, then executed
  separately with real services via `make sim-integration`: all 8 passed.
- Unit/contracts verify deterministic data, every FK, database constraints, actual SQL
  results, DAG ordering/cycles, all six fault impacts, complete baseline regeneration,
  replay, pending recovery, oracle permissions, event validation and outbox/inbox behavior.
- PostgreSQL integration verifies all six faults/reset/replay and migration/schema agreement
  in isolated temporary schemas. Observer permission tests reject writes, outbox access,
  schema creation and PostgreSQL server-file reads. Kafka test verifies durable replay
  deduplication across new consumer instances; temporary topics/schemas cleaned up.
- All three scenario CLIs ran on the dedicated local PostgreSQL database. Baseline:
  revenue USD 49,557.96; reported payments 681; churned 16. A: revenue 45,009.98;
  B: churned 280; C: payments 737. Fixture restored to baseline afterward.
- Real Kafka CLI smoke: 185 outbox events published, 185 received/applied; 185 replayed
  and received with 0 additional effects. Local observation topic/receipts remain.
- Pure generator smoke: small 3,865 rows; medium 53,347; large 785,024. Large generated
  in about 2.4 seconds on this machine; this is not a database-load throughput benchmark.
- Alembic upgrade and drift check passed on local PostgreSQL. Upgrade/downgrade/schema
  comparison tested on SQLite. `uv build` passed; archives inspected to exclude private
  environment/oracle files and include simulator code/source migration.
- Pre-commit configuration validation and `git diff --check` passed.
- Compose validation passed and all four containers are actually running/healthy.
  Fixed intermittent IPv6 localhost Kafka connection failure by advertising IPv4
  127.0.0.1 to match Docker's loopback binding, then reran live integrations successfully.
- Existing upstream Starlette/httpx TestClient deprecation warning remains (no failures).

## Current local state

- PostgreSQL host port 55432; Kafka 127.0.0.1:9092; containers remain running.
- `.env` holds local generated owner/observer credentials; ignored, mode 0600.
- `.atlas-private` holds evaluator records; ignored, directory 0700/files 0600.
- Simulator database contains seed-42 small baseline, with public outbox/inbox history.
- No existing unrelated project was modified. See docs/file-inventory.md.

## Limits and next boundary

One private directory/operator writer per fixture database; local file locks do not
coordinate unrelated machines. PostgreSQL and private registry are separate stores;
interrupted intent requires reset. One active fault is allowed. Some no-op parameter
choices do not cross reporting thresholds; shipped benchmark configurations do.
Generation is in-memory, database inserts are batched. Medium/large source generation
was exercised; medium/large full database loading and production performance remain
unbenchmarked. SQLite unit tests emulate decimals; PostgreSQL is the reference backend
for reported benchmark financial results. Pipeline execution is a synchronous fixture
materialization, not a continuously scheduled production ingestion service.

Observer database isolation is verified. Code running as the evaluator OS user can read
its files; future agent runtimes must have a different identity, no private mounts,
no evaluator CLI/scenario access and only observation credentials. That deployment
boundary cannot be verified until an investigation runtime exists.

Next authorized milestone can implement typed read-only database/lineage/telemetry ports
and adapters consuming the public datasets/events. Keep the private evaluator registry
outside that boundary. SQL diagnostic policy/timeouts/limits must precede agent-accessible
SQL. No investigation agents, MCP business tools, RAG/GraphRAG, temporal knowledge memory,
Celery scheduler, agent causal scoring, frontend or Kubernetes runtime was added.

See docs/simulator.md for exact runnable commands. Stop after this milestone.
