"""Semantic cache (blueprint §7 / §8.2 step 1).

Repeated queries within embedding similarity of a previous one get the cached answer
replayed, skipping retrieval + generation. Degrades to a miss when Redis is down.
"""

import hashlib
import json

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

CACHE_PREFIX = "metis:cache:q"
HIT_COUNTER = "metis:cache:hits"
MISS_COUNTER = "metis:cache:misses"
CACHE_TTL_SECONDS = 7 * 86400


def _key(corpus: str | None, question: str) -> str:
    digest = hashlib.sha1(question.encode()).hexdigest()[:12]
    return f"{CACHE_PREFIX}:{corpus or 'default'}:{digest}"


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5 or 1.0
    nb = sum(y * y for y in b) ** 0.5 or 1.0
    return dot / (na * nb)


async def get_redis() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)


async def cache_lookup(redis: aioredis.Redis, query_embedding: list[float], corpus: str | None = None) -> dict | None:
    """Return the cached entry whose question embedding is similar enough, else None."""
    threshold = settings.cache_similarity_threshold
    pattern = f"{CACHE_PREFIX}:{corpus or 'default'}:*"
    try:
        async for key in redis.scan_iter(pattern):
            raw = await redis.get(key)
            if not raw:
                continue
            entry = json.loads(raw)
            if _cosine(query_embedding, entry.get("embedding", [])) >= threshold:
                await redis.incr(HIT_COUNTER)
                return entry
        await redis.incr(MISS_COUNTER)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache lookup failed: %s", exc)
    return None


async def cache_store(redis: aioredis.Redis, question: str, corpus: str | None, payload: dict) -> None:
    try:
        from app.rag.embeddings import get_embedder

        query_embedding = await get_embedder().embed_query(question)
        key = _key(corpus, question)
        entry = {"question": question, "corpus": corpus, "embedding": query_embedding, **payload}
        await redis.set(key, json.dumps(entry), ex=CACHE_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache store failed: %s", exc)


async def cache_stats() -> dict:
    try:
        redis = await get_redis()
        hits, misses = await redis.mget(HIT_COUNTER, MISS_COUNTER)
        hits = int(hits or 0)
        misses = int(misses or 0)
        count = 0
        async for _ in redis.scan_iter(f"{CACHE_PREFIX}:*"):
            count += 1
        await redis.aclose()
        total = hits + misses
        return {
            "hits": hits,
            "misses": misses,
            "hit_rate": round(hits / total, 4) if total else 0.0,
            "entries": count,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache stats failed: %s", exc)
        return {"hits": 0, "misses": 0, "hit_rate": 0.0, "entries": 0}
