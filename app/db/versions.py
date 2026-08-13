"""Monotonic per-corpus versioning (invalidates semantic caches / client caches)."""

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CorpusVersion


async def bump_corpus_version(session: AsyncSession, corpus: str) -> int:
    """Upsert-increment the version for a corpus; returns the new version."""
    stmt = (
        pg_insert(CorpusVersion)
        .values(corpus=corpus, version=1)
        .on_conflict_do_update(
            index_elements=[CorpusVersion.corpus],
            set_={"version": CorpusVersion.version + 1, "updated_at": func.now()},
        )
        .returning(CorpusVersion.version)
    )
    version = (await session.execute(stmt)).scalar_one()
    await session.commit()
    return version


async def get_corpus_version(session: AsyncSession, corpus: str) -> int:
    """Current version for a corpus; 0 when it has never been bumped."""
    version = (
        await session.execute(
            select(CorpusVersion.version).where(CorpusVersion.corpus == corpus)
        )
    ).scalar_one_or_none()
    return version or 0
