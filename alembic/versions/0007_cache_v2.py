"""cache v2: grounded, indexed, versioned semantic cache

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-13

"""

from alembic import op
from app.core.config import get_settings

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_SETTINGS = get_settings()


def upgrade() -> None:
    # Grounded semantic cache: answers + question embeddings, versioned + TTL'd.
    op.execute(
        "CREATE TABLE IF NOT EXISTS cache_entries ("
        "id VARCHAR(36) PRIMARY KEY, "
        "corpus VARCHAR(128) NOT NULL, "
        "question TEXT NOT NULL, "
        f"question_embedding HALFVEC({_SETTINGS.embed_dim}), "
        "answer TEXT NOT NULL DEFAULT '', "
        "sources JSONB NOT NULL DEFAULT '{}', "
        "citations JSONB NOT NULL DEFAULT '{}', "
        "done JSONB NOT NULL DEFAULT '{}', "
        "model VARCHAR(128) NOT NULL DEFAULT '', "
        "embed_model VARCHAR(256) NOT NULL DEFAULT '', "
        "corpus_version INT NOT NULL DEFAULT 0, "
        "hit_count INT NOT NULL DEFAULT 0, "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
        "expires_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_cache_entries_corpus ON cache_entries (corpus)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cache_entries_embedding_hnsw ON cache_entries "
        "USING hnsw (question_embedding halfvec_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )
    # Coarse hit/miss counters for /cache/stats (Redis is queue-only since P2).
    op.execute(
        "CREATE TABLE IF NOT EXISTS cache_metrics ("
        "key VARCHAR(32) PRIMARY KEY, "
        "value INT NOT NULL DEFAULT 0)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS cache_metrics")
    op.execute("DROP INDEX IF EXISTS ix_cache_entries_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_cache_entries_corpus")
    op.execute("DROP TABLE IF EXISTS cache_entries")
