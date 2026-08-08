"""vaults — named document libraries (frontend vaults layer)

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-08

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vaults",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("color", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_vaults_name", "vaults", ["name"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_vaults_name", table_name="vaults")
    op.drop_table("vaults")
