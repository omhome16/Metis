"""Runtime settings — the self-organizing control panel (P8).

Env defaults (`app/core/config.py`) merged with Postgres overrides
(`app_settings`); the frontend Settings view tunes graph behavior here:
extraction tier (t1/t2/t3), auto-reorg on/off + policy, debounce window.
"""

from typing import Any

from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.core.runtime_settings import get_all_settings, set_settings

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
async def get_settings() -> dict:
    """Merged settings + read-only provider status (T3 needs a real LLM route)."""
    return {
        "settings": await get_all_settings(),
        "providers": {
            "groq": bool(settings.groq_api_key),
            "gemini": bool(settings.gemini_api_key),
            "ollama": bool(settings.ollama_model),
        },
    }


@router.put("")
async def put_settings(payload: dict[str, Any]) -> dict:
    """Validate + persist overrides; returns the merged settings."""
    body = payload.get("settings") if isinstance(payload, dict) else None
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail='expected {"settings": {...}}')
    try:
        return {"settings": await set_settings(body)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
