"""vector foundations: halfvec + HNSW indexes, doc metadata, corpus_versions

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-13

"""

from alembic import op

from app.core.config import get_settings

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_SETTINGS = get_settings()


def upgrade() -> None:
    # Fixed-dimension halfvec columns — required by HNSW indexes. Dimensions are
    # config-driven (METIS_EMBED_DIM / METIS_CLIP_DIM) at migration time.
    embed_dims = _SETTINGS.embed_dim
    clip_dims = _SETTINGS.clip_dim
    op.execute(
        f"ALTER TABLE chunks ALTER COLUMN embedding TYPE halfvec({embed_dims}) "
        "USING embedding::halfvec"
    )
    op.execute(
        f"ALTER TABLE images ALTER COLUMN embedding TYPE halfvec({clip_dims}) "
        "USING embedding::halfvec"
    )
    # HNSW cosine indexes (m=16 / ef_construction=64; recall tuned per-query via hnsw.ef_search)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw ON chunks "
        "USING hnsw (embedding halfvec_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_images_embedding_hnsw ON images "
        "USING hnsw (embedding halfvec_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )
    # document metadata
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{}'")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_date TIMESTAMPTZ")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS author VARCHAR(256)")
    # corpus versioning (bumped on ingest success / vault delete)
    op.execute(
        "CREATE TABLE IF NOT EXISTS corpus_versions ("
        "corpus VARCHAR(128) PRIMARY KEY, "
        "version INT NOT NULL DEFAULT 0, "
        "updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS corpus_versions")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS author")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS doc_date")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS tags")
    op.execute("DROP INDEX IF EXISTS ix_images_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
    op.execute("ALTER TABLE images ALTER COLUMN embedding TYPE vector USING embedding::vector")
    op.execute("ALTER TABLE chunks ALTER COLUMN embedding TYPE vector USING embedding::vector")
