"""RAGAS-definition metrics (blueprint §12), judged by the gateway or a judgment model.

All metric functions take a gateway (duck-typed: `.structured(task, messages, schema)`)
and optional embed function so unit tests can drive them deterministically with stubs.

Every metric here is a pile of *binary judgments* — is this claim supported, is this
chunk useful, is this claim present. `_judge_batch` runs them on whichever backend is
configured:

* the **LLM path** costs one call per item. That is why a free-tier run could exhaust
  its daily quota mid-matrix and silently fall back to the mock provider, which is what
  produced the quota-artifact zeros documented in the README.
* the **judgment path** puts every item in one state and asks all the questions in one
  request, so a batched metric is a single call. Items that come back ambiguous (~0.5)
  escalate to the LLM individually rather than guessing.
"""

import re
from dataclasses import dataclass

from app.core.config import get_settings
from app.core.logging import get_logger
from app.judgment.base import Noul
from app.judgment.calibration import ask_quietly, get_judgment_client

logger = get_logger(__name__)

CLAIMS_PROMPT = (
    'Extract the atomic factual claims from the answer. Return ONLY JSON: {"claims": ["..."]}'
)
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


@dataclass(frozen=True)
class JudgeSpec:
    """One binary judgment, defined once so both backends ask the same question.

    `prompt`/`key` drive the LLM path unchanged; `instructions` plus the true/false
    criteria drive the judgment path.
    """

    prompt: str
    key: str
    instructions: str
    true_description: str
    false_description: str


SUPPORT = JudgeSpec(
    prompt=SUPPORT_PROMPT,
    key="supported",
    instructions="Is the CLAIM supported by the CONTEXT?",
    true_description="The context states the claim, or directly implies that it is true.",
    false_description="The context contradicts the claim, or says nothing about it.",
)
USEFULNESS = JudgeSpec(
    prompt=USEFULNESS_PROMPT,
    key="useful",
    instructions="Is the CONTEXT useful for answering the QUESTION?",
    true_description="The context contains information that helps answer the question.",
    false_description="The context does not help answer the question.",
)
PRESENCE = JudgeSpec(
    prompt=PRESENT_PROMPT,
    key="present",
    instructions="Does the CONTEXT contain the information expressed in the CLAIM?",
    true_description="The context contains that information.",
    false_description="The context does not contain that information.",
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


async def _judge_batch(gateway, spec: JudgeSpec, extras: list[str]) -> list[bool]:
    """Judge every item, as one request when a judgment backend is available."""
    if not extras:
        return []
    judged = await _judge_batch_by_judgment(spec, extras)
    if judged is None:
        return [await _judge_bool(gateway, spec.prompt, extra, spec.key) for extra in extras]
    # Ambiguous items fall back to the LLM one at a time; decided ones are kept.
    return [
        value if value is not None else await _judge_bool(gateway, spec.prompt, extras[i], spec.key)
        for i, value in enumerate(judged)
    ]


async def _judge_batch_by_judgment(spec: JudgeSpec, extras: list[str]) -> list[bool | None] | None:
    """One batched judgment call, or None to use the LLM path.

    None per item means that item was ambiguous and should escalate.
    """
    client = get_judgment_client()
    if client is None:
        return None
    band = get_settings().judgment_noul_uncertain_band
    names = [f"item_{i}" for i in range(len(extras))]
    questions = {
        name: Noul(
            # Backticked path into the shared state, per System One's state syntax.
            instructions=f"{spec.instructions} The CLAIM and CONTEXT to judge are `items.{name}`.",
            true_description=spec.true_description,
            false_description=spec.false_description,
        )
        for name in names
    }
    state = {"items": dict(zip(names, extras, strict=True))}
    result = await ask_quietly(client, state, questions)
    if result is None:
        return None
    out: list[bool | None] = []
    for name in names:
        noul = result.noul(name)
        if noul is None:
            return None  # a missing answer means the batch as a whole is unusable
        out.append(None if abs(noul - 0.5) < band else noul >= 0.5)
    return out


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
    supported = await _judge_batch(
        gateway, SUPPORT, [f"CONTEXT:\n{context_blob}\n\nCLAIM:\n{c}" for c in claims]
    )
    return round(sum(supported) / len(claims), 4)


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
    useful = await _judge_batch(
        gateway,
        USEFULNESS,
        [f"QUESTION:\n{question}\n\nCONTEXT:\n{c[:1500]}" for c in contexts],
    )
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
    present = await _judge_batch(
        gateway, PRESENCE, [f"CONTEXT:\n{context_blob}\n\nCLAIM:\n{c}" for c in claims]
    )
    return round(sum(present) / len(claims), 4)


_CITE_RE = re.compile(r"\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\]")


def _parsed_citations(answer: str) -> set[int]:
    """[n] markers in the answer — bare ([1]) or comma-separated ([1, 2])."""
    numbers: set[int] = set()
    for group in _CITE_RE.findall(answer):
        numbers.update(int(n) for n in re.findall(r"\d{1,3}", group))
    return numbers


def citation_correctness(answer: str, context_ids: list[str]) -> tuple[float, dict]:
    """Fraction of [n] citations in the answer that map to a retrieved chunk."""
    parsed = sorted(_parsed_citations(answer))
    if not parsed:
        return 0.0, {"emitted": 0, "valid": 0}
    valid = sum(1 for n in parsed if 1 <= n <= len(context_ids))
    return round(valid / len(parsed), 4), {"emitted": len(parsed), "valid": valid}


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))  # lengths checked above
    na = sum(x * x for x in a) ** 0.5 or 1.0
    nb = sum(y * y for y in b) ** 0.5 or 1.0
    return dot / (na * nb)
