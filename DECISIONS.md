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
29. **Security closure requires composed boundaries.** Re-verify cancellation through
    adapter, gateway semaphore, shared audit lease and shutdown together; isolated
    adapter and lease tests cannot establish admission safety. Keep slots occupied
    during both native execution and native close, even after request expiry/repeated
    cancellation. Controlled driver doubles prove coordination only, not Vantage behavior.
30. **Verify contents and negative-path privacy.** Setup/demo verification checks all
    nine tool bodies, provenance and correlations; compare live query/profile results
    directly. Test real database query/authentication failures over SDK stdio and inspect
    text/structured replies and audits for canary SQL, credentials and connection details.
    Recognized remote error codes still do not authorize forwarding peer messages.
31. **Use explicit partial closure.** PostgreSQL/local MCP can be verified while the
    broader phase remains partial. Mark missing Teradata mappings separately from
    implemented-but-live-unverified driver paths. No live Vantage claim follows from
    installing a driver, using a double or passing PostgreSQL tests. Local per-investigation
    budgets and single-host audit leases do not supply production tenant admission,
    distributed quotas, token lifecycle or remote transport security. Phase 3 remains deferred.
32. **Stage Phase 3 at a durable ingestion boundary.** Deliver 3.1 before NLP/embeddings,
    retrieval and graphs. A declared document kind is not classification, recursive/Markdown
    ranges are not sentence/SQL-aware NLP, and chunk rebuild is not vector reindexing.
    Do not present deferred capabilities as completed or fabricate benchmark scores.
33. **Separate source storage and migrations.** Use an independent knowledge database,
    non-superuser owner and Alembic history. Store bounded original bytes with versions
    and jobs in one transaction for this local milestone; S3 would require cross-store
    recovery and retention design. Do not give this mutation owner to MCP or agents.
34. **Fence durable workers, rather than assume exactly-once execution.** Serialize each
    document's changes, claim jobs with SKIP LOCKED, cap attempts and lease duration, and
    verify current generation/token/lease before publishing. Cancellation leaves work
    reclaimable. Process exhausted leases without hiding later jobs. No heartbeat or
    scheduler is implied; expensive NLP needs a reviewed continuation of this contract.
35. **Keep original text offsets exact.** Avoid lossy normalization in 3.1. Byte hashes,
    external versions and observation/ingestion times resolve through immutable revisions;
    chunks cover the decoded source without gaps. Validate processor outputs before
    atomic publication. Normalization/PDF require explicit offset mappings in 3.2.
36. **Deletion is a tombstone with chunk removal.** Fence workers and remove derived
    chunks immediately, retaining originals/revision provenance. Privacy erasure, retention
    and garbage collection require an explicit future policy; do not call this a purge.
37. **Measure the implemented operation.** Record a versioned deterministic ingestion
    fixture, exact runtime and actual throughput. Retrieval and extraction metrics remain
    unavailable until models, indexes and human-reviewed relevance/entity labels exist.
    A healthy Neo4j container is not graph integration verification.
38. **Split 3.2 at a verified source artifact.** Deliver PDF extraction, normalization
    mapping and durable publication before semantic NLP. DocumentExtractor is real;
    do not add empty entity/model classes or report NLP scores for absent extractors.
39. **Isolate untrusted PDF work in Linux.** macOS rejected address-space/data limits
    during verification. Use an ephemeral resource-limited container with pinned base
    digest/pypdf, no network/mounts or inherited credentials; runtime never pulls an
    image. This operational boundary has an explicit resource/isolation reason and
    reuses the existing job system. Terminate/reap both attach client and container.
40. **Keep normalized and extracted coordinate frames separate.** Preserve legacy
    source/version/chunk identities. Store compact character mapping runs and page
    boundaries with explicit unavailable PDF coordinates; avoid lossy case/spelling
    normalization. Simple layout text does not establish structured table semantics.
41. **Successful extraction history is append-only.** Bind source/version/config and
    immutable manifest, reject non-deterministic replacement, use a database mutation
    guard, and publish artifact/chunks/job success atomically. Config changes require
    explicit reprocessing. Deletion removes active visibility but retains history.
    Failed attempts remain ordinary job status in 3.2a; do not claim an NLP run registry.
42. **Evaluate authored source truth separately from NLP truth.** Record page text,
    mapping and unsupported-status scores against literal manual labels, including
    inline images that exposed a real detection defect. Mark entity/relationship and
    resolution metrics unavailable until 3.2b has independently labeled predictions.
