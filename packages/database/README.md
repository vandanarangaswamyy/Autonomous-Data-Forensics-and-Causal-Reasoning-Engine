# Database infrastructure

SQLAlchemy simulator tables, Alembic migrations and the local fixture connection factory
remain in this package. `analytics/` adds SQLGlot policy validation and PostgreSQL and
Teradata implementations of the storage-neutral AnalyticsBackend protocol.

PostgreSQL diagnostics are live tested with the observer role. Teradata uses the official
optional driver, requires operator role attestation, reports all live capabilities
unverified and explicitly rejects unsupported dependency lookup. See docs/database-mcp.md.
