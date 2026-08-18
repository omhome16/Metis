"""Semantic cache v2 (P2.3): grounded, indexed, versioned.

Answers are stored in Postgres (`cache_entries`) with the question embedding in
a HALFVEC column behind an HNSW index. Lookup is ONE SQL query — nearest
neighbor by cosine distance, gated by corpus + TTL + corpus_version — instead of
the Redis scan-and-compare path (Redis is queue-only since P2).

P8 near-duplicate guard: cosine alone lets entity-swapped questions hit
("Do Hobbes and Locke agree…" vs "Do Hobbes and Rousseau agree…" are near-
identical in embedding space). A hit must also share ≥ `cache_min_jaccard` of
tokens and match entity-like (capitalized) tokens; loose paraphrases miss the
cache by design — a documented tradeoff.

Cache must never break ask: every public function swallows errors and returns a
miss. Hits replay the exact SSE contract with `cached: true`.
"""

import re
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
_TOKEN_RE = re.compile(r"[a-z0-9']+")
_CAP_RE = re.compile(r"\b[A-Z][a-z]+\b")


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
    question: str | None = None,
) -> dict | None:
    """Nearest cache entry within similarity threshold, version + TTL matched.

    Single query: `ORDER BY question_embedding <=> :q LIMIT 1`. Returns None on
    any failure — ask must never break because of the cache. When `question` is
    given, the P8 near-duplicate guard additionally requires token overlap +
    entity-token agreement with the stored question.
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
        if question and _query_too_different(row.question, question):
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


def _query_too_different(stored: str, question: str) -> bool:
    """P8 near-duplicate guard — False means "safe to serve the cached answer".

    Three cheap checks, no LLM: token Jaccard (loose paraphrases miss), length
    ratio, and capitalized-token agreement (catches single-entity swaps like
    "Hobbes and Locke" vs "Hobbes and Rousseau", which are near-identical in
    embedding space). Questions without capitalized tokens skip the entity check.
    """
    a, b = _TOKEN_RE.findall((stored or "").lower()), _TOKEN_RE.findall((question or "").lower())
    if not a or not b:
        return True
    union = set(a) | set(b)
    jaccard = len(set(a) & set(b)) / len(union)
    if jaccard < settings.cache_min_jaccard:
        return True
    ratio = max(len(a), len(b)) / max(min(len(a), len(b)), 1)
    if ratio > settings.cache_max_len_ratio:
        return True
    caps_a, caps_b = set(_CAP_RE.findall(stored or "")), set(_CAP_RE.findall(question or ""))
    if caps_a and caps_b and caps_a != caps_b:
        return True
    return False


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


async def cache_evict_question(
    session: AsyncSession, question: str, corpus: str | None = None
) -> int:
    """Delete cache entries semantically matching `question` (lookup threshold).

    Used by negative-feedback eviction (P6): thumbs-down on an answer should
    make the same/similar question miss. Returns rows deleted; never raises.
    """
    try:
        from app.rag.embeddings import get_embedder

        query_embedding = await get_embedder().embed_query(question)
        stmt = select(CacheEntry).where(CacheEntry.corpus == (corpus or "default"))
        rows = (await session.execute(stmt)).scalars().all()
        threshold = settings.cache_similarity_threshold
        victims = [r for r in rows if _cosine(r.question_embedding, query_embedding) >= threshold]
        for entry in victims:
            await session.delete(entry)
        await session.commit()
        if victims:
            logger.info(
                "cache evict: removed %d entries for corpus=%s", len(victims), corpus or "default"
            )
        return len(victims)
    except Exception as exc:  # noqa: BLE001 — eviction must never break feedback
        logger.warning("cache evict failed: %s", exc)
        return 0


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
