from unittest.mock import patch

import pytest

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
