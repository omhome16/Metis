"""`POST /api/v1/ask` — SSE stream: sources → tokens → citations → contradiction → done.

Semantic cache: identical/similar questions replay the cached answer (M7).
"""

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from app.cache import cache_lookup, cache_store, get_redis
from app.core.tracing import flush_tracer, get_tracer, trace_span
from app.db.session import get_session
from app.gateway.gateway import get_gateway
from app.core.logging import get_logger
from app.rag.embeddings import get_embedder
from app.rag.pipeline import ask_events

logger = get_logger(__name__)
router = APIRouter(tags=["ask"])


class AskRequest(BaseModel):
    question: str
    corpus: str | None = None
    image: str | None = None  # base64 data URL — multimodal (M4)
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

    async def event_stream():
        if not request.image:
            try:
                redis = await get_redis()
                query_vec = await get_embedder().embed_query(request.question)
                cached = await cache_lookup(redis, query_vec, request.corpus)
                await redis.aclose()
                if cached:
                    logger.info("cache HIT for corpus=%s question=%r", request.corpus, request.question)
                    async for ev in _cached_events(cached):
                        yield ev
                    return
                logger.info("cache MISS for corpus=%s question=%r", request.corpus, request.question)
            except Exception as exc:  # noqa: BLE001 — cache must never break ask
                logger.warning("cache lookup error: %s", exc)

        collected: dict = {"sources": None, "citations": None, "done": None, "answer_parts": []}
        with trace_span(tracer, "ask", input={"question": request.question, "corpus": request.corpus}) as span:
            async for event, data in ask_events(session, gateway, request.question, request.corpus, image=request.image):
                if event == "sources":
                    collected["sources"] = data
                elif event == "citations":
                    collected["citations"] = data
                elif event == "done":
                    collected["done"] = data
                elif event == "tokens":
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
                await redis.aclose()
            except Exception:  # noqa: BLE001
                pass
        flush_tracer(tracer)

    return EventSourceResponse(event_stream())
