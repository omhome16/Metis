"""Cache v2 (P2.3): grounded Postgres cache — hit/miss, semantic hit, version and TTL gating."""

import uuid
from datetime import UTC

import pytest
from sqlalchemy import delete

from app.cache import _cosine, cache_lookup, cache_stats, cache_store
from app.db.models import CacheEntry
from app.db.session import async_session_factory
from app.db.versions import bump_corpus_version, get_corpus_version
from app.rag.embeddings import get_embedder


def _vec(dim: int = 1024, seed: float = 1.0) -> list[float]:
    """Unit vector with a knob: [seed, sqrt(1-seed^2), 0, ...] — dim must match the column."""
    v = [0.0] * dim
    v[0] = seed
    v[1] = (1.0 - seed * seed) ** 0.5
    return v


async def test_cache_roundtrip_hit_and_miss(require_db):
    corpus = f"cache-{uuid.uuid4().hex[:8]}"
    embedder = get_embedder()
    qvec = await embedder.embed_query("What is FastAPI?")
    async with async_session_factory() as session:
        await cache_store(
            session,
            "What is FastAPI?",
            corpus,
            {"answer": "A web framework.", "done": {"answer_id": "x"}},
        )
        hit = await cache_lookup(session, qvec, corpus)
        assert hit and hit["answer"] == "A web framework."

        other = await embedder.embed_query("Completely unrelated: sunsets over the sea")
        assert await cache_lookup(session, other, corpus) is None
        # cleanup
        await session.execute(delete(CacheEntry).where(CacheEntry.corpus == corpus))
        await session.commit()


async def _insert_entry(
    corpus: str, vec: list[float], version: int = 1, expires_days: int = 7
) -> None:
    """Insert a cache row directly (bypasses the embedder — hand-crafted vectors)."""
    from datetime import datetime, timedelta

    from app.db.models import CacheEntry

    async with async_session_factory() as session:
        session.add(
            CacheEntry(
                corpus=corpus,
                question="q",
                question_embedding=vec,
                answer="a",
                sources={},
                citations={},
                done={},
                corpus_version=version,
                expires_at=datetime.now(UTC) + timedelta(days=expires_days),
            )
        )
        await session.commit()


async def test_cache_semantic_hit_above_threshold(require_db):
    """Hand-crafted near-identical vectors must hit; distant ones must miss."""
    corpus = f"cache-sem-{uuid.uuid4().hex[:8]}"
    await _insert_entry(corpus, _vec())
    async with async_session_factory() as session:
        assert await cache_lookup(session, _vec(), corpus, current_version=1) is not None
        assert await cache_lookup(session, _vec(seed=0.999), corpus, current_version=1) is not None
        assert await cache_lookup(session, _vec(seed=0.1), corpus, current_version=1) is None
        await session.execute(delete(CacheEntry).where(CacheEntry.corpus == corpus))
        await session.commit()


async def test_cache_version_mismatch_is_miss(require_db):
    corpus = f"cache-ver-{uuid.uuid4().hex[:8]}"
    await _insert_entry(corpus, _vec(), version=1)
    async with async_session_factory() as session:
        assert await cache_lookup(session, _vec(), corpus, current_version=2) is None
        await session.execute(delete(CacheEntry).where(CacheEntry.corpus == corpus))
        await session.commit()


async def test_cache_expired_is_miss(require_db):
    corpus = f"cache-ttl-{uuid.uuid4().hex[:8]}"
    await _insert_entry(corpus, _vec(), expires_days=-1)
    async with async_session_factory() as session:
        assert await cache_lookup(session, _vec(), corpus, current_version=1) is None
        await session.execute(delete(CacheEntry).where(CacheEntry.corpus == corpus))
        await session.commit()


async def test_cache_stale_on_new_ingest(require_db):
    """Bumping the corpus version (ingest/delete) invalidates old entries."""
    corpus = f"cache-bump-{uuid.uuid4().hex[:8]}"
    async with async_session_factory() as session:
        await cache_store(session, "q", corpus, {"answer": "a", "done": {}})
        version_before = await get_corpus_version(session, corpus)
        await bump_corpus_version(session, corpus)
        version_after = await get_corpus_version(session, corpus)
        assert version_after > version_before
        assert await cache_lookup(session, _vec(), corpus, current_version=version_after) is None


# ── P8 near-duplicate guard ──────────────────────────────────────────────────


