"""Eval harness endpoints: run a dataset + config, list past reports."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EvalRun
from app.db.session import get_session
from app.evals.runner import run_eval
from app.gateway.gateway import get_gateway

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
