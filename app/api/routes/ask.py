"""`POST /api/v1/ask` — SSE stream: sources → thinking → tokens → citations → contradiction → done.

Semantic cache: identical/similar questions replay the cached answer (M7).
Conversation history: when `conversation_id` is given, prior turns are fed back
into the pipeline and this exchange is persisted server-side.
"""

import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from app.api.routes.conversations import append_message
from app.cache import cache_evict_question, cache_lookup, cache_store
from app.core.logging import get_logger
from app.core.tracing import flush_tracer, get_tracer, trace_span
from app.db.models import Conversation, Feedback, Message
from app.db.session import get_session
from app.db.versions import get_corpus_version
from app.gateway.gateway import get_gateway
from app.rag.embeddings import get_embedder
from app.rag.pipeline import ask_events
from app.rag.router import route_question
from app.schemas.api import FeedbackOut, FeedbackRequest

logger = get_logger(__name__)
router = APIRouter(tags=["ask"])


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    corpus: str | None = Field(None, max_length=128)
    image: str | None = Field(
        None, max_length=15_000_000
    )  # ~11MB base64 data URL — multimodal (M4)
    conversation_id: str | None = Field(None, max_length=64)
    stream: bool = True
    options: dict = Field(default_factory=dict)


async def _cached_events(entry: dict) -> AsyncIterator[ServerSentEvent]:
    """Replay a cached answer as the same SSE event contract."""
    yield ServerSentEvent(event="sources", data=json.dumps(entry.get("sources") or {"chunks": []}))
    answer = entry.get("answer", "")
    for token in answer.split(" "):
        yield ServerSentEvent(event="tokens", data=json.dumps({"text": token + " "}))
    yield ServerSentEvent(
        event="citations", data=json.dumps(entry.get("citations") or {"citations": []})
    )
    done = dict(entry.get("done") or {})
    done["cached"] = True
    yield ServerSentEvent(event="done", data=json.dumps(done))


