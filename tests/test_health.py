from unittest.mock import patch

import pytest
import uuid

from app.api.routes import health as health_module


@pytest.mark.parametrize(
    ("db", "redis", "graph", "expected"),
    [
        (True, True, True, "ok"),
        (True, True, False, "degraded"),
        (False, False, False, "degraded"),
    ],
)
async def test_healthz(client, db, redis, graph, expected):
    with (
        patch.object(health_module, "check_db", return_value=db),
        patch.object(health_module, "check_redis", return_value=redis),
        patch.object(health_module, "check_graph", return_value=graph),
    ):
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == expected
    assert set(body["services"]) == {"db", "redis", "graph"}


async def test_healthz_live(client):
    """Against real infra: /healthz must at least return 200."""
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] in {"ok", "degraded"}


async def test_healthz_includes_corpus_versions(client, require_db):
    from sqlalchemy import delete

    from app.db.models import CorpusVersion
    from app.db.session import async_session_factory
    from app.db.versions import bump_corpus_version

    corpus = f"healthz-{uuid.uuid4().hex[:6]}"
    async with async_session_factory() as s:
        await bump_corpus_version(s, corpus)

    resp = await client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert "corpus_versions" in body
    assert isinstance(body["corpus_versions"], dict)
    assert body["corpus_versions"].get(corpus) == 1

    async with async_session_factory() as s:
        await s.execute(delete(CorpusVersion).where(CorpusVersion.corpus == corpus))
        await s.commit()
