"""Retrieval layer: pgvector cosine + Postgres tsvector keyword, fused with RRF."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import String, bindparam, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chunk, Document, ImageRecord, ParentChunk


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
    parent_ids: list[str | None] | None = None,
) -> list[Chunk]:
    """Persist chunk rows (text + embedding) for one document.

    `parent_ids` aligns with `chunks` for parent-child mode (P3.1); flat mode
    leaves them None.
    """
    parent_ids = parent_ids or [None] * len(chunks)
    rows = [
        Chunk(
            doc_id=doc_id,
            text=text,
            chunk_index=i,
            tokens=len(text.split()),
            embedding=emb,
            parent_id=parent_ids[i],
        )
        for i, (text, emb) in enumerate(zip(chunks, embeddings, strict=False))
    ]
    session.add_all(rows)
    await session.commit()
    return rows


async def store_parents(
    session: AsyncSession,
    doc_id: str,
    parents: list[str],
    start_indices: list[int] | None = None,
) -> list[ParentChunk]:
    """Persist un-embedded parent blocks for one document (P3.1).

    `start_indices[i]` = the doc-wide chunk_index of the first child under
    parent i (used for source display / navigation).
    """
    start_indices = start_indices or [0] * len(parents)
    rows = [
        ParentChunk(doc_id=doc_id, text=text, start_chunk_idx=start_indices[i])
        for i, text in enumerate(parents)
    ]
    session.add_all(rows)
    await session.commit()
    return rows


async def expand_to_parents(session: AsyncSession, hits: list[ChunkHit]) -> list[ChunkHit]:
    """P3.1 small-to-big: swap child snippets for their parent blocks.

    Children are searched/reranked; each surviving child's text is replaced by
    its parent's text so context blocks (and eval contexts) are full sections.
    Citation accounting keeps the child chunk ids; hits without a parent
    (flat corpora) pass through untouched.
    """
    child_ids = [h.chunk.parent_id for h in hits if h.chunk.parent_id]
    if not child_ids:
        return hits
    parents = {
        p.id: p
        for p in (
            await session.execute(select(ParentChunk).where(ParentChunk.id.in_(child_ids)))
        ).scalars()
    }
    if not parents:
        return hits
    best_by_parent: dict[str, ChunkHit] = {}
    for h in hits:
        pid = h.chunk.parent_id
        if not pid or pid not in parents:
            continue
        if pid not in best_by_parent or h.score > best_by_parent[pid].score:
            kept = ChunkHit(
                chunk=Chunk(
                    id=h.chunk.id,
                    doc_id=h.chunk.doc_id,
                    text=parents[pid].text,
                    chunk_index=h.chunk.chunk_index,
                    tokens=h.chunk.tokens,
                    embedding=h.chunk.embedding,
                    parent_id=pid,
                ),
                score=h.score,
                doc_title=h.doc_title,
                rerank_score=h.rerank_score,
            )
            best_by_parent[pid] = kept
    if not best_by_parent:
        return hits
    seen: set[str] = set()
    expanded: list[ChunkHit] = []
    rest: list[ChunkHit] = []
    for h in hits:
        pid = h.chunk.parent_id
        if pid and pid in best_by_parent:
            if pid not in seen:
                expanded.append(best_by_parent[pid])
                seen.add(pid)
        else:
            rest.append(h)
    return sorted([*expanded, *rest], key=lambda h: h.score, reverse=True)[: len(hits)]


async def vector_search(
    session: AsyncSession,
    query_embedding: list[float],
    corpus: str | None = None,
    top_k: int = 10,
    meta: dict | None = None,
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
    stmt = _apply_meta_filters(stmt, meta)
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
    meta: dict | None = None,
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
    sql, params = _apply_meta_filters_sql(sql, params, meta)
    sql += "ORDER BY rank DESC LIMIT :limit"
    rows = (await session.execute(text(sql), params)).all()
    hits: list[ChunkHit] = []
    for row in rows:
        chunk = _chunk_from_row(row[:5])
        title = row[5]
        rank = float(row[6] or 0.0)
        hits.append(ChunkHit(chunk=chunk, score=round(rank, 4), doc_title=title))
    return hits


def _meta_date(value) -> str | None:
    """Normalize a date-ish value to 'YYYY-MM-DD' (best-effort)."""
    if not value:
        return None
    text = str(value).strip()[:10]
    return text if len(text) == 10 and text[4] == "-" and text[7] == "-" else None


def _meta_datetime(value: str | None) -> datetime | None:
    """'YYYY-MM-DD' → midnight-UTC datetime for timestamptz comparisons."""
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)


def _apply_meta_filters(stmt, meta: dict | None):
    """P3.2: document-level filters on the vector arm (tags/date/author)."""
    if not meta:
        return stmt
    tags = [t for t in (meta.get("tags") or []) if isinstance(t, str) and t.strip()]
    if tags:
        stmt = stmt.where(
            text("CAST(documents.tags AS text[]) && CAST(:mtags AS text[])").bindparams(
                bindparam("mtags", value=tags, type_=ARRAY(String))
            )
        )
    date_from = _meta_datetime(_meta_date(meta.get("date_from")))
    if date_from:
        stmt = stmt.where(Document.doc_date >= date_from)
    date_to = _meta_datetime(_meta_date(meta.get("date_to")))
    if date_to:
        stmt = stmt.where(Document.doc_date <= date_to)
    author = (meta.get("author") or "").strip()
    if author:
        stmt = stmt.where(Document.author.ilike(f"%{author}%"))
    return stmt


def _apply_meta_filters_sql(sql: str, params: dict, meta: dict | None) -> tuple[str, dict]:
    """P3.2: document-level filters on the keyword arm (raw SQL variant)."""
    if not meta:
        return sql, params
    tags = [t for t in (meta.get("tags") or []) if isinstance(t, str) and t.strip()]
    if tags:
        sql += "AND CAST(d.tags AS text[]) && CAST(:mtags AS text[]) "
        params["mtags"] = tags
    date_from = _meta_datetime(_meta_date(meta.get("date_from")))
    if date_from:
        sql += "AND d.doc_date >= :mdate_from "
        params["mdate_from"] = date_from
    date_to = _meta_datetime(_meta_date(meta.get("date_to")))
    if date_to:
        sql += "AND d.doc_date <= :mdate_to "
        params["mdate_to"] = date_to
    author = (meta.get("author") or "").strip()
    if author:
        sql += "AND d.author ILIKE :mauthor "
        params["mauthor"] = f"%{author}%"
    return sql, params


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
