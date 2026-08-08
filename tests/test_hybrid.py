import uuid

from sqlalchemy import delete, select

from app.db.models import Chunk, Document
from app.db.session import async_session_factory
from app.rag.embeddings import get_embedder
from app.rag.retrieval import fuse_hybrid, keyword_search, store_chunks, vector_search


async def _cleanup(corpus: str) -> None:
    async with async_session_factory() as session:
        await session.execute(delete(Chunk).where(Chunk.doc_id.in_(select(Document.id).where(Document.corpus == corpus))))
        await session.execute(delete(Document).where(Document.corpus == corpus))
        await session.commit()


async def test_keyword_search_finds_term(require_db):
    corpus = f"test-kw-{uuid.uuid4().hex[:8]}"
    async with async_session_factory() as session:
        doc = Document(
            id=str(uuid.uuid4()), title="PyTorch guide", corpus=corpus, format="txt",
            content_hash=uuid.uuid4().hex, raw_text="tensors are cool",
        )
        session.add(doc)
        await session.commit()
        embedder = get_embedder()
        embs = await embedder.embed_texts(["PyTorch tensors are the core data structure."])
        await store_chunks(session, doc.id, ["PyTorch tensors are the core data structure."], embs)

    async with async_session_factory() as session:
        hits = await keyword_search(session, "tensors", corpus=corpus, top_k=5)
        assert len(hits) == 1
        assert "tensor" in hits[0].chunk.text.lower()
        assert hits[0].score > 0

        # no keyword match → empty
        assert await keyword_search(session, "zebra", corpus=corpus, top_k=5) == []
        # corpus isolation
        assert await keyword_search(session, "tensors", corpus="nope", top_k=5) == []

    await _cleanup(corpus)


async def test_rrf_fusion(require_db):
    """A chunk found by both retrievers outranks chunks found by only one."""
    corpus = f"test-rrf-{uuid.uuid4().hex[:8]}"
    embedder = get_embedder()
    async with async_session_factory() as session:
        doc = Document(
            id=str(uuid.uuid4()), title="Both", corpus=corpus, format="txt",
            content_hash=uuid.uuid4().hex, raw_text="alpha beta gamma",
        )
        session.add(doc)
        await session.commit()
        embs = await embedder.embed_texts(["alpha beta gamma"])
        await store_chunks(session, doc.id, ["alpha beta gamma"], embs)

    async with async_session_factory() as session:
        qvec = await embedder.embed_query("alpha beta")
        vector_hits = await vector_search(session, qvec, corpus=corpus, top_k=5)
        keyword_hits = await keyword_search(session, "alpha", corpus=corpus, top_k=5)
        fused = fuse_hybrid(vector_hits, keyword_hits, top_k=5)
        assert fused and fused[0].chunk.id == vector_hits[0].chunk.id
        assert fused[0].score > 0

    await _cleanup(corpus)
