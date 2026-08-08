"""Retrieval layer: pgvector cosine + Postgres tsvector keyword, fused with RRF."""

from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chunk, Document, ImageRecord


@dataclass
class ChunkHit:
    chunk: Chunk
    score: float  # fusion / cosine similarity (1 − cosine distance)
    doc_title: str
    rerank_score: float | None = None  # cross-encoder score, when reranked


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
    """Union by chunk id, keeping the best score."""
    seen: dict[str, ChunkHit] = {}
    for hit in [*hits, *extra]:
        if hit.chunk.id not in seen or hit.score > seen[hit.chunk.id].score:
            seen[hit.chunk.id] = hit
    return sorted(seen.values(), key=lambda h: h.score, reverse=True)[:top_k]


# ── keyword search (Postgres full-text) ───────────────────────────────────────


def _chunk_from_row(row) -> Chunk:
    """Rebuild a lightweight Chunk from a raw keyword-search row."""
    return Chunk(
        id=row[0], doc_id=row[1], text=row[2], chunk_index=row[3], tokens=row[4], embedding=None
    )


async def keyword_search(
    session: AsyncSession,
    query: str,
    corpus: str | None = None,
    top_k: int = 10,
) -> list[ChunkHit]:
    """Postgres tsvector search ranked by ts_rank (BM25-style)."""
    if not query.strip():
        return []
    sql = (
        "SELECT c.id, c.doc_id, c.text, c.chunk_index, c.tokens, d.title, "
        "ts_rank(to_tsvector('english', c.text), plainto_tsquery('english', :q)) AS rank "
        "FROM chunks c JOIN documents d ON d.id = c.doc_id "
        "WHERE to_tsvector('english', c.text) @@ plainto_tsquery('english', :q) "
    )
    params: dict = {"q": query, "limit": top_k}
    if corpus:
        sql += "AND d.corpus = :corpus "
        params["corpus"] = corpus
    sql += "ORDER BY rank DESC LIMIT :limit"
    rows = (await session.execute(text(sql), params)).all()
    hits: list[ChunkHit] = []
    for row in rows:
        chunk = _chunk_from_row(row[:5])
        title = row[5]
        rank = float(row[6] or 0.0)
        hits.append(ChunkHit(chunk=chunk, score=round(rank, 4), doc_title=title))
    return hits


def fuse_hybrid(vector_hits: list[ChunkHit], keyword_hits: list[ChunkHit], top_k: int = 20, k: int = 60) -> list[ChunkHit]:
    """Reciprocal Rank Fusion over the ranked lists from each retriever."""
    scores: dict[str, float] = {}
    for ranked in (vector_hits, keyword_hits):
        for i, hit in enumerate(ranked):
            scores[hit.chunk.id] = scores.get(hit.chunk.id, 0.0) + 1.0 / (k + i + 1)
    by_id = {hit.chunk.id: hit for hit in [*vector_hits, *keyword_hits]}
    ordered = sorted(scores.items(), key=lambda kv: -kv[1])[:top_k]
    result: list[ChunkHit] = []
    for chunk_id, score in ordered:
        hit = by_id[chunk_id]
        hit.score = round(score, 4)
        result.append(hit)
    return result


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
