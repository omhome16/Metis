import json
import uuid
from unittest.mock import patch

from sqlalchemy import delete, select

from app.db.models import Chunk, Document
from app.db.session import async_session_factory
from app.rag.embeddings import get_embedder
from app.rag.retrieval import store_chunks


class FakeGateway:
    """Deterministic streaming gateway — no network."""

    async def chat_stream(self, task, messages, temperature=0.7, max_tokens=None):
        for token in ["The ", "answer ", "is ", "four. "]:
            yield token


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


async def test_ask_streams_full_event_sequence(client, require_db):
    corpus = f"test-{uuid.uuid4().hex[:8]}"
    await _seed(corpus)

    with patch("app.api.routes.ask.get_gateway", return_value=FakeGateway()):
        resp = await client.post("/api/v1/ask", json={"question": "Who wrote The Art of War?", "corpus": corpus})
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")

    events = _parse_sse(resp.text)
    names = [e for e, _ in events]
    assert names[0] == "sources"
    assert names[-1] == "done"
    assert "citations" in names
    assert "tokens" in names

    sources = events[0][1]
    assert len(sources["chunks"]) >= 1
    assert sources["chunks"][0]["doc"] == "The Art of War"

    citations = dict(events)["citations"]
    assert citations["citations"][0]["n"] == 1
    assert citations["citations"][0]["chunk_id"]

    done = dict(events)["done"]
    assert done["answer_id"]
    assert "usage" in done
    assert "cost_usd" in done

    await _cleanup(corpus)


async def test_ask_empty_corpus_graceful(client, require_db):
    corpus = f"test-empty-{uuid.uuid4().hex[:8]}"
    with patch("app.api.routes.ask.get_gateway", return_value=FakeGateway()):
        resp = await client.post("/api/v1/ask", json={"question": "anything?", "corpus": corpus})
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    assert events[0][0] == "sources"
    assert events[0][1]["chunks"] == []
    assert events[-1][0] == "done"
