"""initial schema (blueprint §6)

Revision ID: 0001
Revises:
Create Date: 2026-08-08

"""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("corpus", sa.String(128), nullable=False),
        sa.Column("source_url", sa.String(1024), nullable=True),
        sa.Column("format", sa.String(16), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("file_path", sa.String(1024), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_documents_corpus", "documents", ["corpus"])
    op.create_index("ix_documents_content_hash", "documents", ["content_hash"], unique=True)

    op.create_table(
        "chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("doc_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(), nullable=True),
    )
    op.create_index("ix_chunks_doc_id", "chunks", ["doc_id"])

    op.create_table(
        "images",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("doc_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_path", sa.String(1024), nullable=False),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("tags", sa.ARRAY(sa.String(128)), nullable=False, server_default="{}"),
        sa.Column("embedding", Vector(), nullable=True),
    )
    op.create_index("ix_images_doc_id", "images", ["doc_id"])

    op.create_table(
        "golden_questions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("corpus", sa.String(128), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("ground_truth", sa.Text(), nullable=False),
        sa.Column("source_hint", sa.Text(), nullable=True),
    )
    op.create_index("ix_golden_questions_corpus", "golden_questions", ["corpus"])

    op.create_table(
        "eval_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("config", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )

    op.create_table(
        "ingest_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("corpus", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("per_file_errors", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ingest_jobs_corpus", "ingest_jobs", ["corpus"])


def downgrade() -> None:
    op.drop_table("ingest_jobs")
    op.drop_table("eval_runs")
    op.drop_table("golden_questions")
    op.drop_table("images")
    op.drop_table("chunks")
    op.drop_table("documents")
    op.execute("DROP EXTENSION IF EXISTS vector")
