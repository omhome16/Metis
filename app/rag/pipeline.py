"""The /ask pipeline (blueprint §8.2).

Emits `(event, data)` tuples consumed by the SSE route:
  sources → tokens → citations → contradiction → done

M5 wiring: query rewriting, hybrid retrieval (vector + tsvector + RRF), graph
boost, local reranking, citation grounding, and the contradiction scan.
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
from app.rag.contradiction import check_contradiction, parse_citations
from app.rag.context import assemble_context
from app.rag.embeddings import get_embedder, get_image_embedder
from app.rag.rerank import get_reranker
from app.rag.retrieval import (
    ChunkHit,
    fetch_chunks_by_id,
    fuse_hybrid,
    image_search,
    keyword_search,
    merge_hits,
    vector_search,
)
from app.rag.rewrite import rewrite_query

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
) -> tuple[list[ChunkHit], str]:
    """Query rewrite → hybrid (vector+keyword, RRF) → graph boost → rerank."""
    rewritten = question
    if settings.query_rewrite:
        try:
            candidate = await rewrite_query(gateway, question)
            if candidate:
                rewritten = candidate
        except Exception as exc:  # noqa: BLE001
            logger.warning("query rewrite skipped: %s", exc)

    embedder = get_embedder()
    query_vec = await embedder.embed_query(rewritten)
    vector_hits = await vector_search(session, query_vec, corpus=corpus, top_k=settings.rerank_candidates)
    keyword_hits = await keyword_search(session, rewritten, corpus=corpus, top_k=settings.rerank_candidates)
    hits = fuse_hybrid(vector_hits, keyword_hits, top_k=settings.rerank_candidates)

    try:
        store = get_graph_store()
        if await store.ping():
            extracted = await extract_entities(gateway, rewritten, max_chars=4000)
            names = [e["name"] for e in extracted.get("entities", [])][:8]
            if names:
                chunk_ids = await store.neighbor_chunk_ids(names, max_hops=2, limit=10)
                graph_hits = await fetch_chunks_by_id(session, chunk_ids)
                hits = merge_hits(hits, graph_hits, top_k=settings.rerank_candidates)
    except Exception as exc:  # noqa: BLE001 — graph boost must never break ask
        logger.warning("graph boost skipped: %s", exc)

    if settings.rerank_enabled:
        hits = await get_reranker().rerank(rewritten, hits, top_k=settings.top_k_rerank)

    return hits, rewritten


async def contradiction_scan(
    gateway: LLMGateway,
    hits: list[ChunkHit],
) -> dict | None:
    """Compare the two top chunks; surface a 'sources disagree' alert if they conflict."""
    if len(hits) < 2:
        return None
    a, b = hits[0].chunk, hits[1].chunk
    try:
        store = get_graph_store()
        if not await store.ping():
            return None
        if await store.has_contradiction(a.id, b.id):
            return {"alert": "sources disagree", "chunks": [a.id, b.id], "reason": "previously detected"}
        verdict = await check_contradiction(gateway, a.text, b.text)
        if verdict["contradicts"]:
            await store.add_contradiction(a.id, b.id)
            return {"alert": "sources disagree", "chunks": [a.id, b.id], "reason": verdict["reason"]}
    except Exception as exc:  # noqa: BLE001
        logger.warning("contradiction scan skipped: %s", exc)
    return None


async def ask_events(
    session: AsyncSession,
    gateway: LLMGateway,
    question: str,
    corpus: str | None = None,
    image: str | None = None,
) -> AsyncIterator[tuple[str, dict]]:
    answer_id = str(uuid.uuid4())

    hits, _rewritten = await retrieve_context(session, gateway, question, corpus)

    image_hits: list = []
    if image:
        try:
            mime, image_bytes = parse_image_data_url(image)
            query_vec = await get_image_embedder().embed_image(image_bytes, mime)
            image_hits = await image_search(session, query_vec, corpus=corpus, top_k=3)
        except Exception as exc:  # noqa: BLE001
            logger.warning("image query failed: %s", exc)

    sources = {"chunks": [{"id": h.chunk.id, "doc": h.doc_title, "text": h.chunk.text[:400], "score": h.score} for h in hits]}
    if image_hits:
        sources["images"] = [
            {"id": h.image.id, "doc": h.doc_title, "caption": h.image.caption, "score": h.score} for h in image_hits
        ]
    yield ("sources", sources)

    image_captions = [{"doc": h.doc_title, "caption": h.image.caption or "", "tags": h.image.tags} for h in image_hits]
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

    # Grounding: keep only citations the answer actually references.
    emitted = parse_citations(answer)
    if emitted:
        kept = [c for c in assembled.citations if c["n"] in emitted]
        dropped = [c["n"] for c in assembled.citations if c["n"] not in emitted]
        yield ("citations", {"citations": kept, "dropped": dropped, "grounded": True})
    else:
        yield ("citations", {"citations": assembled.citations, "grounded": False})

    alert = await contradiction_scan(gateway, hits)
    if alert:
        yield ("contradiction", alert)

    yield (
        "done",
        {
            "answer_id": answer_id,
            "usage": usage,
            "cost_usd": round(estimate_cost_usd(settings.generation_model, usage), 6),
        },
    )
