"""P3.1 parent-child (small-to-big): storage, expansion, and ingest path."""

import uuid
from unittest.mock import patch

from sqlalchemy import delete, select

from app.db.models import Chunk, Document, ParentChunk
from app.db.session import async_session_factory
from app.rag.embeddings import get_embedder
from app.rag.retrieval import ChunkHit, expand_to_parents, store_chunks, store_parents


async def _cleanup(corpus: str) -> None:
    async with async_session_factory() as session:
        await session.execute(
            delete(Chunk).where(
                Chunk.doc_id.in_(select(Document.id).where(Document.corpus == corpus))
            )
        )
        await session.execute(
            delete(ParentChunk).where(
                ParentChunk.doc_id.in_(select(Document.id).where(Document.corpus == corpus))
            )
        )
        await session.execute(delete(Document).where(Document.corpus == corpus))
        await session.commit()


async def _seed(corpus: str, parent_text: str, child_texts: list[str]) -> tuple[str, str]:
    """One doc, one parent, N children (all under the parent). Returns (doc_id, parent_id)."""
    embedder = get_embedder()
    doc_id = str(uuid.uuid4())
    async with async_session_factory() as session:
        session.add(
            Document(
                id=doc_id,
                title="P3 parent doc",
                corpus=corpus,
                format="txt",
                content_hash=uuid.uuid4().hex,
                raw_text=parent_text,
            )
        )
        await session.commit()
        (parent,) = await store_parents(session, doc_id, [parent_text], start_indices=[0])
        embeddings = await embedder.embed_texts(child_texts)
        await store_chunks(
            session, doc_id, child_texts, embeddings, parent_ids=[parent.id] * len(child_texts)
        )
        return doc_id, parent.id


async def test_expand_to_parents_dedupes_keeps_best_score(require_db):
    corpus = f"test-pc-{uuid.uuid4().hex[:8]}"
    parent_text = "The complete parent block covering the whole section."
    await _seed(corpus, parent_text, ["child one about alpha", "child two about beta"])

    async with async_session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(Chunk).where(
                        Chunk.doc_id.in_(select(Document.id).where(Document.corpus == corpus))
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2
        pid = rows[0].parent_id
        assert pid

        hits = [
            ChunkHit(chunk=rows[0], score=0.3, doc_title="P3 parent doc"),
            ChunkHit(chunk=rows[1], score=0.9, doc_title="P3 parent doc"),
        ]
        expanded = await expand_to_parents(session, hits)
        assert len(expanded) == 1  # both children share one parent → one block
        assert expanded[0].chunk.text == parent_text  # parent replaces child snippet
        assert expanded[0].score == 0.9  # best child score kept
        assert expanded[0].chunk.id == rows[1].id  # citation accounting keeps the child id
        assert expanded[0].chunk.parent_id == pid

    await _cleanup(corpus)


async def test_expand_to_parents_flat_noop(require_db):
    corpus = f"test-pc-flat-{uuid.uuid4().hex[:8]}"
    embedder = get_embedder()
    async with async_session_factory() as session:
        doc = Document(
            id=str(uuid.uuid4()),
            title="flat",
            corpus=corpus,
            format="txt",
            content_hash=uuid.uuid4().hex,
        )
        session.add(doc)
        await session.commit()
        (chunk,) = await store_chunks(
            session, doc.id, ["flat chunk text"], await embedder.embed_texts(["flat chunk text"])
        )
        hits = [ChunkHit(chunk=chunk, score=0.5, doc_title="flat")]
        assert await expand_to_parents(session, hits) == hits

    await _cleanup(corpus)


async def test_build_chunks_parent_child_mode(require_db):
    """Ingest path: parents stored un-embedded; children embedded with parent_id."""
    from app.workers.ingest import _build_chunks

    corpus = f"test-pc-ingest-{uuid.uuid4().hex[:8]}"
    embedder = get_embedder()
    text = "\n\n".join(f"Section {i} paragraph. " + "sentence. " * 40 for i in range(8))
    async with async_session_factory() as session:
        doc = Document(
            id=str(uuid.uuid4()),
            title="pc ingest",
            corpus=corpus,
            format="txt",
            content_hash=uuid.uuid4().hex,
        )
        session.add(doc)
        await session.commit()
        rows = await _build_chunks(session, embedder, doc, text)

    assert len(rows) > 1  # several children
    assert all(r.parent_id for r in rows)  # every child points at a parent
    async with async_session_factory() as session:
        parents = (
            (await session.execute(select(ParentChunk).where(ParentChunk.doc_id == doc.id)))
            .scalars()
            .all()
        )
        assert len(parents) >= 2  # ~2000-char blocks from a long text
        child = (await session.execute(select(Chunk).where(Chunk.id == rows[0].id))).scalar_one()
        assert child.parent_id == parents[0].id
        # children of parent 0 start at index 0
        assert parents[0].start_chunk_idx == 0

    await _cleanup(corpus)


async def test_build_chunks_flat_mode(require_db):
    """Kill switch METIS_PARENT_CHILD=false restores the old flat behavior."""
    from app.workers.ingest import _build_chunks

    corpus = f"test-pc-flat-ingest-{uuid.uuid4().hex[:8]}"
    embedder = get_embedder()
    text = "\n\n".join(f"Section {i} paragraph. " + "sentence. " * 40 for i in range(8))
    async with async_session_factory() as session:
        doc = Document(
            id=str(uuid.uuid4()),
            title="pc ingest flat",
            corpus=corpus,
            format="txt",
            content_hash=uuid.uuid4().hex,
        )
        session.add(doc)
        await session.commit()
        with patch("app.workers.ingest.settings.parent_child", False):
            rows = await _build_chunks(session, embedder, doc, text)

    assert len(rows) > 1
    assert all(r.parent_id is None for r in rows)
    async with async_session_factory() as session:
        parents = (
            (await session.execute(select(ParentChunk).where(ParentChunk.doc_id == doc.id)))
            .scalars()
            .all()
        )
        assert parents == []

    await _cleanup(corpus)
