"""Semantic router (Phase 4) — decides how a question should be served.

Lanes:
  fast     — greetings / thanks / trivial / explicit "no retrieval" phrasing:
             direct LLM chat, zero retrieval, zero DB, no cache write.
  standard — default: hybrid + rerank (+ metadata filters), no agent, no graph.
  deep     — agent + graph tools (+ P5 global search); multi-hop markers or
             explicit `mode=deep`.

Heuristic-first (keyword/length/punctuation rules); optional LLM refinement
behind METIS_ROUTER_LLM. Any failure degrades to the heuristic result and the
router itself never raises — the heuristic default is `standard`.
"""

import re
from typing import Literal

from app.core.config import settings
from app.core.logging import get_logger
from app.gateway.gateway import LLMGateway

logger = get_logger(__name__)

Lane = Literal["fast", "standard", "deep"]

# Exact-match greetings / acknowledgements (after stripping punctuation).
_FAST_EXACT = frozenset(
    {
        "hi",
        "hello",
        "hey",
        "yo",
        "howdy",
        "hiya",
        "hola",
        "hi there",
        "hello there",
        "how are you",
        "good morning",
        "good afternoon",
        "good evening",
        "good night",
        "thanks",
        "thank you",
        "thank u",
        "thx",
        "ty",
        "cheers",
        "appreciated",
        "ok",
        "okay",
        "k",
        "sure",
        "yep",
        "yeah",
        "yes",
        "no",
        "nope",
        "nah",
        "bye",
        "goodbye",
        "see you",
        "see ya",
        "later",
        "cya",
        "?",
        "!",
        ".",
        "...",
        "what?",
        "why?",
        "how?",
        "who?",
    }
)

# Explicit "answer without retrieval" phrasing.
_FAST_NO_RETRIEVAL = re.compile(
    r"\b(no retrieval|without (searching|using) (the )?(corpus|library|vault|knowledge)"
    r"|don'?t (search|look up|retrieve|use (the )?(corpus|library))"
    r"|skip (the )?(search|retrieval))\b",
    re.IGNORECASE,
)

# Multi-hop / corpus-level markers → deep lane.
_DEEP_MARKERS = (
    re.compile(r"\b(compare|contrast|versus|vs\.?)\b", re.IGNORECASE),
    re.compile(r"\bdifferences?\b", re.IGNORECASE),
    re.compile(r"\bsimilarities?\b", re.IGNORECASE),
    re.compile(r"\brelationship between\b", re.IGNORECASE),
    re.compile(r"\b(link|connection) between\b", re.IGNORECASE),
    re.compile(r"\bhow does .+ (relate|compare) to\b", re.IGNORECASE),
    re.compile(r"\bacross (the )?(corpus|library|vault|sources)\b", re.IGNORECASE),
    re.compile(r"\bsummarize (the )?(library|corpus|vault|knowledge)\b", re.IGNORECASE),
    re.compile(r"\bwhat does the (library|corpus|vault) (say|contain)\b", re.IGNORECASE),
    re.compile(r"\bthemes?\b", re.IGNORECASE),
)

ROUTER_PROMPT = (
    "Classify the user's input into exactly one lane and reply with JSON "
    '{"lane": "fast|standard|deep"}. '
    "fast: greetings, thanks, or trivia needing no source lookup. "
    "standard: factual asks about the library's content. "
    "deep: comparisons, relationships across sources, or corpus-level summaries."
)

ROUTER_SCHEMA = {
    "type": "object",
    "properties": {"lane": {"type": "string", "enum": ["fast", "standard", "deep"]}},
    "required": ["lane"],
}


def _heuristic(question: str) -> Lane:
    """Keyword/length/punctuation rules — synchronous, never raises."""
    text = re.sub(r"[\u2018\u2019\u201c\u201d]", "'", question)
    stripped = " ".join(text.strip().split())
    norm = stripped.rstrip("!?.,;").strip().lower() if stripped else stripped
    if len(stripped) <= 2:
        return "fast"
    if " " not in stripped and len(stripped) <= 12:
        return "fast"
    if norm in _FAST_EXACT:
        return "fast"
    if _FAST_NO_RETRIEVAL.search(stripped):
        return "fast"
    for marker in _DEEP_MARKERS:
        if marker.search(stripped):
            return "deep"
    return "standard"


async def _llm_refine(gateway: LLMGateway, question: str, heuristic: Lane) -> Lane:
    try:
        out = await gateway.structured(
            "router",
            [
                {"role": "system", "content": ROUTER_PROMPT},
                {"role": "user", "content": question},
            ],
            ROUTER_SCHEMA,
        )
        lane = (out or {}).get("lane")
        if lane in ("fast", "standard", "deep"):
            return lane
    except Exception as exc:  # noqa: BLE001 — routing must never break ask
        logger.warning("router LLM refinement skipped: %s", exc)
    return heuristic


async def route_question(
    question: str,
    gateway: LLMGateway | None,
    image: bool = False,
    mode: str | None = None,
    use_llm: bool | None = None,
) -> Lane:
    """Pick a serving lane for a question. Never raises; defaults to 'standard'."""
    if image:
        return "standard"  # image queries keep the multimodal direct path — never deep
    if mode in ("fast", "standard", "deep"):
        return mode
    lane = _heuristic(question)
    if gateway is not None and (settings.router_llm if use_llm is None else use_llm):
        return await _llm_refine(gateway, question, lane)
    return lane
