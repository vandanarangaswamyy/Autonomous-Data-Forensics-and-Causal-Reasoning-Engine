"""Independent knowledge-ingestion metadata. Never attach to simulator migrations."""

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()
blobs = Table(
    "source_blobs",
    metadata,
    Column("sha256", String(64), primary_key=True),
    Column("content", LargeBinary, nullable=False),
)
documents = Table(
    "documents",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("collection", String(64), nullable=False),
    Column("source_uri", String(1000), nullable=False),
    Column("version", Integer, nullable=False),
    Column("generation", Integer, nullable=False),
    Column("deleted", Boolean, nullable=False),
    UniqueConstraint("collection", "source_uri"),
    CheckConstraint("version >= 0 AND generation >= 0"),
)
versions = Table(
    "document_versions",
    metadata,
    Column("document_id", String(36), ForeignKey("documents.id"), primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("content_sha256", String(64), ForeignKey("source_blobs.sha256"), nullable=False),
    Column("revision_sha256", String(64), nullable=False),
    Column("descriptor", JSON, nullable=False),
    Column("chunking", JSON, nullable=False),
    Column("ingested_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("version >= 1"),
)
jobs = Table(
    "ingestion_jobs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("document_id", String(36), nullable=False),
    Column("version", Integer, nullable=False),
    Column("generation", Integer, nullable=False),
    Column("status", String(20), nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("token", String(36)),
    Column("lease_until", DateTime(timezone=True)),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("error_code", String(50)),
    ForeignKeyConstraint(
        ["document_id", "version"], ["document_versions.document_id", "document_versions.version"]
    ),
    UniqueConstraint("document_id", "generation"),
    CheckConstraint("status IN ('queued','running','retry','succeeded','failed','superseded')"),
    CheckConstraint("attempts >= 0 AND attempts <= 3"),
)
Index("ix_ingestion_jobs_claim", jobs.c.status, jobs.c.available_at, jobs.c.lease_until)
chunks = Table(
    "document_chunks",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("document_id", String(36), nullable=False),
    Column("version", Integer, nullable=False),
    Column("start", Integer, nullable=False),
    Column("end", Integer, nullable=False),
    Column("text", Text, nullable=False),
    Column("section_path", JSON, nullable=False),
    Column("content_sha256", String(64), nullable=False),
    Column("processor_sha256", String(64), nullable=False),
    ForeignKeyConstraint(
        ["document_id", "version"], ["document_versions.document_id", "document_versions.version"]
    ),
    CheckConstraint('"start" >= 0 AND "end" > "start"'),
)
Index("ix_document_chunks_version", chunks.c.document_id, chunks.c.version)
