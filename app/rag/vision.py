"""Image understanding: Gemini vision → caption + tags (blueprint §10)."""

import json

from app.core.logging import get_logger
from app.gateway.gateway import LLMGateway

logger = get_logger(__name__)

VISION_PROMPT = (
    "Describe this image for a knowledge library. Return ONLY JSON:\n"
    '{"caption": "one clear sentence describing the image", "tags": ["up to 6 short tags"]}'
)

_MIME_BY_EXT = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
}


def mime_for_file(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return _MIME_BY_EXT.get(ext, "image/png")


def parse_image_description(text: str) -> dict:
    """Parse the model's JSON reply; fall back to treating the whole text as the caption."""
    try:
        data = json.loads(text)
        caption = str(data.get("caption", "")).strip()[:500]
        tags = [str(t).strip()[:64] for t in data.get("tags", []) if str(t).strip()][:10]
        return {"caption": caption or text[:500], "tags": tags}
    except Exception:  # noqa: BLE001
        return {"caption": text.strip()[:500], "tags": []}


async def describe_image(gateway: LLMGateway, image_b64: str, mime: str = "image/png") -> dict:
    try:
        text = await gateway.describe_image(image_b64, VISION_PROMPT, mime)
    except Exception as exc:  # noqa: BLE001 — vision is best-effort
        logger.warning("vision failed: %s", exc)
        return {"caption": "", "tags": []}
    return parse_image_description(text)