def test_query_too_different_guard():
    from app.cache import _query_too_different

    q1 = "Do Hobbes and Locke agree on the state of nature?"
    q2 = "Do Hobbes and Rousseau agree on the state of nature?"
    assert _query_too_different(q1, q2) is True  # entity swap → block
    assert _query_too_different(q1, q1) is False  # identical → serve
    assert _query_too_different(q1, "Completely unrelated question about pasta?") is True
    assert (
        _query_too_different("what is the state of nature", "what is the state of nature?") is False
    )
    assert (
        _query_too_different("what is the state of nature", "what is the meaning of life") is True
    )


async def test_cache_guard_blocks_entity_swapped_question(require_db):
    """P8: embedding-identical but entity-different questions must NOT share a hit."""
    from datetime import datetime, timedelta

    corpus = f"cache-swap-{uuid.uuid4().hex[:8]}"
    q1 = "Do Hobbes and Locke agree on the state of nature?"
    q2 = "Do Hobbes and Rousseau agree on the state of nature?"
    async with async_session_factory() as session:
        session.add(
            CacheEntry(
                corpus=corpus,
                question=q1,
                question_embedding=_vec(),
                answer="cached answer about Hobbes and Locke",
                sources={},
                citations={},
                done={},
                corpus_version=1,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
        )
        await session.commit()
    async with async_session_factory() as session:
        assert (
            await cache_lookup(session, _vec(), corpus, current_version=1, question=q1) is not None
        )
        assert await cache_lookup(session, _vec(), corpus, current_version=1, question=q2) is None
        assert (
            await cache_lookup(
                session,
                _vec(),
                corpus,
                current_version=1,
                question="Could you explain what Hobbes thinks about the state of nature?",
            )
            is None
        )
        assert await cache_lookup(session, _vec(), corpus, current_version=1) is not None
        await session.execute(delete(CacheEntry).where(CacheEntry.corpus == corpus))
        await session.commit()
        await session.execute(delete(CacheEntry).where(CacheEntry.corpus == corpus))
        await session.commit()


async def test_cache_stats(require_db):
    corpus = f"cache-stats-{uuid.uuid4().hex[:8]}"
    embedder = get_embedder()
    async with async_session_factory() as session:
        await cache_store(session, "q1", corpus, {"answer": "a", "done": {}}, current_version=1)
        qvec = await embedder.embed_query("q1")
        await cache_lookup(session, qvec, corpus, current_version=1)  # hit
        await cache_lookup(
            session, await embedder.embed_query("q2"), corpus, current_version=1
        )  # miss
        stats = await cache_stats(session)
        assert stats["entries"] >= 1
        assert stats["hits"] >= 1
        assert stats["misses"] >= 1
        assert 0.0 <= stats["hit_rate"] <= 1.0
        await session.execute(delete(CacheEntry).where(CacheEntry.corpus == corpus))
        await session.commit()


async def test_ask_route_replays_cached_answer(client, require_db):
    """Regression: a second identical /ask must replay the cached answer (cached: true)."""
    import json as _json

    corpus = f"ask-cache-{uuid.uuid4().hex[:8]}"
    async with async_session_factory() as session:
        await cache_store(
            session,
            "What is FastAPI built on?",
            corpus,
            {
                "answer": "Cached-able answer [1]. ",
                "sources": {"chunks": []},
                "citations": {"citations": []},
                "done": {"answer_id": "cached-1"},
            },
        )
        await session.commit()

    class CachedFakeGateway:
        async def chat_stream(self, task, messages, temperature=0.7, max_tokens=None):
            yield "Never reached — cache replays [1]. "

        async def structured(self, task, messages, json_schema):
            return {}

    from unittest.mock import patch

    with patch("app.api.routes.ask.get_gateway", return_value=CachedFakeGateway()):
        first = await client.post(
            "/api/v1/ask", json={"question": "What is FastAPI built on?", "corpus": corpus}
        )
        second = await client.post(
            "/api/v1/ask", json={"question": "What is FastAPI built on?", "corpus": corpus}
        )

    def _done(text: str) -> dict:
        for block in text.replace("\r\n", "\n").strip().split("\n\n"):
            if "event: done" in block:
                return _json.loads(block.split("data: ", 1)[1])
        return {}

    done1 = _done(first.text)
    done2 = _done(second.text)
    assert done1["answer_id"] == done2["answer_id"] == "cached-1"
    assert done2.get("cached") is True
    assert "Cached-able" in second.text and "answer" in second.text

    async with async_session_factory() as session:
        await session.execute(delete(CacheEntry).where(CacheEntry.corpus == corpus))
        await session.commit()


def test_cosine_helper():
    assert _cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert _cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)
    assert _cosine([], []) == 0.0
