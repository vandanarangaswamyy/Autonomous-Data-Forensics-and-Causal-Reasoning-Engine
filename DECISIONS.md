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
