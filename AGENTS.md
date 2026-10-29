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
