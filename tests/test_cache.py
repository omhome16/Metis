import pytest
import redis.asyncio as aioredis

from app.cache import cache_lookup, cache_stats, cache_store
from app.core.config import settings
from app.rag.embeddings import get_embedder


@pytest.fixture
async def require_redis():
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        pytest.skip("Redis not reachable — start with: docker compose up -d cache")
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


async def test_cache_roundtrip(require_redis):
    redis = require_redis
    embedder = get_embedder()
    await cache_store(redis, "What is FastAPI?", "tech", {"answer": "A web framework.", "done": {"answer_id": "x"}})

    qvec = await embedder.embed_query("What is FastAPI?")
    entry = await cache_lookup(redis, qvec, "tech")
    assert entry and entry["answer"] == "A web framework."

    # dissimilar question → miss
    other = await embedder.embed_query("Completely unrelated: sunsets over the sea")
    assert await cache_lookup(redis, other, "tech") is None


async def test_ask_route_serves_cached_answer(client, require_redis):
    """Regression: a second identical /ask must replay the cached answer (cached: true)."""
    import uuid
    from sqlalchemy import delete, select

    from app.db.models import Chunk, Document
    from app.rag.embeddings import get_embedder
    from app.rag.retrieval import store_chunks

    corpus = f"test-cache-{uuid.uuid4().hex[:8]}"
    embedder = get_embedder()
    async with async_session_factory() as session:
        doc = Document(
            id=str(uuid.uuid4()), title="d", corpus=corpus, format="txt",
            content_hash=uuid.uuid4().hex, raw_text="x",
        )
        session.add(doc)
        await session.commit()
        embs = await embedder.embed_texts(["FastAPI is built on Starlette and Pydantic."])
        await store_chunks(session, doc.id, ["FastAPI is built on Starlette and Pydantic."], embs)

    from app.gateway.mock import MockProvider

    class CachedFakeGateway:
        async def chat_stream(self, task, messages, temperature=0.7, max_tokens=None):
            yield "Cached-able answer [1]. "

        async def structured(self, task, messages, json_schema):
            return {}

    from unittest.mock import patch

    with patch("app.api.routes.ask.get_gateway", return_value=CachedFakeGateway()):
        first = await client.post("/api/v1/ask", json={"question": "What is FastAPI built on?", "corpus": corpus})
        second = await client.post("/api/v1/ask", json={"question": "What is FastAPI built on?", "corpus": corpus})

    def _done(text: str) -> dict:
        for block in text.replace("\r\n", "\n").strip().split("\n\n"):
            if "event: done" in block:
                import json

                return json.loads(block.split("data: ", 1)[1])
        return {}

    done1 = _done(first.text)
    done2 = _done(second.text)
    assert done1["answer_id"] == done2["answer_id"]  # same cached answer
    assert done2.get("cached") is True

    async with async_session_factory() as session:
        await session.execute(delete(Chunk).where(Chunk.doc_id.in_(select(Document.id).where(Document.corpus == corpus))))
        await session.execute(delete(Document).where(Document.corpus == corpus))
        await session.commit()


async def test_cache_stats(require_redis):
    redis = require_redis
    embedder = get_embedder()
    await cache_store(redis, "q1", None, {"answer": "a", "done": {}})
    qvec = await embedder.embed_query("q1")
    await cache_lookup(redis, qvec, None)  # hit
    await cache_lookup(redis, await embedder.embed_query("q2"), None)  # miss
    stats = await cache_stats()
    assert stats["hits"] >= 1
    assert stats["misses"] >= 1
    assert stats["entries"] >= 1
    assert 0.0 <= stats["hit_rate"] <= 1.0
