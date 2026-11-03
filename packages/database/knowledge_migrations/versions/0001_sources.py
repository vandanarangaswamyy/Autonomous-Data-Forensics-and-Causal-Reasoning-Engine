"""Initial independent knowledge ingestion storage."""

import sqlalchemy as sa
from alembic import op

revision = "knowledge_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("collection", sa.String(length=64), nullable=False),
        sa.Column("source_uri", sa.String(length=1000), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.CheckConstraint("version >= 0 AND generation >= 0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("collection", "source_uri"),
    )
    op.create_table(
        "source_blobs",
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.PrimaryKeyConstraint("sha256"),
    )
    op.create_table(
        "document_versions",
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("revision_sha256", sa.String(length=64), nullable=False),
        sa.Column("descriptor", sa.JSON(), nullable=False),
        sa.Column("chunking", sa.JSON(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1"),
        sa.ForeignKeyConstraint(
            ["content_sha256"],
            ["source_blobs.sha256"],
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
        ),
        sa.PrimaryKeyConstraint("document_id", "version"),
    )
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("start", sa.Integer(), nullable=False),
        sa.Column("end", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("section_path", sa.JSON(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("processor_sha256", sa.String(length=64), nullable=False),
        sa.CheckConstraint('"start" >= 0 AND "end" > "start"'),
        sa.ForeignKeyConstraint(
            ["document_id", "version"],
            ["document_versions.document_id", "document_versions.version"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_document_chunks_version", "document_chunks", ["document_id", "version"], unique=False
    )
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(length=36), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued','running','retry','succeeded','failed','superseded')"
        ),
        sa.CheckConstraint("attempts >= 0 AND attempts <= 3"),
        sa.ForeignKeyConstraint(
            ["document_id", "version"],
            ["document_versions.document_id", "document_versions.version"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "generation"),
    )
    op.create_index(
        "ix_ingestion_jobs_claim",
        "ingestion_jobs",
        ["status", "available_at", "lease_until"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ingestion_jobs_claim", table_name="ingestion_jobs")
    op.drop_table("ingestion_jobs")
    op.drop_index("ix_document_chunks_version", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_table("document_versions")
    op.drop_table("source_blobs")
    op.drop_table("documents")
