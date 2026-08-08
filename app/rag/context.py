"""Context engineering: pack retrieved chunks into a citation-requiring prompt.

M2: basic assembly with [n] citation markers and a token budget. Query rewriting,
dedup, ordering and grounding land with the M5 phase.
"""

from dataclasses import dataclass

from app.core.config import Settings, get_settings
from app.rag.chunking import count_tokens
from app.rag.retrieval import ChunkHit

SYSTEM_PROMPT = (
    "You are Metis, an expert research assistant. Answer the user's question using ONLY the "
    "sources below. Cite your sources inline with [n] matching the source numbers. If the "
    "sources do not contain the answer, say so explicitly instead of guessing."
)


@dataclass
class AssembledContext:
    messages: list[dict]
    citations: list[dict]  # [{n, chunk_id, doc}]
    tokens_used: int
    user_text: str = ""


def assemble_context(question: str, hits: list[ChunkHit], settings: Settings | None = None) -> AssembledContext:
    s = settings or get_settings()
    blocks: list[str] = []
    citations: list[dict] = []
    tokens_used = 0
    budget = 6000  # max context tokens for retrieved chunks (blueprint §9)

    for n, hit in enumerate(hits, start=1):
        block = f"[{n}] ({hit.doc_title}) {hit.chunk.text.strip()}"
        tokens = count_tokens(block)
        if tokens_used + tokens > budget and blocks:
            break  # truncate lowest-relevance chunk last
        blocks.append(block)
        citations.append({"n": n, "chunk_id": hit.chunk.id, "doc": hit.doc_title})
        tokens_used += tokens

    user_content = "\n\n".join(
        [
            "SOURCES:",
            *blocks,
            "",
            f"QUESTION: {question}",
        ]
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    return AssembledContext(messages=messages, citations=citations, tokens_used=tokens_used, user_text=user_content)
