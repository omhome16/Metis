"""P6: feedback endpoint CRUD + negative-feedback cache eviction."""

import uuid

import pytest

from app.cache import cache_lookup, cache_store
from app.db.models import Conversation
from app.db.session import async_session_factory
from app.rag.embeddings import get_embedder


@pytest.fixture
async def message_pair(require_db):
    """A persisted user→assistant exchange in a fresh conversation."""
    from app.api.routes.conversations import append_message

    corpus = f"test-fb-{uuid.uuid4().hex[:8]}"
    question = f"Question about feedback {uuid.uuid4().hex[:6]}"
    async with async_session_factory() as session:
        conv = Conversation(vault_name=corpus, title="feedback test")
        session.add(conv)
        await session.commit()
        await append_message(session, conv.id, "user", question)
        assistant = await append_message(session, conv.id, "assistant", "Some answer.")
        yield corpus, question, assistant.id
        async with async_session_factory() as cleanup:
            conv2 = await cleanup.get(Conversation, conv.id)
            if conv2 is not None:
                await cleanup.delete(conv2)
                await cleanup.commit()


async def test_feedback_upsert_and_validation(client, message_pair):
    corpus, question, message_id = message_pair

    resp = await client.post(f"/api/v1/ask/{message_id}/feedback", json={"rating": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["rating"] == 1 and body["message_id"] == message_id

    # re-posting replaces (one row per message, latest wins)
    resp = await client.post(
        f"/api/v1/ask/{message_id}/feedback", json={"rating": -1, "note": "hallucinated"}
    )
    assert resp.status_code == 200
    assert resp.json()["rating"] == -1

    resp = await client.get("/api/v1/evals/feedback")
    assert resp.status_code == 200
    stats = resp.json()
    assert stats["down"] >= 1
    assert any(
        r["message_id"] == message_id and r["note"] == "hallucinated" for r in stats["recent"]
    )


async def test_feedback_unknown_message_404(client):
    resp = await client.post(f"/api/v1/ask/{uuid.uuid4()}/feedback", json={"rating": 1})
    assert resp.status_code == 404


async def test_feedback_rating_validation(client, message_pair):
    _, _, message_id = message_pair
    for bad in (0, 2, -2):
        resp = await client.post(f"/api/v1/ask/{message_id}/feedback", json={"rating": bad})
        assert resp.status_code == 422


async def test_negative_feedback_evicts_cache(client, message_pair):
    corpus, question, message_id = message_pair
    embedder = get_embedder()

    async with async_session_factory() as session:
        await cache_store(
            session,
            question,
            corpus,
            {"sources": {"chunks": []}, "citations": {}, "done": {}, "answer": "cached answer"},
        )
        vec = await embedder.embed_query(question)
        hit = await cache_lookup(session, vec, corpus)
        assert hit is not None, "cache entry should be findable before feedback"

    resp = await client.post(f"/api/v1/ask/{message_id}/feedback", json={"rating": -1})
    assert resp.status_code == 200

    async with async_session_factory() as session:
        vec = await embedder.embed_query(question)
        miss = await cache_lookup(session, vec, corpus)
        assert miss is None, "negative feedback must evict matching cache entries"


async def test_positive_feedback_keeps_cache(client, message_pair):
    corpus, question, message_id = message_pair
    embedder = get_embedder()

    async with async_session_factory() as session:
        await cache_store(
            session,
            question,
            corpus,
            {"sources": {"chunks": []}, "citations": {}, "done": {}, "answer": "cached answer"},
        )

    resp = await client.post(f"/api/v1/ask/{message_id}/feedback", json={"rating": 1})
    assert resp.status_code == 200

    async with async_session_factory() as session:
        vec = await embedder.embed_query(question)
        hit = await cache_lookup(session, vec, corpus)
        assert hit is not None, "positive feedback must NOT evict cache entries"
