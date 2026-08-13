"""DB-gated smoke tests: 0006 migration shape (halfvec + HNSW, metadata, corpus_versions)."""

from sqlalchemy import text


async def test_halfvec_columns_and_indexes(client, require_db):
    from app.db.session import engine

    async with engine.connect() as conn:
        hnsw = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM pg_indexes WHERE indexname IN "
                    "('ix_chunks_embedding_hnsw','ix_images_embedding_hnsw')"
                )
            )
        ).scalar()
        assert hnsw == 2
        row = (
            await conn.execute(
                text("SELECT version FROM corpus_versions WHERE corpus='smoke'")
            )
        ).scalar_one_or_none()
        assert row is None
        cols = set(
            (
                await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='documents'"
                    )
                )
            ).scalars()
        )
        assert {"tags", "doc_date", "author"} <= cols
