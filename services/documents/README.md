# Document ingestion

Phase 3 milestone 3.1 implements durable PostgreSQL UTF-8 source ingestion, versioning,
fenced asynchronous jobs and exact-offset recursive/Markdown chunks. Operator CLI:
`atlas-documents`. See [runbook and milestone limits](../../docs/phase-3.md).

PDF, spaCy/Transformers, entities, embeddings and S3 remain deferred. This module does
not supply hybrid retrieval, an investigation agent or temporal memory.
