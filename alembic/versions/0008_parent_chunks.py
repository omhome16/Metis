"""P3.1: parent_chunks table + chunks.parent_id (parent-child chunking).

Parents hold the ~2000-char structure-preserving blocks used as context;
children (~400 chars) are embedded and searched, pointing back at their parent.
"""

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "parent_chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("doc_id", sa.String(36), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("start_chunk_idx", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["doc_id"], ["documents.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_parent_chunks_doc_id", "parent_chunks", ["doc_id"])
    op.add_column(
        "chunks",
        sa.Column(
            "parent_id",
            sa.String(36),
            nullable=True,
        ),
    )
    op.create_index("ix_chunks_parent_id", "chunks", ["parent_id"])
    op.create_foreign_key(
        "fk_chunks_parent_id", "chunks", "parent_chunks", ["parent_id"], ["id"], ondelete="CASCADE"
    )


def downgrade() -> None:
    op.drop_constraint("fk_chunks_parent_id", "chunks", type_="foreignkey")
    op.drop_index("ix_chunks_parent_id", table_name="chunks")
    op.drop_column("chunks", "parent_id")
    op.drop_index("ix_parent_chunks_doc_id", table_name="parent_chunks")
    op.drop_table("parent_chunks")
