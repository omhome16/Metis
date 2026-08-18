"""Entity/relationship extraction (blueprint §8.1 step 4).

Primary: Gemini structured output (via the gateway). Fallback: a local regex-based
extractor that works offline / in tests / when the LLM is unavailable.

P8 tiered whole-document extraction (`extract_document`): the old design ran ONE
LLM call over `text[:8000]`, so anything past the first ~8 pages of a long book
never reached the graph. Tiers:
  t1 — local regex over EVERY parent chunk (full-document entity coverage, zero
       LLM cost; co-occurrence edges come from `upsert_document_graph`).
  t2 — t1 + LLM typed relations on k sampled windows (start/middle/end).
  t3 — LLM per parent chunk (needs an API key; falls back to t1 otherwise).
All tiers merge + dedupe by canonical name before the single graph write.
"""

import re

from app.core.config import settings
from app.core.logging import get_logger
from app.gateway.gateway import LLMGateway
from app.rag.chunking import chunk_into_parents

logger = get_logger(__name__)

# Sentence punctuation stripped from entity-name edges ('C++'/'A.I.' survive).
_EDGE_PUNCT = ".,;:!?()[]{}'\"-–—"

ENTITY_TYPES = [
    "Person",
    "Place",
    "Concept",
    "Artwork",
    "Work",
    "Event",
    "Organization",
    "Technology",
]

EXTRACTION_PROMPT = (
    "Extract named entities and the relationships between them from the text below.\n"
    "Entity types: " + ", ".join(ENTITY_TYPES) + "\n"
    "Return JSON with this exact shape (no markdown):\n"
    '{"entities": [{"name": "...", "type": "..."}], "relations": [{"source": "...", "target": "...", "type": "RELATED_TO"}]}\n'
    "Include only entities that actually appear in the text. Max 40 entities."
)

_EXCLUDED = {
    "i",
    "the",
    "a",
    "an",
    "we",
    "you",
    "they",
    "it",
    "he",
    "she",
    "this",
    "that",
    "and",
    "or",
}

# P8 whole-document budgets (keeps Neo4j writes + merge cost bounded per doc).
_EXTRACT_LOCAL_PER_PARENT = 15
_EXTRACT_ENTITY_CAP = 200
_EXTRACT_REL_CAP = 200
_EXTRACT_T2_WINDOW_CHARS = 8000
_EXTRACT_T3_MAX_PARENTS = 60


def normalize_entity_name(name: str) -> str:
    """Canonical entity key: case-folded, whitespace-collapsed, punctuation-stripped.

    Used as the Neo4j `Entity.canonical` MERGE key so 'Neo4j', 'neo4j' and
    'Neo4j,' collapse onto one node (aliases kept for display/search). Only
    *surrounding sentence punctuation* is stripped — 'C++' stays 'c++'.
    """
    stripped = (name or "").strip().strip(_EDGE_PUNCT)
    return re.sub(r"\s+", " ", stripped).lower()


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


async def extract_entities(
    gateway: LLMGateway, text: str, max_chars: int = 8000, use_llm: bool = True
) -> dict:
    """Extract entities + relations.

    LLM path (typed relations) when `use_llm`; otherwise the local regex fallback
    (LazyGraphRAG-style default — zero LLM cost, offline-safe). Toggle via
    METIS_GRAPH_LLM_EXTRACT.
    """
    if not use_llm:
        return extract_entities_fallback(text)
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
        {
            "source": str(r.get("source", "")).strip(),
            "target": str(r.get("target", "")).strip(),
            "type": str(r.get("type", "RELATED_TO"))[:40],
        }
        for r in relations[:50]
        if r.get("source") and r.get("target")
    ]
    return {"entities": clean, "relations": clean_rels}


# ── P8 tiered whole-document extraction ──────────────────────────────────────


def _llm_capable() -> bool:
    """True when at least one real LLM route is configured (T3 needs one)."""
    return bool(settings.groq_api_key or settings.gemini_api_key or settings.ollama_model)


