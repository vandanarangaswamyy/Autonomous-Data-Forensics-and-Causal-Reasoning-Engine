# ATLAS engineering rules

Read PROJECT_STATE.md, DECISIONS.md and docs/architecture.md before work.
Stop at the authorized milestone. Do not implement agents, RAG, graph memory or MCP
business tools without explicit instruction. Reserved directories are not features.

- Use Python 3.12, uv and the committed lockfile. No requirements.txt.
- Keep modules independently testable; separate processes only for operational reasons.
- Use typed interfaces and dependency inversion. Orchestration imports ports/contracts,
  never PostgreSQL, Neo4j or Teradata implementations.
- Use Pydantic v2 contracts and SQLAlchemy 2.0 when persistence is introduced.
  All schema changes require Alembic migrations.
- Keep investigation state separate from long-term knowledge memory.
- Associate every tool call with an investigation UUID and durable audit record.
- Diagnostic SQL must be read-only, policy validated, time limited and result bounded.
  Never execute generated SQL directly or treat regex filtering as adequate validation.
- LLM conclusions are hypotheses. Evidence references must resolve and be validated
  before any supported finding can appear in a report.
- Preserve valid and transaction time plus provenance for knowledge revisions.
- Design retries for idempotency; persist checkpoints and honor cancellation.
- Never commit secrets, raw sensitive evidence or environment files.
- Run `make format` and `make check`; validate Compose when it changes. Report actual
  service startup separately from syntax validation. Never claim unrun checks passed.
- Add meaningful unit, integration, contract and fault tests at the relevant layer.
- Update PROJECT_STATE.md and DECISIONS.md with delivered scope and limitations.

Simulator-specific rules:
- Read docs/simulator.md before changing fixture generation or fault behavior.
- Never expose evaluator files, scenario definitions, mutation CLI or oracle labels to
  investigation tools. Use observer credentials and omit evaluator mounts in future runtimes.
- Use a dedicated fixture database; never reset control-plane or enterprise source data.
- Preserve deterministic generation/reset/replay and public event versioning/idempotency.
- Run `make sim-integration` for simulator/storage/streaming changes with live services;
  default integration skips are not evidence that PostgreSQL or Kafka was tested.
- Record generator algorithm/configuration changes; do not silently reuse old benchmark
  truth after changing fixture semantics. One private directory/operator writer per DB.

Database/MCP rules:
- Read docs/database-mcp.md before changing SQL validation, tools, transport or gateway policy.
- Preserve fail-closed AST validation and independent SELECT-only database enforcement.
- Never infer application authorization from MCP hints, schemas or client-supplied permissions.
- Do not pass simulator owner credentials, .env or evaluator mounts to MCP/agent runtimes.
- Run make mcp-integration for database/MCP changes; include real PostgreSQL timeout and
  SDK stdio tests. Driver doubles do not establish live Teradata compatibility.
- Keep unsupported/live-unverified capabilities explicit. No fake Teradata success.
- Treat audit ledgers as sensitive durable budget/replay state; do not discard casually.

Phase 3 rules:
- Read docs/phase-3.md before knowledge-source/NLP/retrieval work. Milestones 3.1 and source-extraction increment 3.2a are
  implemented; read docs/phase-3.2a.md and stop there unless a later user task continues.
- Knowledge and simulator migrations/databases are independent; never use simulator
  observer credentials or evaluator files as an ingestion corpus.
- Preserve immutable bytes/version provenance and exact character-offset semantics.
  Normalization/PDF must add explicit offset maps rather than silently changing offsets.
- Preserve leased-job generation/token fencing, bounded retries and atomic publication.
- `delete` is a retained-provenance tombstone, not hard purge; `reprocess` rebuilds chunks,
  not embeddings. Do not claim deferred NLP, graph or vector operations are implemented.
- Run `make documents-integration` and the all-service runner documented in docs/phase-3.md.
  Benchmarks must distinguish measured ingestion performance from retrieval/NLP quality.

- PDF runtime requires an explicitly built local parser image. Keep the pinned revision,
  no-network/no-mount/resource limits and cleanup verification. No native permissive fallback.
- Preserve mapped normalization and append-only artifact/config history. Source-fidelity
  metrics do not establish entity/relationship extraction or resolution accuracy.
