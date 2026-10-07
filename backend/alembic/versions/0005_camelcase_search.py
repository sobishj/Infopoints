"""Keyword search splits camelCase words ("accessToken" also matches "access token").

Revision ID: 0005
Revises: 0004
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
        CREATE OR REPLACE FUNCTION ip_search_text(t text) RETURNS text
        LANGUAGE sql IMMUTABLE PARALLEL SAFE AS
        $fn$ SELECT regexp_replace(coalesce(t, ''), '([a-z0-9])([A-Z])', '\1 \2', 'g') $fn$""")
    op.execute("DROP INDEX IF EXISTS chunks_tsv")
    op.execute("ALTER TABLE chunks DROP COLUMN tsv")
    op.execute("""ALTER TABLE chunks ADD COLUMN tsv tsvector GENERATED ALWAYS AS (
                      setweight(to_tsvector('english', ip_search_text(heading)), 'A') ||
                      to_tsvector('english', ip_search_text(text))) STORED""")
    op.execute("CREATE INDEX chunks_tsv ON chunks USING gin (tsv)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS chunks_tsv")
    op.execute("ALTER TABLE chunks DROP COLUMN tsv")
    op.execute("""ALTER TABLE chunks ADD COLUMN tsv tsvector GENERATED ALWAYS AS (
                      setweight(to_tsvector('english', coalesce(heading, '')), 'A') ||
                      to_tsvector('english', text)) STORED""")
    op.execute("CREATE INDEX chunks_tsv ON chunks USING gin (tsv)")
    op.execute("DROP FUNCTION IF EXISTS ip_search_text(text)")
