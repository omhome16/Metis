"""Shared pytest fixtures. Env is configured BEFORE importing the app.

All tests run through httpx AsyncClient + ASGITransport so the app and the tests
share a single event loop — avoids SQLAlchemy pool cross-loop errors.
"""

import os

os.environ.setdefault("METIS_ENV", "test")
os.environ.setdefault("METIS_EMBED_MODEL", "mock")  # never download weights in tests
os.environ.setdefault("METIS_RERANK_MODEL", "mock")

import asyncio  # noqa: E402

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
async def _dispose_engine():
    """Close pooled connections after every test so no cross-loop reuse happens."""
    yield
    await engine.dispose()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _db_reachable() -> bool:
    import asyncpg

    async def _probe() -> bool:
        try:
            dsn = settings.db_url.replace("postgresql+asyncpg://", "postgresql://")
            conn = await asyncpg.connect(dsn, timeout=3)
            await conn.close()
            return True
        except Exception:
            return False

    try:
        return asyncio.run(_probe())
    except Exception:
        return False


@pytest.fixture
def require_db():
    """Skip a test when Postgres is not reachable (infra-dependent tests)."""
    if not _db_reachable():
        pytest.skip("Postgres not reachable — start with: docker compose up -d db")
    return True


@pytest.fixture
async def require_graph():
    """Skip a test when Neo4j is not reachable; wipe the graph around the test."""
    from app.graph.store import get_graph_store

    store = get_graph_store()
    if not await store.ping():
        pytest.skip("Neo4j not reachable — start with: docker compose up -d graph")
    async with store._driver.session() as session:  # wipe pre-test for isolation
        await session.run("MATCH (n) DETACH DELETE n")
    yield store
    async with store._driver.session() as session:  # wipe post-test
        await session.run("MATCH (n) DETACH DELETE n")
