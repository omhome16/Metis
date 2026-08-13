"""Health/liveness endpoint with readiness checks for Postgres, Redis, and Neo4j."""

import redis.asyncio as aioredis
from fastapi import APIRouter
from neo4j import AsyncGraphDatabase
from sqlalchemy import text

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import engine

logger = get_logger(__name__)
router = APIRouter(tags=["health"])


async def check_db() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # pragma: no cover - infra dependent
        logger.warning("db health check failed: %s", exc)
        return False


async def check_redis() -> bool:
    try:
        client = aioredis.from_url(settings.redis_url, socket_connect_timeout=2)
        await client.ping()
        await client.aclose()
        return True
    except Exception as exc:  # pragma: no cover - infra dependent
        logger.warning("redis health check failed: %s", exc)
        return False


async def check_graph() -> bool:
    try:
        driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        )
        await driver.verify_connectivity()
        await driver.close()
        return True
    except Exception as exc:  # pragma: no cover - infra dependent
        logger.warning("neo4j health check failed: %s", exc)
        return False


@router.get("/healthz")
async def healthz() -> dict:
    db, redis_ok, graph = await check_db(), await check_redis(), await check_graph()
    services = {
        "db": "up" if db else "down",
        "redis": "up" if redis_ok else "down",
        "graph": "up" if graph else "down",
    }
    corpus_versions: dict[str, int] = {}
    if db:
        try:
            async with engine.connect() as conn:
                rows = (await conn.execute(text("SELECT corpus, version FROM corpus_versions"))).all()
            corpus_versions = {corpus: version for corpus, version in rows}
        except Exception as exc:  # pragma: no cover - infra dependent
            logger.warning("corpus_versions health read failed: %s", exc)
    return {
        "status": "ok" if db and redis_ok and graph else "degraded",
        "services": services,
        "corpus_versions": corpus_versions,
    }
