"""Semantic cache statistics."""

from fastapi import APIRouter

from app.cache import cache_stats

router = APIRouter(prefix="/cache", tags=["cache"])


@router.get("/stats")
async def stats() -> dict:
    return await cache_stats()
