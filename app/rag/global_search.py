"""Global graph sensemaking (P5): map-reduce answers over community summaries.

Triggered by the deep lane + global-intent keywords ("themes", "summarize the
library", "what does the corpus say about …"). Degrades cleanly to None
(→ standard/deep retrieval) when Neo4j is down, no communities exist, or the
summaries have not been generated yet — honesty discipline: global answers
always cite the communities they draw on.
"""

import re

from app.core.config import settings
from app.core.logging import get_logger
from app.gateway.gateway import LLMGateway, estimate_cost_usd
from app.graph.store import get_graph_store
from app.rag.chunking import count_tokens

logger = get_logger(__name__)

GLOBAL_INTENT = re.compile(
    r"\b(themes?|summarize (the )?(library|corpus|vault|knowledge)"
    r"|what does the (library|corpus|vault) (say|contain|cover)"
    r"|overview of (the )?(library|corpus|vault)"
    r"|big picture|main ideas)\b",
    re.IGNORECASE,
)

_MAP_SYSTEM = (
    "You are analyzing one community of related concepts from a knowledge "
    "library. In 1-2 sentences: what does this community say about the user's "
    "question? Reply 'irrelevant' when the community does not touch it."
)

_REDUCE_SYSTEM = (
    "You are summarizing the whole library for the user. Below are notes from "
    "thematic communities. Synthesize a structured answer; tag each claim with "
    "the community number in brackets, e.g. [1]. Only use relevant communities, "
    "and note when a theme appears to be absent."
)


def global_intent(question: str) -> bool:
    return bool(GLOBAL_INTENT.search(question or ""))


async def load_communities(store, budget: int | None = None) -> list[dict]:
    """Top-k communities (by entity count) that already have summaries."""
    budget = max(1, int(budget or settings.global_relevance_budget))
    async with store._driver.session() as session:
        result = await session.run(
            "MATCH (c:Community) WHERE c.summary IS NOT NULL "
            "RETURN c.id AS id, c.summary AS summary, c.entity_count AS entity_count, "
            "  c.members AS members "
            "ORDER BY c.entity_count DESC LIMIT $limit",
            limit=budget,
        )
        return [
            {
                "id": rec["id"],
                "summary": rec["summary"],
                "entity_count": rec["entity_count"],
                "members": list(rec["members"] or []),
            }
            async for rec in result
        ]


async def global_answer(
    gateway: LLMGateway,
    question: str,
    corpus: str | None = None,
    budget: int | None = None,
) -> dict | None:
    """Map-reduce over community summaries; None when degraded (no communities)."""
    store = get_graph_store()
    try:
        if not await store.ping():
            return None
        communities = await load_communities(store, budget)
    except Exception as exc:  # noqa: BLE001 — global must never break ask
        logger.warning("community load failed: %s", exc)
        return None
    if not communities:
        return None

    used: list[dict] = []
    notes: list[str] = []
    total_in = 0
    total_out = 0
    for i, community in enumerate(communities, start=1):
        try:
            result = await gateway.chat(
                "generation",
                [
                    {"role": "system", "content": _MAP_SYSTEM},
                    {
                        "role": "user",
                        "content": (
                            f"Community {i} summary: {community['summary']}\n"
                            f"Members: {', '.join(community['members'][:10])}\n"
                            f"Question: {question}"
                        ),
                    },
                ],
                temperature=0.2,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("global map step failed: %s", exc)
            continue
        text = (result.text or "").strip()
        if not text or "irrelevant" in text.lower():
            continue
        notes.append(f"[{i}] {text}")
        used.append(community)
        total_in += int(result.usage.get("in", 0)) or count_tokens(
            f"{community['summary']} {question}"
        )
        total_out += int(result.usage.get("out", 0)) or count_tokens(text)
    if not notes:
        return None

    try:
        final = await gateway.chat(
            "generation",
            [
                {"role": "system", "content": _REDUCE_SYSTEM},
                {"role": "user", "content": f"Question: {question}\n\n" + "\n".join(notes)},
            ],
            temperature=0.3,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("global reduce step failed: %s", exc)
        return None
    answer = (final.text or "").strip()
    total_in += int(final.usage.get("in", 0)) or count_tokens("\n".join(notes))
    total_out += int(final.usage.get("out", 0)) or count_tokens(answer)

    return {
        "answer": answer,
        "communities": used,
        "in": total_in,
        "out": total_out,
        "cost_usd": round(
            estimate_cost_usd(settings.generation_model, {"in": total_in, "out": total_out}),
            6,
        ),
    }
