"""P5: global graph sensemaking — communities, summaries, global answers."""

import json
import uuid
from unittest.mock import patch

from app.gateway.base import ChatResult
from app.gateway.gateway import LLMGateway
from app.gateway.mock import MockProvider
from app.rag.global_search import global_intent


def _mock_gateway() -> LLMGateway:
    """Task-routed gateway over the deterministic mock, no real providers (no network)."""
    from app.core.config import Settings

    return LLMGateway(
        settings=Settings(groq_api_key="", gemini_api_key=""),
        clients={"mock": MockProvider()},
    )


class FakeChatGateway:
    """Non-streaming chat gateway for the map-reduce path (no network)."""

    async def chat(self, task, messages, temperature=0.7, max_tokens=None) -> ChatResult:
        last = next(m for m in reversed(messages) if m.get("role") == "user")
        text = last["content"].split("Question:")[-1][:60]
        return ChatResult(text=f"[mock:{task}] {text}", model="mock", usage={"in": 10, "out": 20})


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.replace("\r\n", "\n").strip().split("\n\n"):
        event, data = None, None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        if event is not None:
            events.append((event, data))
    return events


async def _community_ids(store) -> set[str]:
    async with store._driver.session() as session:
        result = await session.run("MATCH (c:Community) RETURN c.id AS id")
        return {rec["id"] async for rec in result}


async def _cleanup(store, prefix: str, keep_communities: set[str]) -> None:
    """Remove entities of this test and any community nodes it created."""
    async with store._driver.session() as session:
        await session.run(
            "MATCH (c:Community) WHERE NOT c.id IN $keep DETACH DELETE c",
            keep=sorted(keep_communities),
        )
        await session.run(
            "MATCH (e:Entity) WHERE e.canonical STARTS WITH $p DETACH DELETE e",
            p=prefix.lower(),
        )


def test_global_intent_matches():
    for q in [
        "What are the main themes of the library?",
        "Summarize the library",
        "what does the corpus say about leadership?",
        "Give me an overview of the vault",
        "What is the big picture of the knowledge base?",
        "themes",
    ]:
        assert global_intent(q), q


def test_global_intent_rejects_local_questions():
    for q in [
        "Compare the strategies of Sun Tzu and Machiavelli",
        "Who wrote The Art of War?",
        "How does mitosis relate to meiosis?",
    ]:
        assert not global_intent(q), q


async def test_lpa_communities_deterministic(require_graph):
    """P5: community assignment is deterministic on a fixture graph."""
    from app.graph.communities import detect_communities

    store = require_graph
    keep = await _community_ids(store)
    prefix = f"P5-det-{uuid.uuid4().hex[:6]}"
    nodes = [f"{prefix}-{i}" for i in range(6)]
    for n in nodes:
        await store.add_entity(n, "Concept")
    # two clusters: 0-1-2 and 3-4, plus an isolated node 5
    for a, b in [(nodes[0], nodes[1]), (nodes[1], nodes[2]), (nodes[3], nodes[4])]:
        await store.add_relation(a, b, "RELATED_TO")

    try:
        first = await detect_communities(store)
        assert first["engine"] == "lpa"
        assert first["communities"] >= 3  # two clusters + isolated node
        assert first["entities"] >= 6

        async def _assignment():
            async with store._driver.session() as session:
                result = await session.run(
                    "MATCH (e:Entity) WHERE e.canonical STARTS WITH $p "
                    "RETURN e.canonical AS name, e.community_id AS cid, e.community_rank AS rank",
                    p=prefix.lower(),
                )
                return {rec["name"]: (rec["cid"], rec["rank"]) async for rec in result}

        assignment1 = await _assignment()
        second = await detect_communities(store)
        assert second["communities"] == first["communities"]
        assignment2 = await _assignment()
        assert assignment1 == assignment2

        # cluster members share a community; the isolated node has its own
        c01 = assignment1[nodes[0].lower()]
        assert assignment1[nodes[1].lower()][0] == c01[0]
        assert assignment1[nodes[2].lower()][0] == c01[0]
        assert assignment1[nodes[5].lower()][0] != c01[0]
        # rank is a positive int, distinct within a community
        assert isinstance(c01[1], int) and c01[1] >= 1
    finally:
        await _cleanup(store, prefix, keep)


async def test_communities_endpoint_idempotent(require_graph):
    """P5: detection + summaries; re-running summaries is idempotent per community."""
    from app.graph.communities import detect_communities, summarize_communities

    store = require_graph
    keep = await _community_ids(store)
    prefix = f"P5-ep-{uuid.uuid4().hex[:6]}"
    nodes = [f"{prefix}-{i}" for i in range(4)]
    for n in nodes:
        await store.add_entity(n, "Concept")
    for a, b in [(nodes[0], nodes[1]), (nodes[1], nodes[2]), (nodes[2], nodes[3])]:
        await store.add_relation(a, b, "RELATED_TO")

    try:
        detected = await detect_communities(store)
        assert detected["communities"] >= 1

        gateway = _mock_gateway()
        first = await summarize_communities(gateway, store)
        assert first["total"] >= 1
        assert first["summaries"] + first["skipped"] == first["total"]
        assert first["summaries"] >= 1  # fresh summaries were written
        second = await summarize_communities(gateway, store)
        assert second["summaries"] == 0  # idempotent — nothing re-summarized
        assert second["skipped"] == second["total"]

        async with store._driver.session() as session:
            result = await session.run(
                "MATCH (c:Community) RETURN count(c) AS total, "
                "count(CASE WHEN c.summary IS NOT NULL THEN 1 END) AS with_summary"
            )
            record = await result.single()
            assert record and record["with_summary"] == record["total"]
            # the test cluster's community carries a non-empty summary
            result = await session.run(
                "MATCH (e:Entity) WHERE e.canonical STARTS WITH $p "
                "RETURN e.community_id AS cid LIMIT 1",
                p=prefix.lower(),
            )
            cid = (await result.single())["cid"]
            result = await session.run(
                "MATCH (c:Community {id: $id}) RETURN c.summary AS s", id=cid
            )
            record = await result.single()
            assert record and record["s"]
    finally:
        await _cleanup(store, prefix, keep)


