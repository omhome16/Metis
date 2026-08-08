"""Retrieval layer. M2: pure pgvector cosine search. Hybrid (+tsvector, RRF) in M5."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chunk, Document, ImageRecord


@dataclass
class ChunkHit:
    chunk: Chunk
    score: float  # cosine similarity (1 − cosine distance)
    doc_title: str


async def store_chunks(
    session: AsyncSession,
    doc_id: str,
    chunks: list[str],
    embeddings: list[list[float]],
) -> list[Chunk]:
    """Persist chunk rows (text + embedding) for one document."""
    rows = [
        Chunk(
            doc_id=doc_id,
            text=text,
            chunk_index=i,
            tokens=len(text.split()),
            embedding=emb,
        )
        for i, (text, emb) in enumerate(zip(chunks, embeddings))
    ]
    session.add_all(rows)
    await session.commit()
    return rows


async def vector_search(
    session: AsyncSession,
    query_embedding: list[float],
    corpus: str | None = None,
    top_k: int = 10,
) -> list[ChunkHit]:
    stmt = (
        select(
            Chunk,
            Chunk.embedding.cosine_distance(query_embedding).label("distance"),
            Document.title,
        )
        .join(Document, Document.id == Chunk.doc_id)
        .order_by("distance")
        .limit(top_k)
    )
    if corpus:
        stmt = stmt.where(Document.corpus == corpus)
    rows = (await session.execute(stmt)).all()
    return [ChunkHit(chunk=chunk, score=round(1 - distance, 4), doc_title=title) for chunk, distance, title in rows]


async def fetch_chunks_by_id(session: AsyncSession, chunk_ids: list[str]) -> list[ChunkHit]:
    """Load chunks from Postgres by id (used for graph-boosted hits)."""
    if not chunk_ids:
        return []
    stmt = (
        select(Chunk, Document.title)
        .join(Document, Document.id == Chunk.doc_id)
        .where(Chunk.id.in_(chunk_ids))
    )
    rows = (await session.execute(stmt)).all()
    return [ChunkHit(chunk=chunk, score=0.5, doc_title=title) for chunk, title in rows]  # graph hit: neutral score


def merge_hits(hits: list[ChunkHit], extra: list[ChunkHit], top_k: int = 20) -> list[ChunkHit]:
    """Union by chunk id, keeping the best score; preserves original order (RRF comes in M5)."""
    seen: dict[str, ChunkHit] = {}
    for hit in [*hits, *extra]:
        if hit.chunk.id not in seen or hit.score > seen[hit.chunk.id].score:
            seen[hit.chunk.id] = hit
    return sorted(seen.values(), key=lambda h: h.score, reverse=True)[:top_k]


# ── image retrieval ─────────────────────────────────────────────────────────────


@dataclass
class ImageHit:
    image: ImageRecord
    score: float
    doc_title: str


async def store_image(
    session: AsyncSession,
    doc_id: str,
    file_path: str,
    caption: str,
    tags: list[str],
    embedding: list[float],
) -> ImageRecord:
    row = ImageRecord(doc_id=doc_id, file_path=file_path, caption=caption, tags=tags, embedding=embedding)
    session.add(row)
    await session.commit()
    return row


async def image_search(
    session: AsyncSession,
    query_embedding: list[float],
    corpus: str | None = None,
    top_k: int = 5,
) -> list[ImageHit]:
    stmt = (
        select(
            ImageRecord,
            ImageRecord.embedding.cosine_distance(query_embedding).label("distance"),
            Document.title,
        )
        .join(Document, Document.id == ImageRecord.doc_id)
        .order_by("distance")
        .limit(top_k)
    )
    if corpus:
        stmt = stmt.where(Document.corpus == corpus)
    rows = (await session.execute(stmt)).all()
    return [ImageHit(image=image, score=round(1 - distance, 4), doc_title=title) for image, distance, title in rows]
