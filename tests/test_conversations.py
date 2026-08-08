"""Conversation history: CRUD endpoints + ask persistence with conversation_id."""

import json
import uuid
from unittest.mock import patch

from sqlalchemy import delete, select

from app.db.models import Conversation, Document
from app.db.session import async_session_factory


class FakeGateway:
    async def chat_stream(self, task, messages, temperature=0.7, max_tokens=None):
        for token in ["The ", "answer ", "is ", "four. "]:
            yield token


async def _seed(corpus: str) -> None:
    async with async_session_factory() as session:
        doc = Document(
            id=str(uuid.uuid4()),
            title="Test Doc",
            corpus=corpus,
            format="txt",
            content_hash=uuid.uuid4().hex,
            raw_text="Some retrievable content here.",
        )
        session.add(doc)
        await session.commit()


async def _cleanup(corpus: str) -> None:
    async with async_session_factory() as session:
        convs = (await session.execute(select(Conversation).where(Conversation.vault_name == corpus))).scalars().all()
        for c in convs:
            await session.delete(c)
        await session.execute(delete(Document).where(Document.corpus == corpus))
        await session.commit()


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


async def test_conversation_crud(client, require_db):
    corpus = f"test-conv-{uuid.uuid4().hex[:8]}"
    try:
        # create
        resp = await client.post(f"/api/v1/vaults/{corpus}/conversations", json={"title": "First chat"})
        assert resp.status_code == 201, resp.text
        conv = resp.json()
        assert conv["vault_name"] == corpus
        assert conv["title"] == "First chat"
        assert conv["message_count"] == 0

        # list
        lst = await client.get(f"/api/v1/vaults/{corpus}/conversations")
        assert lst.status_code == 200
        assert [c["id"] for c in lst.json()] == [conv["id"]]

        # detail (empty messages)
        det = await client.get(f"/api/v1/conversations/{conv['id']}")
        assert det.status_code == 200
        assert det.json()["messages"] == []

        # rename
        ren = await client.patch(f"/api/v1/conversations/{conv['id']}", json={"title": "Renamed"})
        assert ren.status_code == 200
        assert ren.json()["title"] == "Renamed"

        # 404s
        assert (await client.get("/api/v1/conversations/does-not-exist")).status_code == 404
        assert (await client.delete("/api/v1/conversations/does-not-exist")).status_code == 404

        # delete
        assert (await client.delete(f"/api/v1/conversations/{conv['id']}")).status_code == 200
        assert (await client.get(f"/api/v1/conversations/{conv['id']}")).status_code == 404
    finally:
        await _cleanup(corpus)


async def test_ask_persists_conversation(client, require_db):
    corpus = f"test-conv-ask-{uuid.uuid4().hex[:8]}"
    await _seed(corpus)
    try:
        with patch("app.api.routes.ask.get_gateway", return_value=FakeGateway()):
            resp = await client.post("/api/v1/ask", json={"question": "hello world?", "corpus": corpus})
        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        done = dict(events)["done"]
        assert done.get("conversation_id"), "ask should auto-create a conversation"

        conv_id = done["conversation_id"]
        det = await client.get(f"/api/v1/conversations/{conv_id}")
        assert det.status_code == 200
        messages = det.json()["messages"]
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[0]["content"] == "hello world?"
        assert messages[1]["content"].startswith("The answer is four.")
        assert messages[1]["sources"] is not None
        assert messages[1]["usage"] is not None

        # listing shows the conversation with 2 messages
        lst = await client.get(f"/api/v1/vaults/{corpus}/conversations")
        assert lst.json()[0]["message_count"] == 2

        # follow-up with conversation_id → history preserved, one exchange appended
        with patch("app.api.routes.ask.get_gateway", return_value=FakeGateway()):
            resp2 = await client.post(
                "/api/v1/ask", json={"question": "and then?", "corpus": corpus, "conversation_id": conv_id}
            )
        assert resp2.status_code == 200
        det2 = await client.get(f"/api/v1/conversations/{conv_id}")
        roles = [m["role"] for m in det2.json()["messages"]]
        assert roles == ["user", "assistant", "user", "assistant"]
    finally:
        await _cleanup(corpus)
