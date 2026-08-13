"""P3.2: best-effort LLM extraction of retrieval metadata from a query.

Turns "the 2024 policy on privacy, by Adams" into
`{"tags": [...], "date_from": "2024-01-01", "date_to": "2024-12-31", "author": "Adams"}`
which then filters both retrieval arms. Any failure or empty result → `{}`
(no filtering, never breaks ask).
"""

import json
import re

from app.core.logging import get_logger
from app.gateway.gateway import LLMGateway

logger = get_logger(__name__)

METADATA_PROMPT = (
    "Extract retrieval scoping hints from the user's question.\n"
    "Return JSON with this exact shape (no markdown):\n"
    '{"tags": ["..."], "date_from": "YYYY-MM-DD or null",'
    ' "date_to": "YYYY-MM-DD or null", "author": "name or null"}\n'
    "tags: domain terms that would appear as document tags (max 5, lowercase).\n"
    "date_from/date_to: a date range implied by the question ('the 2024 policy' -> "
    'date_from "2024-01-01", date_to "2024-12-31"); null when no range is implied.\n'
    "author: a named author/creator if the question asks for one; null otherwise.\n"
    "Use null for anything not implied — do not invent constraints."
)

_FALLBACK_YEAR = re.compile(r"\b(1[89]\d\d|20\d\d)\b")


def _fallback_metadata(question: str) -> dict:
    """Regex-only hints when the LLM is unavailable: a bare year → that year."""
    match = _FALLBACK_YEAR.search(question or "")
    if not match:
        return {}
    year = match.group(1)
    return {"date_from": f"{year}-01-01", "date_to": f"{year}-12-31"}


def _clean(question: str, raw: dict) -> dict:
    out: dict = {}
    tags = [
        str(t).strip().lower()[:64]
        for t in (raw.get("tags") or [])
        if isinstance(t, str) and t.strip()
    ]
    if tags:
        out["tags"] = tags[:5]
    for key in ("date_from", "date_to"):
        value = raw.get(key)
        if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
            out[key] = value.strip()
    author = str(raw.get("author") or "").strip()
    if author:
        out["author"] = author[:128]
    return out


async def extract_query_metadata(gateway: LLMGateway, question: str) -> dict:
    """LLM (best-effort) → scoping dict; `{}` on any failure or empty result."""
    try:
        result = await gateway.structured(
            "query_metadata",
            [
                {"role": "system", "content": METADATA_PROMPT},
                {"role": "user", "content": question[:2000]},
            ],
            {},
        )
        if isinstance(result, dict) and result:
            cleaned = _clean(question, result)
            if cleaned:
                return cleaned
    except Exception as exc:  # noqa: BLE001
        logger.warning("query metadata extraction failed: %s", exc)
    return _fallback_metadata(question)


def parse_metadata_json(text: str) -> dict:
    """Parse the LLM's raw JSON text (lenient: strips code fences)."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        stripped = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return {}
