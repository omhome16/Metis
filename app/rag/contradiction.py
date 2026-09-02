"""Contradiction detection (blueprint §8.2 step 9) + citation parsing for grounding."""

import re

from app.core.logging import get_logger
from app.gateway.gateway import LLMGateway

logger = get_logger(__name__)

CONTRADICTION_PROMPT = (
    "Compare the two passages. Decide whether they express directly contradictory claims "
    "about the same subject (not merely different topics). Return ONLY JSON: "
    '{"contradicts": true|false, "reason": "short explanation"}'
)


async def check_contradiction(gateway: LLMGateway, text_a: str, text_b: str) -> dict:
    """Judge whether two passages contradict each other. Never raises."""
    try:
        result = await gateway.structured(
            "judge",
            [
                {"role": "system", "content": CONTRADICTION_PROMPT},
                {"role": "user", "content": f"PASSAGE A:\n{text_a[:1500]}\n\nPASSAGE B:\n{text_b[:1500]}"},
            ],
            {},
        )
        return {
            "contradicts": bool(result.get("contradicts")),
            "reason": str(result.get("reason", ""))[:300],
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("contradiction check failed: %s", exc)
        return {"contradicts": False, "reason": ""}


_CITE_RE = re.compile(r"\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\]")


def parse_citations(answer: str) -> set[int]:
    """Extract the [n] citation markers actually emitted in the answer.

    Handles bare markers (`[1]`) and comma-separated ones (`[1, 2]`) — the
    frontend renders both as citation chips.
    """
    numbers: set[int] = set()
    for group in _CITE_RE.findall(answer):
        numbers.update(int(n) for n in re.findall(r"\d{1,3}", group))
    return numbers
