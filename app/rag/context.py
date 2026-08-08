"""Context engineering: pack retrieved chunks into a citation-requiring prompt.

M2: basic assembly with [n] citation markers and a token budget. Query rewriting,
dedup, ordering and grounding land with the M5 phase.
"""

from dataclasses import dataclass

from app.core.config import Settings, get_settings
from app.rag.chunking import count_tokens
from app.rag.retrieval import ChunkHit

SYSTEM_PROMPT = (
    "You are Metis, an expert research assistant grounded in the user's own document vault. "
    "Answer the user's question directly and confidently — when they ask for a definition or "
    "explanation, give one. Base your answer on the numbered sources below and cite them "
    "inline with [n]. Synthesize across sources when they complement each other. If the "
    "sources are thin on a point, say what the sources cover and then, only if genuinely "
    "helpful, add a sentence of general knowledge clearly marked as such. Never claim the "
    "sources lack the answer when the passages plausibly contain it."
)


@dataclass
class AssembledContext:
    messages: list[dict]
    citations: list[dict]  # [{n, chunk_id, doc}]
    tokens_used: int
    user_text: str = ""


def assemble_context(
    question: str,
    hits: list[ChunkHit],
    settings: Settings | None = None,
    image_captions: list[dict] | None = None,
) -> AssembledContext:
    s = settings or get_settings()
    blocks: list[str] = []
    citations: list[dict] = []
    tokens_used = 0
    budget = 6000  # max context tokens for retrieved chunks (blueprint §9)

    for n, hit in enumerate(hits, start=1):
        block = f"[{n}] ({hit.doc_title}) {hit.chunk.text.strip()}"
        tokens = count_tokens(block)
        if tokens_used + tokens > budget and blocks:
            break  # truncate lowest-relevance chunk last (first chunk always fits: better than empty context)
        blocks.append(block)
        citations.append({"n": n, "chunk_id": hit.chunk.id, "doc": hit.doc_title})
        tokens_used += tokens

    image_lines: list[str] = []
    for cap in image_captions or []:
        line = f"(image: {cap.get('doc', 'image')}) {cap.get('caption', '')} tags={cap.get('tags', [])}"
        image_lines.append(line)
        tokens_used += count_tokens(line)

    user_content = "\n\n".join(
        [
            "SOURCES:",
            *blocks,
            *(["IMAGES:", *image_lines] if image_lines else []),
            "",
            f"QUESTION: {question}",
        ]
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    return AssembledContext(messages=messages, citations=citations, tokens_used=tokens_used, user_text=user_content)
