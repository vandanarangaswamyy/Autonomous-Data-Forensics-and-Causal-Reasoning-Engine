# Architecture decisions

1. **Modular monorepo first.** One installable Python distribution with explicit source
   roots; avoid independent package releases and service sprawl at foundation stage.
2. **Separate durable execution and knowledge.** PostgreSQL will own control state;
   Neo4j will own long-term temporal assertions. Redis is transport/cache.
3. **Defer unused dependencies.** Install only API/contracts and quality tooling now.
   Add SQLAlchemy/Alembic, Celery, LangGraph, MCP, NLP and retrieval dependencies with
   working implementations and appropriate tests. No fake adapters or empty migrations.
4. **Lifecycle readiness.** Check real application readiness now; add dependency checks
   when the API actually depends on infrastructure.
5. **Evidence references are necessary, insufficient.** Contracts enforce shape and
   basic policy invariants. Future services verify evidence integrity, ownership,
   diagnostic outcomes and graph temporal semantics.
6. **Documents have an explicit boundary.** Add services/documents for S3 ingestion/NLP.
7. **Local infrastructure only.** Named volumes and loopback ports; reviewed tags now,
   digest pinning and production hardening before release.
8. **Dedicated repository.** Workspace inspection found unrelated repositories and no
   ATLAS code. Initialize atlas/ independently; leave existing projects untouched.

9. **Dedicated simulator storage.** Thirty portable SQLAlchemy Core tables live in
   atlas_simulator, separate from future control-plane persistence. Alembic migrations
   are explicit and tested on SQLite and PostgreSQL. Core generation has no DB imports.
10. **Executable deterministic fixtures.** Fixed calendar, seeded local PRNG, decimal
    amounts and UUID-derived fault identities support reproducible generation/reset.
    SQL transformations compute actual reports; faults mutate real inputs or joins.
11. **Evaluator-only oracle.** Ground-truth configuration/history resides in mode-0600
    files inside a mode-0700 private directory. No oracle DB table or Kafka label.
    Future agents get a SELECT-only database login and no evaluator filesystem mount.
    This does not isolate code running as the same evaluator OS user.
12. **One active fault per fixture.** Keep initial benchmarks single-cause. Reset
    deterministically regenerates baseline; replay resets and reapplies a recorded
    configuration. A private lock serializes local evaluator operations. Pending intent
    records make interrupted mutations recoverable through reset; no cross-store atomicity
    is claimed. Use one private directory/operator writer per fixture database.
13. **At-least-once events, idempotent effects.** Commit public envelopes with fixture
    changes, acknowledge Kafka delivery before marking published, retain stable event IDs
    during replay, and commit consumer receipts/effects before Kafka offsets. Producer
    idempotence does not imply end-to-end exactly-once execution.
14. **Bounded benchmark scope.** Three profiles and scenarios establish observable
    symptoms and known causes. No background scheduler, SQL diagnostic gateway, agents,
    RAG, GraphRAG, MCP business tools, or agent scoring is part of this milestone.
