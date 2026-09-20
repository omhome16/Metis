"""Contradiction detection (blueprint §8.2 step 9) + citation parsing for grounding.

Two judges, one contract. `check_contradiction` returns
`{"contradicts": bool, "reason": str}` either way, so callers never branch:

* **LLM judge** (always available) asks a model to read both passages and write a
  JSON verdict — free text in, a parse that can fail out.
* **Judgment judge** (when `METIS_JUDGMENT_BACKEND` is configured) asks for the
  probability that the passages contradict, and thresholds it in code. Jev does
  not explain itself by design, so the reason is the probability itself.

An ambiguous judgment escalates to the LLM judge rather than acting: a Noul near
0.5 means similar probability either way, which is not a verdict.
"""

import re

from app.core.config import get_settings
from app.core.logging import get_logger
from app.gateway.gateway import LLMGateway
from app.judgment.base import Noul
from app.judgment.calibration import ask_quietly, get_judgment_client

logger = get_logger(__name__)

CONTRADICTION_PROMPT = (
    "Compare the two passages. Decide whether they express directly contradictory claims "
    "about the same subject (not merely different topics). Return ONLY JSON: "
    '{"contradicts": true|false, "reason": "short explanation"}'
)


def _contradiction_question() -> Noul:
    """Criteria fix what counts as yes and no, so every pair is judged alike."""
    return Noul(
        instructions=(
            "Do the two passages make directly contradictory claims about the same subject?"
            " Passages about different subjects are not a contradiction; passages that"
            " disagree about the same fact are."
        ),
        true_description="The passages make opposing claims about the same subject.",
        false_description=(
            "The passages agree with each other, or they are about different subjects."
        ),
    )


async def _contradiction_by_judgment(text_a: str, text_b: str) -> dict | None:
    """Calibrated verdict, or None to defer to the LLM judge."""
    client = get_judgment_client()
    if client is None:
        return None
    settings = get_settings()
    result = await ask_quietly(
        client,
        {"passage_a": text_a[:1500], "passage_b": text_b[:1500]},
        {"contradicts": _contradiction_question()},
    )
    if result is None:
        return None
    noul = result.noul("contradicts")
    if noul is None:
        return None
    if abs(noul - 0.5) < settings.judgment_noul_uncertain_band:
        logger.info("contradiction judgment ambiguous (p=%.2f) — deferring to the LLM judge", noul)
        return None
    return {
        "contradicts": noul >= settings.judgment_contradiction_threshold,
        "reason": f"Noul p(contradiction)={noul:.2f}",
        "probability": round(noul, 3),
    }


async def _contradiction_by_llm(gateway: LLMGateway, text_a: str, text_b: str) -> dict:
    """Original path: generate a JSON verdict and parse it. Never raises."""
    try:
        result = await gateway.structured(
            "judge",
            [
                {"role": "system", "content": CONTRADICTION_PROMPT},
                {
                    "role": "user",
                    "content": f"PASSAGE A:\n{text_a[:1500]}\n\nPASSAGE B:\n{text_b[:1500]}",
                },
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


async def check_contradiction(gateway: LLMGateway, text_a: str, text_b: str) -> dict:
    """Judge whether two passages contradict each other. Never raises."""
    judged = await _contradiction_by_judgment(text_a, text_b)
    if judged is not None:
        return judged
    return await _contradiction_by_llm(gateway, text_a, text_b)


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
