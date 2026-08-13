"""Corpus version helpers: bump on ingest completion and vault delete."""

import uuid

from sqlalchemy import delete, select

from app.db.models import CorpusVersion, Document, Vault
from app.db.session import async_session_factory
from app.db.versions import bump_corpus_version, get_corpus_version


async def test_bump_and_get(client, require_db):
    async with async_session_factory() as s:
        assert await get_corpus_version(s, "vtest") == 0
        assert await bump_corpus_version(s, "vtest") == 1
        assert await bump_corpus_version(s, "vtest") == 2
        assert await get_corpus_version(s, "vtest") == 2
        rows = (
            await s.execute(select(CorpusVersion).where(CorpusVersion.corpus == "vtest"))
        ).scalars().all()
        assert len(rows) == 1
        await s.execute(delete(CorpusVersion).where(CorpusVersion.corpus == "vtest"))
        await s.commit()


async def test_vault_delete_bumps_version(client, require_db):
    name = f"vdel-{uuid.uuid4().hex[:8]}"
    async with async_session_factory() as s:
        s.add(Vault(id=str(uuid.uuid4()), name=name))
        s.add(
            Document(
                id=str(uuid.uuid4()),
                title="t",
                corpus=name,
                format="txt",
                content_hash=uuid.uuid4().hex,
            )
        )
        await s.commit()

    r = await client.delete(f"/api/v1/vaults/{name}")
    assert r.status_code == 200
    async with async_session_factory() as s:
        version = (await s.execute(select(CorpusVersion.version).where(CorpusVersion.corpus == name))).scalar()
    assert version is not None and version >= 1