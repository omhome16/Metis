"""documents.ingest_job_id — robust worker job matching (review follow-up)

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-08

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("ingest_job_id", sa.String(36), nullable=True))
    op.create_index("ix_documents_ingest_job_id", "documents", ["ingest_job_id"])


def downgrade() -> None:
    op.drop_index("ix_documents_ingest_job_id", table_name="documents")
    op.drop_column("documents", "ingest_job_id")
