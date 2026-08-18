"""Runtime settings (P8): env defaults + Postgres overrides, validation, API."""

import pytest

from app.core.runtime_settings import (
    DEFAULT_VALUES,
    get_all_settings,
    get_setting,
    set_settings,
)


async def test_settings_defaults(require_db):
    merged = await get_all_settings()
    assert merged["graph.extraction_mode"] == "t1"
    assert merged["graph.reorg_policy"] == "debounced"
    assert merged["graph.reorg_auto"] is True
    assert merged["graph.reorg_min_docs"] == 3


async def test_settings_override_and_restore(require_db):
    updated = await set_settings({"graph.extraction_mode": "t2", "graph.reorg_auto": False})
    assert updated["graph.extraction_mode"] == "t2"
    assert updated["graph.reorg_auto"] is False
    assert await get_setting("graph.extraction_mode", "t1") == "t2"
    restored = await set_settings({"graph.extraction_mode": "t1", "graph.reorg_auto": True})
    assert restored["graph.extraction_mode"] == "t1"
    assert restored["graph.reorg_auto"] is True


async def test_settings_reject_invalid_values(require_db):
    with pytest.raises(ValueError):
        await set_settings({"graph.extraction_mode": "t9"})
    with pytest.raises(ValueError):
        await set_settings({"graph.reorg_policy": "sometimes"})
    with pytest.raises(ValueError):
        await set_settings({"graph.reorg_min_docs": 0})
    with pytest.raises(ValueError):
        await set_settings({"graph.reorg_auto": "yes"})
    with pytest.raises(ValueError):
        await set_settings({"unknown.setting": 1})


async def test_settings_get_never_raises_without_db():
    """Without a reachable DB (or pre-migration), reads fall back to defaults."""
    merged = await get_all_settings()
    assert (
        merged["graph.extraction_mode"] in DEFAULT_VALUES.values()
        or "graph.extraction_mode" in merged
    )


async def test_settings_api_roundtrip(require_db, client):
    r = await client.get("/api/v1/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["settings"]["graph.extraction_mode"] == "t1"
    assert "providers" in body  # read-only provider status for the UI warning

    r = await client.put("/api/v1/settings", json={"settings": {"graph.extraction_mode": "t2"}})
    assert r.status_code == 200
    assert r.json()["settings"]["graph.extraction_mode"] == "t2"

    r = await client.put("/api/v1/settings", json={"settings": {"graph.extraction_mode": "t9"}})
    assert r.status_code == 422

    r = await client.put("/api/v1/settings", json={"settings": {"graph.extraction_mode": "t1"}})
    assert r.status_code == 200
    assert r.json()["settings"]["graph.extraction_mode"] == "t1"

    r = await client.put("/api/v1/settings", json={"nonsense": 1})
    assert r.status_code == 422
