"""Semantic cache statistics."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import cache_stats
from app.db.session import get_session

router = APIRouter(prefix="/cache", tags=["cache"])


@router.get("/stats")
async def stats(session: Annotated[AsyncSession, Depends(get_session)]) -> dict:
    return await cache_stats(session)
