"""`POST /api/v1/ask` — SSE stream: sources → tokens → citations → done."""

import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from app.db.session import get_session
from app.gateway.gateway import get_gateway
from app.rag.pipeline import ask_events

router = APIRouter(tags=["ask"])


class AskRequest(BaseModel):
    question: str
    corpus: str | None = None
    image: str | None = None  # base64 data URL — enabled with multimodal (M4)
    stream: bool = True
    options: dict = Field(default_factory=dict)


@router.post("/ask")
async def ask(request: AskRequest, session: AsyncSession = Depends(get_session)):
    gateway = get_gateway()

    async def event_stream():
        async for event, data in ask_events(session, gateway, request.question, request.corpus):
            yield ServerSentEvent(event=event, data=json.dumps(data))

    return EventSourceResponse(event_stream())
