"""RAGAS-definition metrics (blueprint §12), computed via the LLM gateway.

All metric functions take a gateway (duck-typed: `.structured(task, messages, schema)`)
and optional embed function so unit tests can drive them deterministically with stubs.
"""

import re

from app.core.logging import get_logger

logger = get_logger(__name__)

CLAIMS_PROMPT = "Extract the atomic factual claims from the answer. Return ONLY JSON: {\"claims\": [\"...\"]}"
SUPPORT_PROMPT = (
    "Decide whether the CLAIM is supported by the provided CONTEXT. "
    'Return ONLY JSON: {"supported": true|false}'
)
QUESTIONS_PROMPT = (
    "Generate 3 short questions that this answer could plausibly be the answer to. "
    'Return ONLY JSON: {"questions": ["..."]}'
)
USEFULNESS_PROMPT = (
    "Decide whether this context chunk is useful for answering the QUESTION. "
    'Return ONLY JSON: {"useful": true|false}'
)
PRESENT_PROMPT = (
    "Decide whether the context contains the information expressed in the CLAIM. "
    'Return ONLY JSON: {"present": true|false}'
)

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_RE.split(text.strip()) if len(s.strip()) > 3]


def _fallback_claims(answer: str) -> list[str]:
    return _sentences(answer)


async def _judge_bool(gateway, prompt: str, extra: str, key: str) -> bool:
    try:
        result = await gateway.structured(
            "judge",
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": extra[:2000]},
            ],
            {},
        )
        return bool(result.get(key))
    except Exception as exc:  # noqa: BLE001
        logger.warning("judge call failed (%s): %s", key, exc)
        return False


async def faithfulness(gateway, answer: str, contexts: list[str]) -> float:
    """Fraction of answer claims supported by the context (RAGAS Faithfulness)."""
    if not answer.strip():
        return 0.0
    try:
        claims = (
            await gateway.structured(
                "judge",
                [
                    {"role": "system", "content": CLAIMS_PROMPT},
                    {"role": "user", "content": answer[:2000]},
                ],
                {},
            )
        ).get("claims") or []
        claims = [str(c) for c in claims if str(c).strip()]
    except Exception:  # noqa: BLE001
        claims = []
    if not claims:
        claims = _fallback_claims(answer)
    if not claims:
        return 1.0
    context_blob = "\n\n".join(contexts)[:6000]
    supported = 0
    for c in claims:
        if await _judge_bool(gateway, SUPPORT_PROMPT, f"CONTEXT:\n{context_blob}\n\nCLAIM:\n{c}", "supported"):
            supported += 1
    return round(supported / len(claims), 4)


async def answer_relevancy(gateway, question: str, answer: str, embed) -> float:
    """Mean cosine similarity between the question and questions derived from the answer."""
    if not answer.strip():
        return 0.0
    try:
        questions = (
            await gateway.structured(
                "judge",
                [
                    {"role": "system", "content": QUESTIONS_PROMPT},
                    {"role": "user", "content": answer[:2000]},
                ],
                {},
            )
        ).get("questions") or []
        questions = [str(q) for q in questions if str(q).strip()]
    except Exception:  # noqa: BLE001
        questions = []
    if not questions:
        return 1.0  # no generated questions to compare — neutral
    q_vec = await embed(question)
    sims = []
    for q in questions:
        v = await embed(q)
        sims.append(_cosine(q_vec, v))
    return round(sum(sims) / len(sims), 4)


async def context_precision(gateway, question: str, contexts: list[str]) -> float:
    """RAGAS ContextPrecision: average precision over the ranked context chunks."""
    if not contexts:
        return 0.0
    useful = [
        await _judge_bool(gateway, USEFULNESS_PROMPT, f"QUESTION:\n{question}\n\nCONTEXT:\n{c[:1500]}", "useful")
        for c in contexts
    ]
    numerator, denominator = 0.0, 0
    for k, is_useful in enumerate(useful, start=1):
        if is_useful:
            precision_at_k = sum(useful[:k]) / k
            numerator += precision_at_k
            denominator += 1
    return round(numerator / denominator, 4) if denominator else 0.0


async def context_recall(gateway, ground_truth: str, contexts: list[str]) -> float:
    """Fraction of ground-truth claims present in the context (RAGAS ContextRecall)."""
    claims = _sentences(ground_truth)
    if not claims:
        return 0.0
    context_blob = "\n\n".join(contexts)[:6000]
    present = 0
    for c in claims:
        if await _judge_bool(gateway, PRESENT_PROMPT, f"CONTEXT:\n{context_blob}\n\nCLAIM:\n{c}", "present"):
            present += 1
    return round(present / len(claims), 4)


def citation_correctness(answer: str, context_ids: list[str]) -> tuple[float, dict]:
    """Fraction of [n] citations in the answer that map to a retrieved chunk."""
    parsed = sorted({int(n) for n in re.findall(r"\[(\d{1,3})\]", answer)})
    if not parsed:
        return 0.0, {"emitted": 0, "valid": 0}
    valid = sum(1 for n in parsed if 1 <= n <= len(context_ids))
    return round(valid / len(parsed), 4), {"emitted": len(parsed), "valid": valid}


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5 or 1.0
    nb = sum(y * y for y in b) ** 0.5 or 1.0
    return dot / (na * nb)