def _merge_extracts(extracts: list[dict]) -> dict:
    """Merge per-parent/per-window extracts: dedupe entities by canonical name,
    drop relations whose endpoints fell off the entity cap, dedupe by (src, tgt, type)."""
    counts: dict[str, int] = {}
    ents: dict[str, dict] = {}
    for ex in extracts:
        for e in ex.get("entities") or []:
            name = str(e.get("name", "")).strip()
            if not name:
                continue
            key = normalize_entity_name(name)
            if not key:
                continue
            counts[key] = counts.get(key, 0) + 1
            if key not in ents:
                ents[key] = {"name": name[:120], "type": str(e.get("type", "Concept"))[:40]}
    entities = sorted(ents.values(), key=lambda e: -counts[normalize_entity_name(e["name"])])[
        :_EXTRACT_ENTITY_CAP
    ]
    kept = {normalize_entity_name(e["name"]) for e in entities}
    rels: dict[tuple[str, str, str], dict] = {}
    for ex in extracts:
        for r in ex.get("relations") or []:
            source, target = str(r.get("source", "")).strip(), str(r.get("target", "")).strip()
            if not source or not target:
                continue
            sk, tk = normalize_entity_name(source), normalize_entity_name(target)
            if sk not in kept or tk not in kept or sk == tk:
                continue
            rel_type = str(r.get("type", "RELATED_TO"))[:40]
            key = (sk, tk, rel_type)
            rels.setdefault(key, {"source": source[:120], "target": target[:120], "type": rel_type})
    relations = sorted(
        rels.values(),
        key=lambda r: (normalize_entity_name(r["source"]), normalize_entity_name(r["target"])),
    )[:_EXTRACT_REL_CAP]
    return {"entities": entities, "relations": relations}


async def _extract_windows(
    gateway: LLMGateway, text: str, windows: int, use_llm: bool
) -> list[dict]:
    """LLM extraction on `windows` evenly-spaced windows (T2). Empty when LLM disabled."""
    if not use_llm or not _llm_capable():
        return []
    n = len(text)
    if n <= _EXTRACT_T2_WINDOW_CHARS:
        return [await extract_entities(gateway, text, use_llm=use_llm)]
    step = max((n - _EXTRACT_T2_WINDOW_CHARS) // max(windows - 1, 1), 1)
    starts = [min(i * step, n - _EXTRACT_T2_WINDOW_CHARS) for i in range(windows)]
    results: list[dict] = []
    for start in starts:
        results.append(
            await extract_entities(
                gateway, text[start : start + _EXTRACT_T2_WINDOW_CHARS], use_llm=use_llm
            )
        )
    return results


async def extract_document(
    gateway: LLMGateway,
    text: str,
    mode: str = "t1",
    parent_size: int = 2000,
    windows: int = 3,
    use_llm: bool = True,
) -> dict:
    """Whole-document extraction (P8). Never raises; unknown modes fall back to t1.

    t1: local regex over every parent chunk — entities from the WHOLE document
        (co-occurrence RELATED_TO edges are added by `upsert_document_graph`).
    t2: t1 + LLM typed relations on `windows` sampled windows (start/middle/end).
    t3: LLM per parent chunk (up to `_EXTRACT_T3_MAX_PARENTS`, rest local); falls
        back to t1 when no API key is configured.
    """
    parents = chunk_into_parents(text, parent_size) if text.strip() else []
    if not parents:
        return {"entities": [], "relations": []}
    local = [extract_entities_fallback(p, _EXTRACT_LOCAL_PER_PARENT) for p in parents]
    if mode == "t2":
        llm = await _extract_windows(gateway, text, windows, use_llm)
        return _merge_extracts([*local, *llm])
    if mode == "t3":
        if not use_llm or not _llm_capable():
            logger.warning("t3 needs an LLM key — falling back to t1 (local extraction)")
            return _merge_extracts(local)
        llm: list[dict] = []
        for parent in parents[:_EXTRACT_T3_MAX_PARENTS]:
            llm.append(await extract_entities(gateway, parent, use_llm=use_llm))
        return _merge_extracts([*local, *llm])
    return _merge_extracts(local)
