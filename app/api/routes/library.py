"""Library — the whole library, not just one vault.

Cross-vault graph export, entity search, mined "surprises" (unexpected
connections between vaults) and idea journeys (a narrated shortest path between
any two entities). Narratives are LLM-generated with template fallbacks so the
endpoints stay fast and never fail hard.
"""

import json

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.core.logging import get_logger
from app.db.models import ReorgRun
from app.db.session import async_session_factory
from app.gateway.gateway import get_gateway
from app.graph.store import get_graph_store

logger = get_logger(__name__)
router = APIRouter(prefix="/library", tags=["library"])


async def _store():
    store = get_graph_store()
    if not await store.ping():
        raise HTTPException(status_code=503, detail="knowledge graph unavailable (Neo4j down?)")
    return store


@router.get("/graph")
async def library_graph(
    node_limit: int = Query(260, ge=10, le=5000), edge_limit: int = Query(700, ge=10, le=5000)
) -> dict:
    store = await _store()
    return await store.library_graph(node_limit=node_limit, edge_limit=edge_limit)


@router.get("/entities")
async def search_entities(
    q: str = Query(..., min_length=1, max_length=120), limit: int = Query(12, ge=1, le=50)
) -> dict:
    store = await _store()
    return {"entities": await store.search_entities(q, limit)}


@router.get("/surprises")
async def surprises(limit: int = Query(6, ge=1, le=12)) -> dict:
    """Mine unexpected cross-vault connections and narrate them in one LLM call."""
    store = await _store()
    cards = await store.library_surprises(limit=limit)
    if not cards:
        return {"cards": [], "note": "Index more vaults to surface cross-library connections."}
    cards = await _narrate_cards(cards)
    return {"cards": cards}


@router.get("/journey")
async def journey(
    from_: str = Query(..., alias="from", min_length=1, max_length=120),
    to: str = Query(..., min_length=1, max_length=120),
) -> dict:
    """Shortest entity-to-entity path across the library, with a narrated story."""
    store = await _store()
    if from_.strip().lower() == to.strip().lower():
        return {
            "found": False,
            "from": from_,
            "to": to,
            "nodes": [],
            "rels": [],
            "narrative": "Start and destination are the same entity.",
        }
    path = await store.journey(from_.strip(), to.strip())
    if path is None:
        return {
            "found": False,
            "from": from_,
            "to": to,
            "nodes": [],
            "rels": [],
            "narrative": f"No path found between '{from_}' and '{to}' in the current graph.",
        }
    path["found"] = True
    path["narrative"] = await _narrate_journey(path)
    return path


@router.get("/reorganizations")
async def reorganizations(limit: int = Query(8, ge=1, le=50)) -> dict:
    """Audit log of library reorganizations (P8): auto after ingest batches, or manual."""
    try:
        async with async_session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(ReorgRun).order_by(ReorgRun.run_at.desc()).limit(limit)
                    )
                )
                .scalars()
                .all()
            )
        return {
            "runs": [
                {
                    "run_at": r.run_at.isoformat() if r.run_at else None,
                    "triggered_by": r.triggered_by,
                    "docs_since_last": r.docs_since_last,
                    "communities_before": r.communities_before,
                    "communities_after": r.communities_after,
                    "summaries_made": r.summaries_made,
                    "detail": r.detail,
                }
                for r in rows
            ]
        }
    except Exception as exc:  # noqa: BLE001 — the log must never fail the library view
        logger.warning("reorg log read failed: %s", exc)
        return {"runs": [], "error": str(exc)}


# ── LLM narration (template fallbacks) ───────────────────────────────────────


def _template_card(card: dict) -> str:
    if card.get("kind") == "shared":
        vaults = ", ".join(card.get("vaults") or [])
        return f"{card['entity']} appears in both {vaults} — a rare concept bridging separate libraries."
    return f"{card.get('vault_a', 'a vault')}'s {card['source']} is connected to {card.get('vault_b', 'another vault')}'s {card['target']}."


async def _narrate_cards(cards: list[dict]) -> list[dict]:
    """One LLM call writes a one-sentence insight for every card; template on failure."""
    lines = []
    for i, c in enumerate(cards):
        if c.get("kind") == "shared":
            lines.append(
                f"{i + 1}. concept '{c['entity']}' (type {c.get('type', 'Concept')}) appears in vaults {', '.join(c['vaults'])}"
            )
        else:
            lines.append(
                f"{i + 1}. '{c['source']}' (in vault {c.get('vault_a')}) is related to '{c['target']}' (in vault {c.get('vault_b')})"
            )
    prompt = (
        "These are automatically discovered connections between document vaults. "
        "For each, write ONE sentence (max 22 words) that explains why the connection "
        "is interesting or surprising. No numbering, no markdown. "
        'Return ONLY JSON of the shape {"narrations": ["...", "..."]}, one string per connection:'
        + "\n\n"
        + "\n".join(lines)
    )
    try:
        result = await get_gateway().structured(
            "fast",
            [
                {
                    "role": "system",
                    "content": "You write short, insightful explanations about connections between ideas.",
                },
                {"role": "user", "content": prompt},
            ],
            {},
        )
        narrations = result.get("narrations") or result.get("insights") or []
        if isinstance(narrations, str):
            try:
                narrations = json.loads(narrations).get("narrations", [])
            except Exception:  # noqa: BLE001
                narrations = []
        if not isinstance(narrations, list):
            narrations = []
        for i, c in enumerate(cards):
            c["insight"] = str(narrations[i])[:220] if i < len(narrations) else _template_card(c)
    except Exception as exc:  # noqa: BLE001 — narration must never fail the endpoint
        logger.warning("surprise narration skipped: %s", exc)
        for c in cards:
            c["insight"] = _template_card(c)
    return cards


async def _narrate_journey(path: dict) -> str:
    """Narrate the path as a short story; template on failure."""
    names = [nd["name"] for nd in path.get("nodes", [])]
    rels = path.get("rels", [])
    if len(names) < 2:
        return f"'{path['from']}' and '{path['to']}' are the same node."
    if len(names) == 2:
        return f"'{names[0]}' and '{names[1]}' are directly connected in the library."
    chain = " \u2192 ".join(f"{n} [{r}]" for n, r in zip(names, rels + [""], strict=False))
    prompt = (
        "You are Metis, a librarian AI. The shortest connection between two ideas in a "
        "knowledge library goes through these entities (and relationship types):"
        + "\n"
        + chain
        + "\nWrite a 2-3 sentence narrative that explains this connection as a surprising or "
        "insightful journey between the ideas. No markdown, no preamble."
    )
    try:
        result = await get_gateway().chat(
            "fast",
            [
                {
                    "role": "system",
                    "content": "You narrate conceptual journeys through a knowledge graph.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=220,
        )
        text = result.text.strip()
        if len(text) > 30:
            return text[:800]
    except Exception as exc:  # noqa: BLE001
        logger.warning("journey narration skipped: %s", exc)
    return (
        f"The shortest path from '{names[0]}' to '{names[-1]}' runs through "
        + ", ".join(names[1:-1])
        + ("." if len(names) == 2 else " — each hop carries the previous idea forward.")
    )
