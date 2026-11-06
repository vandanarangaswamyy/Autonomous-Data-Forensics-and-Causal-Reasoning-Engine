"""Add immutable source-extraction artifacts without changing source/version/chunk IDs."""

import sqlalchemy as sa
from alembic import op

revision = "knowledge_0002"
down_revision = "knowledge_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ingestion_jobs", sa.Column("extraction_config", sa.JSON(), nullable=True))
    op.create_table(
        "source_extractions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "source_sha256", sa.String(64), sa.ForeignKey("source_blobs.sha256"), nullable=False
        ),
        sa.Column("config_sha256", sa.String(64), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("artifact", sa.JSON(), nullable=False),
        sa.Column("chunk_manifest", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id", "version"],
            ["document_versions.document_id", "document_versions.version"],
        ),
        sa.UniqueConstraint("document_id", "version", "config_sha256"),
        sa.UniqueConstraint("document_id", "version", "id", name="uq_extraction_reference"),
    )
    op.create_index(
        "ix_source_extractions_version", "source_extractions", ["document_id", "version"]
    )
    op.add_column("document_chunks", sa.Column("extraction_id", sa.String(36), nullable=True))
    op.create_foreign_key(
        "fk_chunk_extraction_version",
        "document_chunks",
        "source_extractions",
        ["document_id", "version", "extraction_id"],
        ["document_id", "version", "id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_chunk_extraction_version", "document_chunks", type_="foreignkey")
    op.drop_column("document_chunks", "extraction_id")
    op.drop_index("ix_source_extractions_version", table_name="source_extractions")
    op.drop_table("source_extractions")
    op.drop_column("ingestion_jobs", "extraction_config")
