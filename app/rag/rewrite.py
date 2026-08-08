"""Query rewriting/expansion (blueprint §9): vague queries → searchable terms."""

from app.core.logging import get_logger
from app.gateway.gateway import LLMGateway

logger = get_logger(__name__)

REWRITE_PROMPT = (
    "Rewrite the user's question into a more effective search query for retrieval: expand "
    "vague terms into likely domain vocabulary, keep it concise (under 30 words), and do "
    "not invent facts. Return ONLY JSON: {\"query\": \"...\"}. If the question is already "
    "specific, return it unchanged."
)


async def rewrite_query(gateway: LLMGateway, question: str, max_len: int = 200) -> str | None:
    """Best-effort rewrite; returns None on failure so callers fall back to the original."""
    try:
        result = await gateway.structured(
            "fast",
            [
                {"role": "system", "content": REWRITE_PROMPT},
                {"role": "user", "content": question},
            ],
            {},
        )
        query = str(result.get("query", "")).strip()
        return query[:max_len] if query else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("query rewrite failed: %s", exc)
        return None
