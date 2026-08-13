"""Eval harness endpoints: run a dataset + config, list past reports, review feedback."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, EvalRun, Feedback, Message
from app.db.session import get_session
from app.evals.runner import run_eval
from app.gateway.gateway import get_gateway
from app.schemas.api import FeedbackLogRow

router = APIRouter(prefix="/evals", tags=["evals"])


class EvalRunRequest(BaseModel):
    dataset_id: str = Field(..., min_length=1)
    config: dict = Field(default_factory=dict)


@router.post("/run")
async def run(request: EvalRunRequest, session: AsyncSession = Depends(get_session)) -> dict:
    try:
        return await run_eval(session, get_gateway(), request.dataset_id, request.config)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/reports")
async def reports(limit: int = 10, session: AsyncSession = Depends(get_session)) -> list[dict]:
    stmt = select(EvalRun).order_by(EvalRun.created_at.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {"run_id": r.id, "config": r.config, "metrics": r.metrics, "created_at": r.created_at.isoformat()}
        for r in rows
    ]


@router.get("/feedback")
async def feedback_log(
    limit: int = Query(50, ge=1, le=500),
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> dict:
    """Feedback surfaced for eval tooling (P6): totals + recent rows with context."""
    counts = dict(
        (
            await session.execute(
                select(Feedback.rating, func.count(Feedback.id)).group_by(Feedback.rating)
            )
        ).all()
    )
    rows = (
        (
            await session.execute(
                select(Feedback, Message, Conversation)
                .join(Message, Message.id == Feedback.message_id)
                .join(Conversation, Conversation.id == Message.conversation_id)
                .order_by(Feedback.created_at.desc())
                .limit(limit)
            )
        )
        .all()
    )
    recent = [
        FeedbackLogRow(
            id=fb.id,
            message_id=fb.message_id,
            rating=fb.rating,
            note=fb.note,
            question=msg.content if msg.role == "user" else None,
            answer=msg.content if msg.role == "assistant" else "",
            corpus=conv.vault_name,
            created_at=fb.created_at,
        )
        for fb, msg, conv in rows
    ]
    return {
        "total": sum(counts.values()),
        "up": counts.get(1, 0),
        "down": counts.get(-1, 0),
        "recent": recent,
    }
