"""ReAct agent: tool-calling loop emits thinking events, falls back safely, and cites tools' findings."""

import json
import uuid
from collections.abc import AsyncIterator
from unittest.mock import patch

from sqlalchemy import delete, select

from app.db.models import Chunk, Document
from app.db.session import async_session_factory
from app.gateway.base import ToolCall, ToolStreamChunk
from app.rag.embeddings import get_embedder
from app.rag.retrieval import store_chunks


class ToolGateway:
    """Fake gateway with tool support: one search_vault call, then the final answer."""

    supports_tools = True
    searches = 0

    async def chat_stream(self, task, messages, temperature=0.7, max_tokens=None):
        yield "fallback"

    async def chat_tools_stream(self, task, messages, tools, temperature=0.7, max_tokens=None) -> AsyncIterator[ToolStreamChunk]:
        if ToolGateway.searches == 0:
            ToolGateway.searches += 1
            yield ToolStreamChunk(
                text="",
                tool_calls=[ToolCall(id="c1", name="search_vault", arguments={"query": "The Art of War", "top_k": 3})],
            )
            return
        yield ToolStreamChunk(text="Sun Tzu wrote The Art of War [1]. ")


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.replace("\r\n", "\n").strip().split("\n\n"):
        event, data = None, None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if event is not None:
            events.append((event, data))
    return events


async def _seed(corpus: str) -> str:
    embedder = get_embedder()
    doc_id = str(uuid.uuid4())
    async with async_session_factory() as session:
        doc = Document(
            id=doc_id,
            title="The Art of War",
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
    return doc_id


async def _cleanup(corpus: str) -> None:
    async with async_session_factory() as session:
        await session.execute(delete(Chunk).where(Chunk.doc_id.in_(select(Document.id).where(Document.corpus == corpus))))
        await session.execute(delete(Document).where(Document.corpus == corpus))
        await session.commit()


async def test_agent_loop_thinks_then_answers(client, require_db):
    corpus = f"test-agent-{uuid.uuid4().hex[:8]}"
    await _seed(corpus)
    ToolGateway.searches = 0
    try:
        with patch("app.api.routes.ask.get_gateway", return_value=ToolGateway()):
            resp = await client.post("/api/v1/ask", json={"question": "Compare Sun Tzu and Machiavelli", "corpus": corpus})
        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        names = [e for e, _ in events]
        assert "thinking" in names, "agent path should emit thinking events"
        assert names[-1] == "done"

        thinking = [d for e, d in events if e == "thinking"]
        assert thinking and thinking[0]["tool"] == "search_vault"

        answer = "".join(d["text"] for e, d in events if e == "tokens")
        assert "Sun Tzu wrote The Art of War" in answer

        # the tool's finding is cited
        citations = dict(events)["citations"]["citations"]
        assert citations, "agent sources should produce citations"
        assert any(c["n"] == 1 for c in citations)

        # richer final sources emitted after the initial placeholder
        sources = [d for e, d in events if e == "sources"]
        assert sources[-1]["chunks"], "final sources should include agent-retrieved chunks"
    finally:
        await _cleanup(corpus)


async def test_tool_stream_failure_still_grounds_answer(client, require_db):
    """When tool-calling fails mid-flight (rate limit), the agent must fall back to
    direct retrieval so the user still gets real sources, not an empty answer."""

    class BrokenTools:
        supports_tools = True

        async def chat_stream(self, task, messages, temperature=0.7, max_tokens=None):
            # direct-generation fallback path
            yield "The Art of War was written by Sun Tzu [1]. "

        async def chat_tools_stream(self, task, messages, tools, temperature=0.7, max_tokens=None):
            raise RuntimeError("tool-calling unavailable (rate limited)")

    corpus = f"test-toolfail-{uuid.uuid4().hex[:8]}"
    await _seed(corpus)
    try:
        with patch("app.api.routes.ask.get_gateway", return_value=BrokenTools()):
            resp = await client.post("/api/v1/ask", json={"question": "Who wrote The Art of War?", "corpus": corpus})
        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        answer = "".join(d["text"] for e, d in events if e == "tokens")
        assert "Sun Tzu" in answer, f"direct fallback should still answer from retrieved context: {answer!r}"
        sources = [d for e, d in events if e == "sources"]
        assert sources and sources[-1]["chunks"], "fallback must emit real retrieved sources"
        citations = dict(events)["citations"]["citations"]
        assert citations, "fallback sources should still be citable"
    finally:
        await _cleanup(corpus)


async def test_no_tool_support_falls_back(client, require_db):
    """A gateway without tools must still produce a full event sequence (direct path)."""

    class Plain:
        async def chat_stream(self, task, messages, temperature=0.7, max_tokens=None):
            yield "plain answer "

    corpus = f"test-nofall-{uuid.uuid4().hex[:8]}"
    await _seed(corpus)
    try:
        with patch("app.api.routes.ask.get_gateway", return_value=Plain()):
            resp = await client.post("/api/v1/ask", json={"question": "who?", "corpus": corpus})
        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        assert dict(events)["done"]["answer_id"]
        assert "".join(d["text"] for e, d in events if e == "tokens").startswith("plain answer")
    finally:
        await _cleanup(corpus)
