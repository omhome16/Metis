"""Semantic cache v2 (P2.3): grounded, indexed, versioned.

Answers are stored in Postgres (`cache_entries`) with the question embedding in
a HALFVEC column behind an HNSW index. Lookup is ONE SQL query — nearest
neighbor by cosine distance, gated by corpus + TTL + corpus_version — instead of
the Redis scan-and-compare path (Redis is queue-only since P2).

Cache must never break ask: every public function swallows errors and returns a
miss. Hits replay the exact SSE contract with `cached: true`.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import CacheEntry, CacheMetric
from app.db.versions import get_corpus_version

logger = get_logger(__name__)

_MISS_KEY = "misses"


def _now() -> datetime:
    return datetime.now(UTC)


def _to_payload(entry: CacheEntry) -> dict:
    """Shape stored fields as the ask route's cache payload (v1-compatible keys)."""
    return {
        "question": entry.question,
        "corpus": entry.corpus,
        "model": entry.model,
        "embed_model": entry.embed_model,
        "answer": entry.answer,
        "sources": entry.sources,
        "citations": entry.citations,
        "done": entry.done,
    }


async def _bump(session: AsyncSession, key: str) -> None:
    try:
        await session.execute(
            pg_insert(CacheMetric)
            .values(key=key, value=1)
            .on_conflict_do_update(
                index_elements=[CacheMetric.key],
                set_={"value": CacheMetric.value + 1},
            )
        )
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache metric bump failed (%s): %s", key, exc)


async def cache_lookup(
    session: AsyncSession,
    query_embedding: list[float],
    corpus: str | None = None,
    current_version: int | None = None,
) -> dict | None:
    """Nearest cache entry within similarity threshold, version + TTL matched.

    Single query: `ORDER BY question_embedding <=> :q LIMIT 1`. Returns None on
    any failure — ask must never break because of the cache.
    """
    if not query_embedding:
        return None
    try:
        stmt = (
            select(CacheEntry)
            .where(CacheEntry.corpus == (corpus or "default"))
            .where(CacheEntry.expires_at > _now())
            .order_by(CacheEntry.question_embedding.cosine_distance(query_embedding))
            .limit(1)
        )
        if current_version is not None:
            stmt = stmt.where(CacheEntry.corpus_version == current_version)
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            await _bump(session, _MISS_KEY)
            return None
        if _cosine(row.question_embedding, query_embedding) < settings.cache_similarity_threshold:
            await _bump(session, _MISS_KEY)
            return None
        await session.execute(
            update(CacheEntry)
            .where(CacheEntry.id == row.id)
            .values(hit_count=CacheEntry.hit_count + 1)
        )
        await session.commit()
        return _to_payload(row)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache lookup failed: %s", exc)
        return None


async def cache_store(
    session: AsyncSession,
    question: str,
    corpus: str | None,
    payload: dict,
    current_version: int | None = None,
) -> None:
    """Embed the question and write a grounded entry (7-day TTL via expires_at)."""
    try:
        from app.rag.embeddings import get_embedder

        query_embedding = await get_embedder().embed_query(question)
        if current_version is None:
            current_version = await get_corpus_version(session, corpus or "default")
        entry = CacheEntry(
            corpus=corpus or "default",
            question=question[:2000],
            question_embedding=query_embedding,
            answer=payload.get("answer", "") or "",
            sources=payload.get("sources") or {},
            citations=payload.get("citations") or {},
            done=payload.get("done") or {},
            model=settings.generation_model,
            embed_model=settings.embed_model,
            corpus_version=current_version,
            expires_at=_now() + timedelta(days=settings.cache_ttl_days),
        )
        session.add(entry)
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache store failed: %s", exc)


async def cache_stats(session: AsyncSession) -> dict:
    """Entries (non-expired), hits, misses and hit rate from Postgres counters."""
    try:
        from sqlalchemy import func

        count = (
            await session.execute(
                select(func.count(CacheEntry.id)).where(CacheEntry.expires_at > _now())
            )
        ).scalar_one()
        hits = (
            await session.execute(select(func.coalesce(func.sum(CacheEntry.hit_count), 0)))
        ).scalar_one()
        metrics = dict((await session.execute(select(CacheMetric.key, CacheMetric.value))).all())
        misses = metrics.get(_MISS_KEY, 0)
        total = hits + misses
        return {
            "hits": int(hits),
            "misses": int(misses),
            "hit_rate": round(int(hits) / total, 4) if total else 0.0,
            "entries": int(count),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache stats failed: %s", exc)
        return {"hits": 0, "misses": 0, "hit_rate": 0.0, "entries": 0}


def _cosine(a: list[float], b: list[float]) -> float:
    """Kept for tests that construct vectors directly (not used on the hot path)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5 or 1.0
    nb = sum(y * y for y in b) ** 0.5 or 1.0
    return dot / (na * nb)
