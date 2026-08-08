import uuid

from sqlalchemy import delete, select

from app.db.models import Chunk, Document
from app.db.session import async_session_factory
from app.rag.embeddings import get_embedder
from app.rag.retrieval import store_chunks, vector_search


async def _cleanup(corpus: str) -> None:
    async with async_session_factory() as session:
        await session.execute(delete(Chunk).where(Chunk.doc_id.in_(select(Document.id).where(Document.corpus == corpus))))
        await session.execute(delete(Document).where(Document.corpus == corpus))
        await session.commit()


async def test_store_and_search(require_db):
    corpus = f"test-{uuid.uuid4().hex[:8]}"
    embedder = get_embedder()
    async with async_session_factory() as session:
        doc = Document(
            id=str(uuid.uuid4()),
            title="Metis docs",
            corpus=corpus,
            format="txt",
            content_hash=uuid.uuid4().hex,
            raw_text="the quick brown fox",
        )
        session.add(doc)
        await session.commit()
        embeddings = await embedder.embed_texts(["the quick brown fox jumps", "completely unrelated sentence"])
        await store_chunks(session, doc.id, ["the quick brown fox jumps", "completely unrelated sentence"], embeddings)

    async with async_session_factory() as session:
        qvec = await embedder.embed_query("the quick brown fox")
        hits = await vector_search(session, qvec, corpus=corpus, top_k=5)
        assert len(hits) == 2
        assert all(0.0 <= h.score <= 1.0 for h in hits)
        assert hits[0].score >= hits[1].score  # ordered by similarity
        assert hits[0].doc_title == "Metis docs"

        # corpus filter
        hits_other = await vector_search(session, qvec, corpus="nonexistent-corpus", top_k=5)
        assert hits_other == []

    await _cleanup(corpus)
