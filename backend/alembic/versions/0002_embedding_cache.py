"""Embedding cache.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE embedding_cache (
            model       TEXT NOT NULL,
            text_hash   TEXT NOT NULL,
            embedding   vector NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (model, text_hash)
        )""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS embedding_cache")
