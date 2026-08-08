"""Entity/relationship extraction (blueprint §8.1 step 4).

Primary: Gemini structured output (via the gateway). Fallback: a local regex-based
extractor that works offline / in tests / when the LLM is unavailable.
"""

import re

from app.core.logging import get_logger
from app.gateway.gateway import LLMGateway

logger = get_logger(__name__)

ENTITY_TYPES = [
    "Person", "Place", "Concept", "Artwork", "Work", "Event", "Organization", "Technology",
]

EXTRACTION_PROMPT = (
    "Extract named entities and the relationships between them from the text below.\n"
    "Entity types: " + ", ".join(ENTITY_TYPES) + "\n"
    "Return JSON with this exact shape (no markdown):\n"
    '{"entities": [{"name": "...", "type": "..."}], "relations": [{"source": "...", "target": "...", "type": "RELATED_TO"}]}\n'
    "Include only entities that actually appear in the text. Max 40 entities."
)

_EXCLUDED = {"i", "the", "a", "an", "we", "you", "they", "it", "he", "she", "this", "that", "and", "or"}


def extract_entities_fallback(text: str, limit: int = 40) -> dict:
    """Local regex extraction: repeated capitalized phrases, most frequent first."""
    counts: dict[str, int] = {}
    for match in re.finditer(r"\b[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,3}\b", text):
        phrase = match.group().strip()
        if phrase.lower() in _EXCLUDED or len(phrase.split()) > 4:
            continue
        counts[phrase] = counts.get(phrase, 0) + 1
    entities = [
        {"name": name, "type": "Concept"}
        for name, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:limit]
    ]
    return {"entities": entities, "relations": []}


async def extract_entities(gateway: LLMGateway, text: str, max_chars: int = 8000) -> dict:
    """Extract entities + relations. Tries the LLM first, falls back to regex."""
    messages = [
        {"role": "system", "content": EXTRACTION_PROMPT},
        {"role": "user", "content": text[:max_chars]},
    ]
    try:
        result = await gateway.structured("extraction", messages, {})
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM extraction failed, using fallback: %s", exc)
        result = {}
    entities = result.get("entities") or []
    relations = result.get("relations") or []
    if not entities:
        return extract_entities_fallback(text)
    clean = [
        {"name": str(e.get("name", "")).strip()[:120], "type": str(e.get("type", "Concept"))[:40]}
        for e in entities[:50]
        if e.get("name")
    ]
    clean_rels = [
        {"source": str(r.get("source", "")).strip(), "target": str(r.get("target", "")).strip(), "type": str(r.get("type", "RELATED_TO"))[:40]}
        for r in relations[:50]
        if r.get("source") and r.get("target")
    ]
    return {"entities": clean, "relations": clean_rels}
