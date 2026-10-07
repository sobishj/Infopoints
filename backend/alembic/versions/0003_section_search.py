"""Full-text search also covers the section heading of each chunk.

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS chunks_tsv")
    op.execute("ALTER TABLE chunks DROP COLUMN tsv")
    op.execute("""ALTER TABLE chunks ADD COLUMN tsv tsvector GENERATED ALWAYS AS (
                      setweight(to_tsvector('english', coalesce(heading, '')), 'A') ||
                      to_tsvector('english', text)) STORED""")
    op.execute("CREATE INDEX chunks_tsv ON chunks USING gin (tsv)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS chunks_tsv")
    op.execute("ALTER TABLE chunks DROP COLUMN tsv")
    op.execute("ALTER TABLE chunks ADD COLUMN tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED")
    op.execute("CREATE INDEX chunks_tsv ON chunks USING gin (tsv)")
