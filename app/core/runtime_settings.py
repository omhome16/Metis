"""Runtime settings: env defaults + Postgres overrides (self-organizing control panel).

Every key has an env-provided default (`app/core/config.py`); values stored in the
`app_settings` table override it at runtime via `GET/PUT /api/v1/settings` — the
frontend Settings view is where the graph behavior is tuned (extraction tier,
auto-reorg policy, debounce window). Reads never raise: a missing table
(pre-migration) or a failed query falls back to defaults, so ingest/ask keep
working even when the settings table doesn't exist yet.
"""

import json
from typing import Any

from sqlalchemy import text

from app.core.config import settings as env_settings
from app.core.logging import get_logger
from app.db.session import async_session_factory

logger = get_logger(__name__)

DEFAULT_VALUES: dict[str, Any] = {
    "graph.extraction_mode": env_settings.graph_extraction_mode,
    "graph.extract_windows": env_settings.graph_extract_windows,
    "graph.reorg_auto": env_settings.graph_reorg_auto,
    "graph.reorg_policy": env_settings.graph_reorg_policy,
    "graph.reorg_min_docs": env_settings.graph_reorg_min_docs,
}

_CHOICES: dict[str, tuple[str, ...]] = {
    "graph.extraction_mode": ("t1", "t2", "t3"),
    "graph.reorg_policy": ("batch", "debounced", "nightly"),
}
_BOOLS = {"graph.reorg_auto"}
_INTS: dict[str, tuple[int, int]] = {
    "graph.extract_windows": (1, 8),
    "graph.reorg_min_docs": (1, 100),
}


async def get_setting(key: str, default: Any = None) -> Any:
    """Read one stored override; fall back to `default` (usually the env default). Never raises."""
    row = await _read_row(key)
    return default if row is None else row


async def _read_row(key: str) -> Any | None:
    try:
        async with async_session_factory() as session:
            row = (
                await session.execute(
                    text("SELECT value FROM app_settings WHERE key = :k"), {"k": key}
                )
            ).first()
        return None if row is None else json.loads(row[0])
    except Exception as exc:  # noqa: BLE001 — settings must never break ingest/ask
        logger.warning("settings read failed for %s: %s", key, exc)
        return None


async def get_all_settings() -> dict:
    """Env defaults merged with stored overrides. Never raises."""
    merged = dict(DEFAULT_VALUES)
    try:
        async with async_session_factory() as session:
            rows = (await session.execute(text("SELECT key, value FROM app_settings"))).all()
        for key, value in rows:
            merged[key] = json.loads(value)
    except Exception as exc:  # noqa: BLE001
        logger.warning("settings list failed: %s", exc)
    return merged


async def set_settings(payload: dict[str, Any]) -> dict:
    """Validate + upsert overrides; raises ValueError with per-key errors."""
    updates: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for key, value in payload.items():
        if key not in DEFAULT_VALUES:
            errors[key] = "unknown setting"
            continue
        try:
            updates[key] = _validate(key, value)
        except ValueError as exc:
            errors[key] = str(exc)
    if errors:
        raise ValueError(errors)
    if not updates:
        return await get_all_settings()
    try:
        async with async_session_factory() as session:
            for key, value in updates.items():
                await session.execute(
                    text(
                        "INSERT INTO app_settings (key, value, updated_at) "
                        "VALUES (:k, :v, now()) "
                        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"
                    ),
                    {"k": key, "v": json.dumps(value)},
                )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        raise ValueError({"db": f"could not persist settings: {exc}"}) from exc
    return await get_all_settings()


def _validate(key: str, value: Any) -> Any:
    if key in _BOOLS:
        if not isinstance(value, bool):
            raise ValueError("expected a boolean")
        return value
    if key in _CHOICES:
        if value not in _CHOICES[key]:
            raise ValueError(f"expected one of {', '.join(_CHOICES[key])}")
        return value
    if key in _INTS:
        lo, hi = _INTS[key]
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError("expected an integer")
        if not lo <= value <= hi:
            raise ValueError(f"expected an integer in [{lo}, {hi}]")
        return value
    raise ValueError("unsupported type")
