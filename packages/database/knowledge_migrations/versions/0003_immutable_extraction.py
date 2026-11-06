"""Protect successful extraction history against accidental mutation."""

from alembic import op

revision = "knowledge_0003"
down_revision = "knowledge_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION atlas_extraction_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'source extraction history is append only'; END $$
    """)
    op.execute("""
        CREATE TRIGGER extraction_history_immutable BEFORE UPDATE OR DELETE ON source_extractions
        FOR EACH ROW EXECUTE FUNCTION atlas_extraction_append_only()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER extraction_history_immutable ON source_extractions")
    op.execute("DROP FUNCTION atlas_extraction_append_only()")
