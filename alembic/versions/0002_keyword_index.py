"""keyword search: GIN tsvector index on chunks.text (M5 hybrid retrieval)

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-08

"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunks_text_tsv "
        "ON chunks USING GIN (to_tsvector('english', text))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_text_tsv")
