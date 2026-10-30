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

15. **Storage-neutral async analytics port.** Core defines AnalyticsBackend and shared
    typed contracts; vendor adapters live in database infrastructure. PostgreSQL uses
    async psycopg. Official teradatasql is optional and its blocking driver runs outside
    the event loop. No orchestration code imports vendor implementations.
16. **Conservative SQL subset plus independent database controls.** Parse explicit
    dialects with SQLGlot, resolve physical sources by scope, allow only reviewed AST
    nodes/functions/casts, bind values, qualify identifiers, enforce read-only roles,
    transactions, timeouts and bounded rows/bytes. Unknown syntax fails closed.
17. **Honest Teradata capabilities.** Driver-backed query/ColumnsV/profile/EXPLAIN paths
    exist but all are live unverified. SELECT-only account attestation is required;
    effective-role introspection is not implemented. Unsupported dependency catalog
    lookup fails explicitly; unavailable metadata fields are null.
18. **MCP is transport, not authorization.** Use official SDK low-level servers with
    typed schemas and structured replies. Authenticate a provisioned application token
    from request metadata and authorize through trusted permissions inside every server.
    Hints and caller arguments cannot grant permissions. Default permissions are empty.
19. **Durable local gateway state.** Lock-protected append-only audit records also back
    per-principal/investigation reservations and request replay protection. This keeps
    control state separate from source data without adding a service or speculative
    database schema. Size/rotation, distributed quotas and ownership admission are
    explicit future operational boundaries.
20. **Local transport now, remote extension explicitly.** Discover through a typed
    transport interface and trusted name-to-permission mapping. Validate schemas and
    correlation; reject remote schema references. Stdio is working/tested; HTTP/TLS/OAuth
    deployment must not be inferred from the extension interface.
21. **Public technical keyword corpus.** Implement a small real keyword index over
    three packaged documents to fulfill retrieval-tool contracts without exposing
    evaluator files or pretending vector/graph retrieval has shipped.

22. **Deadlines include synchronous preparation.** Use elapsed monotonic time, check
    admission after file reservation and before success, and classify output defects
    separately. Asyncio cannot preempt synchronous filesystem calls; cleanup may outlast
    the response deadline. Never report expired work as successful.
23. **Concurrency leases follow live owners.** A deadline is not proof of native query
    completion. Retain a reservation until finish or confirmed owner-process death;
    preserve worst-case budget charges after crashes. PID reuse/legacy unknown owners
    fail closed and require operator recovery. File-lock acquisition is bounded.
24. **Untrusted error content is not an audit field.** Forward known remote codes only,
    use fixed safe messages and hash unknown requested names. No peer-controlled error
    text or arbitrary tool-name body belongs in local execution traces.
25. **Role and catalog correctness require nontrivial fixtures.** Validate column-level
    write grants independently, preserve composite key ordinality, exclude INCLUDE columns,
    cover wide tables and reject truncated catalog results rather than fake completeness.
26. **Native driver cleanup survives repeated cancellation.** Shield/drain Teradata work
    before releasing its slot. Force verified TLS options, but retain live-unverified
    capability flags. Driver doubles prove coordination/options only; no Vantage access
    or effective-grant validation is claimed. Shared encoding belongs in a neutral module.
27. **Private bootstrap writes are atomic.** Replace .env from a mode-0600 temporary
    file, reject symlinks and keep transient names ignored. Do not write credentials into
    an existing public file and rely on a later chmod.
28. **Review claims must be reproducible.** Add test-all with live settings and a clean
    source/bootstrap harness using a disposable database container. Separate real-service
    evidence from explicitly scoped test doubles and keep remaining admission/audit/
    remote/Teradata gaps visible before starting autonomous agent work.
