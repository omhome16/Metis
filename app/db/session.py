"""Async SQLAlchemy engine and session management."""

from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(settings.db_url, pool_pre_ping=True)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


@event.listens_for(engine.sync_engine, "connect")
def _set_hnsw_gucs(dbapi_connection, _record) -> None:  # pragma: no cover - infra dependent
    """Apply HNSW query-time GUCs to every new connection (pgvector 0.8+)."""
    cur = dbapi_connection.cursor()
    cur.execute(f"SET hnsw.ef_search = {int(settings.hnsw_ef_search)}")
    cur.execute(f"SET hnsw.iterative_scan = {settings.hnsw_iterative_scan}")
    cur.close()


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session
