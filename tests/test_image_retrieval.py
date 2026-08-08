import uuid

from sqlalchemy import delete, select

from app.db.models import Document, ImageRecord
from app.db.session import async_session_factory
from app.rag.embeddings import get_image_embedder
from app.rag.retrieval import image_search, store_image

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c626001000000ffff03000006000557bfabd40000000049454e44ae426082"
)


async def _cleanup(corpus: str) -> None:
    async with async_session_factory() as session:
        await session.execute(delete(ImageRecord).where(ImageRecord.doc_id.in_(select(Document.id).where(Document.corpus == corpus))))
        await session.execute(delete(Document).where(Document.corpus == corpus))
        await session.commit()


async def test_store_and_search_images(require_db):
    corpus = f"test-img-{uuid.uuid4().hex[:8]}"
    embedder = get_image_embedder()
    async with async_session_factory() as session:
        doc = Document(
            id=str(uuid.uuid4()),
            title="Sunset painting",
            corpus=corpus,
            format="image",
            content_hash=uuid.uuid4().hex,
        )
        session.add(doc)
        await session.commit()
        emb = await embedder.embed_image(_PNG, "image/png")
        await store_image(session, doc.id, "uploads/x/sunset.png", "A red sunset over the sea.", ["sunset"], emb)

    async with async_session_factory() as session:
        qvec = await embedder.embed_image(_PNG, "image/png")
        hits = await image_search(session, qvec, corpus=corpus, top_k=5)
        assert len(hits) == 1
        assert hits[0].doc_title == "Sunset painting"
        assert hits[0].image.caption == "A red sunset over the sea."
        assert hits[0].image.tags == ["sunset"]
        assert 0.0 <= hits[0].score <= 1.0

        # corpus isolation
        assert await image_search(session, qvec, corpus="nope", top_k=5) == []

    await _cleanup(corpus)