@router.post("/ask")
async def ask(request: AskRequest, session: AsyncSession = Depends(get_session)):
    gateway = get_gateway()
    tracer = get_tracer()

    lane = await route_question(
        request.question,
        gateway,
        image=bool(request.image),
        mode=(request.options or {}).get("mode"),
    )

    # fast lane: greetings / thanks / trivial — direct chat, zero DB, no cache.
    if lane == "fast":

        async def fast_stream() -> AsyncIterator[ServerSentEvent]:
            async for event, data in ask_events(
                session, gateway, request.question, None, lane="fast"
            ):
                yield ServerSentEvent(event=event, data=json.dumps(data))

        return EventSourceResponse(fast_stream())

    corpus = request.corpus or "default"
    corpus_version = await get_corpus_version(session, corpus)

    # Load prior turns for this conversation (last 20) so follow-ups have context.
    history: list[dict] = []
    conversation = None
    if request.conversation_id:
        conversation = await session.get(Conversation, request.conversation_id)
        if conversation is not None:
            rows = (
                (
                    await session.execute(
                        select(Message)
                        .where(Message.conversation_id == request.conversation_id)
                        .order_by(Message.created_at.desc())
                        .limit(20)
                    )
                )
                .scalars()
                .all()
            )
            history = [{"role": m.role, "content": m.content} for m in reversed(rows)]

    async def event_stream():
        cached_flag = False
        cached: dict | None = None
        collected: dict = {
            "sources": None,
            "citations": None,
            "done": None,
            "answer_parts": [],
            "error": None,
        }
        if not request.image:
            try:
                query_vec = await get_embedder().embed_query(request.question)
                cached = await cache_lookup(
                    session,
                    query_vec,
                    request.corpus,
                    current_version=corpus_version,
                    question=request.question,
                )
                if cached:
                    logger.info("cache HIT for corpus=%s", request.corpus)
                    cached_flag = True
                    async for ev in _cached_events(cached):
                        yield ev
            except Exception as exc:  # noqa: BLE001 — cache must never break ask
                logger.warning("cache lookup error: %s", exc)

        if not cached_flag:
            with trace_span(
                tracer,
                "ask",
                input={"question": request.question, "corpus": request.corpus, "lane": lane},
            ) as span:
                async for event, data in ask_events(
                    session,
                    gateway,
                    request.question,
                    request.corpus,
                    image=request.image,
                    history=history,
                    lane=lane,
                ):
                    if event == "sources":
                        collected["sources"] = data
                    elif event == "citations":
                        collected["citations"] = data
                    elif event == "done":
                        collected["done"] = data
                    elif event == "tokens":
                        if data["text"].startswith("[generation failed") or data["text"].startswith(
                            "[agent error"
                        ):
                            collected["error"] = data["text"]
                        collected["answer_parts"].append(data["text"])
                    yield ServerSentEvent(event=event, data=json.dumps(data))
                if span is not None:
                    try:
                        span.update(output={"done": collected["done"]})
                    except Exception:  # noqa: BLE001
                        pass

            if not request.image and collected["done"]:
                try:
                    await cache_store(
                        session,
                        request.question,
                        request.corpus,
                        {
                            "sources": collected["sources"],
                            "citations": collected["citations"],
                            "done": collected["done"],
                            "answer": "".join(collected["answer_parts"]),
                        },
                        current_version=corpus_version,
                    )
                except Exception:  # noqa: BLE001
                    pass

        # Persist the exchange (and auto-create a conversation on first message).
        try:
            conv_id = (
                (request.conversation_id or conversation.id)
                if (request.conversation_id or conversation)
                else None
            )
            if not conv_id and request.corpus:
                conv = Conversation(
                    vault_name=request.corpus, title=request.question[:80] or "New conversation"
                )
                session.add(conv)
                await session.commit()
                conv_id = conv.id
            if conv_id:
                answer = (
                    "".join(collected.get("answer_parts", []))
                    if not cached_flag
                    else (cached or {}).get("answer", "")
                )
                done = collected.get("done") if not cached_flag else (cached or {}).get("done", {})
                sources = (
                    collected.get("sources")
                    if not cached_flag
                    else (cached or {}).get("sources", {"chunks": []})
                )
                citations = (
                    collected.get("citations")
                    if not cached_flag
                    else (cached or {}).get("citations", {})
                )
                await append_message(session, conv_id, "user", request.question)
                assistant_msg = await append_message(
                    session,
                    conv_id,
                    "assistant",
                    answer,
                    sources=sources,
                    citations=citations,
                    usage=done.get("usage"),
                    error=collected.get("error"),
                    cached=cached_flag,
                )
                # surface the conversation id + message id so the frontend can
                # attach follow-ups and feedback thumbs
                final_done = dict(done)
                final_done["conversation_id"] = conv_id
                final_done["message_id"] = assistant_msg.id
                final_done["cached"] = cached_flag
                yield ServerSentEvent(event="done", data=json.dumps(final_done))
        except Exception as exc:  # noqa: BLE001 — history must never break the answer
            logger.warning("conversation persistence skipped: %s", exc)
        flush_tracer(tracer)

    return EventSourceResponse(
        event_stream(), headers={"X-Metis-Corpus-Version": str(corpus_version)}
    )


async def _feedback_context(session: AsyncSession, msg: Message) -> tuple[str | None, str | None]:
    """Corpus (from the conversation) and the question that preceded this message."""
    conv = await session.get(Conversation, msg.conversation_id)
    corpus = conv.vault_name if conv else None
    if msg.role == "user":
        return corpus, msg.content
    prior = (
        (
            await session.execute(
                select(Message)
                .where(
                    Message.conversation_id == msg.conversation_id,
                    Message.role == "user",
                    Message.id != msg.id,
                    Message.created_at <= msg.created_at,
                )
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(1)
            )
        )
        .scalars()
        .all()
    )
    return corpus, prior[0].content if prior else None


@router.post("/ask/{message_id}/feedback", response_model=FeedbackOut)
async def submit_feedback(
    message_id: str,
    payload: FeedbackRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FeedbackOut:
    """Thumbs on an answer (P6). Negative feedback evicts matching cache entries."""
    msg = await session.get(Message, message_id)
    if msg is None:
        raise HTTPException(status_code=404, detail="message not found")
    existing = (
        (await session.execute(select(Feedback).where(Feedback.message_id == message_id)))
        .scalars()
        .one_or_none()
    )
    if existing is not None:
        existing.rating = payload.rating
        existing.note = payload.note
        row = existing
    else:
        row = Feedback(message_id=message_id, rating=payload.rating, note=payload.note)
        session.add(row)
    await session.commit()

    if payload.rating < 0:
        corpus, question = await _feedback_context(session, msg)
        if question:
            deleted = await cache_evict_question(session, question, corpus)
            logger.info(
                "feedback thumbs-down on message %s — evicted %d cache entries",
                message_id,
                deleted,
            )
    return FeedbackOut(message_id=message_id, rating=payload.rating, note=payload.note)
