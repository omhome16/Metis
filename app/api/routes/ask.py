"""`POST /api/v1/ask` — SSE stream: sources → thinking → tokens → citations → contradiction → done.

Semantic cache: identical/similar questions replay the cached answer (M7).
Conversation history: when `conversation_id` is given, prior turns are fed back
into the pipeline and this exchange is persisted server-side.
"""

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from app.api.routes.conversations import append_message
from app.cache import cache_lookup, cache_store, get_redis
from app.core.tracing import flush_tracer, get_tracer, trace_span
from app.db.models import Conversation, Message
from app.db.session import get_session
from app.gateway.gateway import get_gateway
from app.core.logging import get_logger
from app.rag.embeddings import get_embedder
from app.rag.pipeline import ask_events

logger = get_logger(__name__)
router = APIRouter(tags=["ask"])


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    corpus: str | None = Field(None, max_length=128)
    image: str | None = Field(None, max_length=15_000_000)  # ~11MB base64 data URL — multimodal (M4)
    conversation_id: str | None = Field(None, max_length=64)
    stream: bool = True
    options: dict = Field(default_factory=dict)


async def _cached_events(entry: dict) -> AsyncIterator[ServerSentEvent]:
    """Replay a cached answer as the same SSE event contract."""
    yield ServerSentEvent(event="sources", data=json.dumps(entry.get("sources") or {"chunks": []}))
    answer = entry.get("answer", "")
    for token in answer.split(" "):
        yield ServerSentEvent(event="tokens", data=json.dumps({"text": token + " "}))
    yield ServerSentEvent(event="citations", data=json.dumps(entry.get("citations") or {"citations": []}))
    done = dict(entry.get("done") or {})
    done["cached"] = True
    yield ServerSentEvent(event="done", data=json.dumps(done))


@router.post("/ask")
async def ask(request: AskRequest, session: AsyncSession = Depends(get_session)):
    gateway = get_gateway()
    tracer = get_tracer()

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
        collected: dict = {"sources": None, "citations": None, "done": None, "answer_parts": [], "error": None}
        if not request.image:
            try:
                redis = await get_redis()
                try:
                    query_vec = await get_embedder().embed_query(request.question)
                    cached = await cache_lookup(redis, query_vec, request.corpus)
                finally:
                    await redis.aclose()  # never leak the client on lookup errors
                if cached:
                    logger.info("cache HIT for corpus=%s", request.corpus)
                    cached_flag = True
                    async for ev in _cached_events(cached):
                        yield ev
            except Exception as exc:  # noqa: BLE001 — cache must never break ask
                logger.warning("cache lookup error: %s", exc)

        if not cached_flag:
            with trace_span(tracer, "ask", input={"question": request.question, "corpus": request.corpus}) as span:
                async for event, data in ask_events(
                    session, gateway, request.question, request.corpus, image=request.image, history=history
                ):
                    if event == "sources":
                        collected["sources"] = data
                    elif event == "citations":
                        collected["citations"] = data
                    elif event == "done":
                        collected["done"] = data
                    elif event == "tokens":
                        if data["text"].startswith("[generation failed") or data["text"].startswith("[agent error"):
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
                    redis = await get_redis()
                    try:
                        await cache_store(
                            redis,
                            request.question,
                            request.corpus,
                            {
                                "sources": collected["sources"],
                                "citations": collected["citations"],
                                "done": collected["done"],
                                "answer": "".join(collected["answer_parts"]),
                            },
                        )
                    finally:
                        await redis.aclose()
                except Exception:  # noqa: BLE001
                    pass

        # Persist the exchange (and auto-create a conversation on first message).
        try:
            conv_id = (request.conversation_id or conversation.id) if (request.conversation_id or conversation) else None
            if not conv_id and request.corpus:
                conv = Conversation(vault_name=request.corpus, title=request.question[:80] or "New conversation")
                session.add(conv)
                await session.commit()
                conv_id = conv.id
            if conv_id:
                answer = "".join(collected.get("answer_parts", [])) if not cached_flag else (cached or {}).get("answer", "")
                done = collected.get("done") if not cached_flag else (cached or {}).get("done", {})
                sources = collected.get("sources") if not cached_flag else (cached or {}).get("sources", {"chunks": []})
                citations = collected.get("citations") if not cached_flag else (cached or {}).get("citations", {})
                await append_message(session, conv_id, "user", request.question)
                await append_message(
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
                # surface the conversation id so the frontend can attach follow-ups
                final_done = dict(done)
                final_done["conversation_id"] = conv_id
                final_done["cached"] = cached_flag
                yield ServerSentEvent(event="done", data=json.dumps(final_done))
        except Exception as exc:  # noqa: BLE001 — history must never break the answer
            logger.warning("conversation persistence skipped: %s", exc)
        flush_tracer(tracer)

    return EventSourceResponse(event_stream())