async def test_global_answer_cites_communities(client, require_graph, require_db):
    """P5: a global-intent question in the deep lane answers from community summaries."""
    from sqlalchemy import delete

    from app.db.models import Chunk, Document
    from app.db.session import async_session_factory
    from app.graph.communities import detect_communities, summarize_communities
    from app.rag.embeddings import get_embedder
    from app.rag.retrieval import store_chunks

    store = require_graph
    keep = await _community_ids(store)
    prefix = f"P5-gl-{uuid.uuid4().hex[:6]}"
    for n in ["Deception", "Strategy", "Leadership", "Warfare"]:
        await store.add_entity(f"{prefix}-{n}", "Concept")
    for a, b in [("Deception", "Strategy"), ("Strategy", "Leadership"), ("Leadership", "Warfare")]:
        await store.add_relation(f"{prefix}-{a}", f"{prefix}-{b}", "RELATED_TO")
    await detect_communities(store)
    await summarize_communities(_mock_gateway(), store)

    corpus = f"test-global-{uuid.uuid4().hex[:8]}"
    embedder = get_embedder()
    doc_id = str(uuid.uuid4())
    async with async_session_factory() as session:
        doc = Document(
            id=doc_id,
            title="Policy Primer",
            corpus=corpus,
            format="txt",
            content_hash=uuid.uuid4().hex,
            raw_text="The 2024 strategy emphasizes deception and indirect approaches.",
        )
        session.add(doc)
        await session.commit()
        chunks = [
            "The 2024 strategy emphasizes deception and indirect approaches.",
            "Leadership themes recur across the corpus.",
        ]
        embeddings = await embedder.embed_texts(chunks)
        await store_chunks(session, doc_id, chunks, embeddings)

    try:
        with patch("app.api.routes.ask.get_gateway", return_value=FakeChatGateway()):
            resp = await client.post(
                "/api/v1/ask",
                json={"question": "What themes run across the corpus?", "corpus": corpus},
            )
        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        names = [e for e, _ in events]
        assert "sources" in names
        sources = dict(events)["sources"]
        assert sources.get("communities"), "global answers cite communities"
        assert sources["communities"][0]["summary"]
        citations = dict(events)["citations"]
        assert citations["grounded"] is True
        assert citations["citations"][0]["community_id"]
        assert dict(events)["meta"]["mode"] == "global"
        done = dict(events)["done"]
        assert done["usage"]["lane"] == "deep"
        assert done["usage"]["mode"] == "global"
    finally:
        async with async_session_factory() as session:
            await session.execute(delete(Chunk).where(Chunk.doc_id == doc_id))
            await session.execute(delete(Document).where(Document.id == doc_id))
            await session.commit()
        await _cleanup(store, prefix, keep)


async def test_global_falls_back_without_communities(client, require_graph, require_db):
    """P5: global intent with no community summaries degrades to standard retrieval."""
    from app.db.models import Document
    from app.db.session import async_session_factory
    from app.rag.embeddings import get_embedder
    from app.rag.retrieval import store_chunks
    from tests.test_ask import FakeGateway
    from tests.test_ask import _cleanup as _cleanup_db

    _ = require_graph  # graph is up but has no communities
    corpus = f"test-global-fb-{uuid.uuid4().hex[:8]}"
    embedder = get_embedder()
    doc_id = str(uuid.uuid4())
    async with async_session_factory() as session:
        doc = Document(
            id=doc_id,
            title="Themes Doc",
            corpus=corpus,
            format="txt",
            content_hash=uuid.uuid4().hex,
            raw_text="Sun Tzu wrote The Art of War.",
        )
        session.add(doc)
        await session.commit()
        chunks = ["Sun Tzu wrote The Art of War.", "Deception is central to warfare."]
        embeddings = await embedder.embed_texts(chunks)
        await store_chunks(session, doc_id, chunks, embeddings)

    try:
        with patch("app.api.routes.ask.get_gateway", return_value=FakeGateway()):
            resp = await client.post(
                "/api/v1/ask",
                json={"question": "Summarize the library", "corpus": corpus},
            )
        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        sources = dict(events)["sources"]
        assert "communities" not in sources
        assert sources["chunks"]  # fell back to standard retrieval
        assert dict(events)["meta"]["lane"] == "deep"
        assert dict(events)["done"]["usage"]["lane"] == "deep"
    finally:
        await _cleanup_db(corpus)
