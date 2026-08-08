"""The /ask pipeline (blueprint §8.2).

Emits `(event, data)` tuples consumed by the SSE route:
  sources → tokens → citations → done
Graph-boosted retrieval (M3) and image-aware ask (M4) plug in here; hybrid/
rerank/grounding (M5) and the contradiction scan (M5) extend the same contract.
"""

import base64
import re
import uuid
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.gateway.gateway import LLMGateway, estimate_cost_usd
from app.graph.extraction import extract_entities
from app.graph.store import get_graph_store
from app.rag.chunking import count_tokens
from app.rag.context import assemble_context
from app.rag.embeddings import get_embedder, get_image_embedder
from app.rag.retrieval import fetch_chunks_by_id, image_search, merge_hits, vector_search

logger = get_logger(__name__)

_DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;]+);base64,(?P<b64>.+)$", re.S)


def parse_image_data_url(data_url: str) -> tuple[str, bytes]:
    """'data:image/png;base64,....' → (mime, raw bytes)."""
    match = _DATA_URL_RE.match(data_url)
    if not match:
        raise ValueError("invalid image data URL")
    return match.group("mime"), base64.b64decode(match.group("b64"))


async def retrieve_context(
    session: AsyncSession,
    gateway: LLMGateway,
    question: str,
    corpus: str | None,
) -> list:
    """Vector search first, then graph-boost: entities from the question → Neo4j
    neighbor chunks. Gracefully skips the graph when Neo4j is unreachable."""
    embedder = get_embedder()
    query_vec = await embedder.embed_query(question)
    hits = await vector_search(session, query_vec, corpus=corpus, top_k=settings.rerank_candidates)

    try:
        store = get_graph_store()
        if await store.ping():
            extracted = await extract_entities(gateway, question, max_chars=4000)
            names = [e["name"] for e in extracted.get("entities", [])][:8]
            if names:
                chunk_ids = await store.neighbor_chunk_ids(names, max_hops=2, limit=10)
                graph_hits = await fetch_chunks_by_id(session, chunk_ids)
                hits = merge_hits(hits, graph_hits, top_k=settings.rerank_candidates)
    except Exception as exc:  # noqa: BLE001 — graph boost must never break ask
        logger.warning("graph boost skipped: %s", exc)

    return hits


async def ask_events(
    session: AsyncSession,
    gateway: LLMGateway,
    question: str,
    corpus: str | None = None,
    image: str | None = None,
) -> AsyncIterator[tuple[str, dict]]:
    answer_id = str(uuid.uuid4())

    hits = await retrieve_context(session, gateway, question, corpus)

    image_hits: list = []
    if image:
        try:
            mime, image_bytes = parse_image_data_url(image)
            embedder = get_image_embedder()
            query_vec = await embedder.embed_image(image_bytes, mime)
            image_hits = await image_search(session, query_vec, corpus=corpus, top_k=3)
        except Exception as exc:  # noqa: BLE001
            logger.warning("image query failed: %s", exc)

    sources = {"chunks": [{"id": h.chunk.id, "doc": h.doc_title, "text": h.chunk.text[:400], "score": h.score} for h in hits]}
    if image_hits:
        sources["images"] = [
            {"id": h.image.id, "doc": h.doc_title, "caption": h.image.caption, "score": h.score} for h in image_hits
        ]
    yield ("sources", sources)

    image_captions = [
        {"doc": h.doc_title, "caption": h.image.caption or "", "tags": h.image.tags} for h in image_hits
    ]
    assembled = assemble_context(question, hits, image_captions=image_captions)
    prompt_tokens = count_tokens(assembled.user_text)

    out_tokens = 0
    text_parts: list[str] = []
    try:
        async for token in gateway.chat_stream("generation", assembled.messages):
            out_tokens += 1
            text_parts.append(token)
            yield ("tokens", {"text": token})
    except Exception as exc:  # noqa: BLE001
        logger.exception("generation failed: %s", exc)
        text_parts.append(f"\n[generation failed: {exc}]")
        yield ("tokens", {"text": f"\n[generation failed: {exc}]"})

    answer = "".join(text_parts)
    usage = {"in": prompt_tokens, "out": out_tokens}
    yield ("citations", {"citations": assembled.citations})
    yield (
        "done",
        {
            "answer_id": answer_id,
            "usage": usage,
            "cost_usd": round(estimate_cost_usd(settings.generation_model, usage), 6),
        },
    )
