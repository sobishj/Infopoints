"""Embedding of each chunk's section path, for matching questions to sections.

Revision ID: 0004
Revises: 0003
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE chunks ADD COLUMN heading_embedding vector")


def downgrade() -> None:
    op.execute("ALTER TABLE chunks DROP COLUMN heading_embedding")
