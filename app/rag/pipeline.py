"""The /ask pipeline (blueprint §8.2).

Emits `(event, data)` tuples consumed by the SSE route:
  sources → [thinking → tokens] → citations → contradiction → done

M5 wiring: query rewriting, hybrid retrieval (vector + tsvector + RRF), graph
boost, local reranking, citation grounding, and the contradiction scan.

M9 wiring: when the gateway can call tools, a ReAct agent (app.rag.agent) drives
retrieval with `search_vault` / `graph_lookup` / `wikipedia` and streams
`thinking` events; conversation history from the previous turns is fed back in.
Providers without tool support (and image queries) keep the direct path.
"""

import base64
import re
import uuid
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Chunk
from app.gateway.gateway import LLMGateway, estimate_cost_usd
from app.graph.extraction import extract_entities
from app.graph.store import get_graph_store
from app.rag.agent import agent_events
from app.rag.chunking import count_tokens
from app.rag.contradiction import check_contradiction, parse_citations
from app.rag.context import assemble_context
from app.rag.embeddings import get_embedder, get_image_embedder
from app.rag.global_search import global_answer, global_intent
from app.rag.metadata import extract_query_metadata
from app.rag.rerank import get_reranker
from app.rag.retrieval import (
    ChunkHit,
    expand_to_parents,
    fetch_chunks_by_id,
    fuse_hybrid,
    image_search,
    keyword_search,
    merge_hits,
    vector_search,
)
from app.rag.rewrite import rewrite_query
from app.rag.router import Lane

logger = get_logger(__name__)

_DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;]+);base64,(?P<b64>.+)$", re.S)

_FAST_SYSTEM = {
    "role": "system",
    "content": "You are a concise, friendly assistant. Answer briefly and "
    "conversationally, without mentioning sources, retrieval, or citations.",
}


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
    config: dict | None = None,
) -> tuple[list[ChunkHit], str, dict]:
    """Query rewrite → hybrid (vector+keyword, RRF) → graph boost → rerank → parents.

    `config` (used by the eval harness) can override rerank_enabled / graph_boost /
    top_k_rerank / parent_child / metadata_filter. Returns (hits, rewritten, meta)
    where `meta` is the active metadata filter ({} when none).
    """
    cfg = config or {}
    rerank_enabled = cfg.get("rerank_enabled", settings.rerank_enabled)
    graph_boost = cfg.get("graph_boost", True)
    top_k = int(cfg.get("top_k_rerank", settings.top_k_rerank))
    parent_child = cfg.get("parent_child", settings.parent_child)
    metadata_filter = cfg.get("metadata_filter", settings.metadata_filter)

    rewritten = question
    if settings.query_rewrite:
        try:
            candidate = await rewrite_query(gateway, question)
            if candidate:
                rewritten = candidate
        except Exception as exc:  # noqa: BLE001
            logger.warning("query rewrite skipped: %s", exc)

    meta: dict = {}
    if metadata_filter:
        try:
            meta = await extract_query_metadata(gateway, rewritten)
        except Exception as exc:  # noqa: BLE001
            logger.warning("metadata filter skipped: %s", exc)

    embedder = get_embedder()
    query_vec = await embedder.embed_query(rewritten)
    vector_hits = await vector_search(
        session, query_vec, corpus=corpus, top_k=settings.rerank_candidates, meta=meta
    )
    keyword_hits = await keyword_search(
        session, rewritten, corpus=corpus, top_k=settings.rerank_candidates, meta=meta
    )
    hits = fuse_hybrid(vector_hits, keyword_hits, top_k=settings.rerank_candidates)

    if graph_boost:
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

    if rerank_enabled:
        hits = await get_reranker().rerank(rewritten, hits, top_k=top_k)

    if parent_child:
        try:
            hits = await expand_to_parents(session, hits)
        except Exception as exc:  # noqa: BLE001
            logger.warning("parent expansion skipped: %s", exc)

    return hits, rewritten, meta


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


def _sources_payload(chunks: list[ChunkHit]) -> dict:
    return {
        "chunks": [
            {"id": h.chunk.id, "doc": h.doc_title, "text": h.chunk.text[:400], "score": h.score} for h in chunks
        ]
    }


def _history_messages(history: list[dict] | None) -> list[dict]:
    """Normalize stored conversation turns into OpenAI-style messages (last 8)."""
    out: list[dict] = []
    for m in (history or [])[-8:]:
        role = m.get("role")
        content = m.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            out.append({"role": role, "content": content[:4000]})
    return out


async def ask_events(
    session: AsyncSession,
    gateway: LLMGateway,
    question: str,
    corpus: str | None = None,
    image: str | None = None,
    history: list[dict] | None = None,
    lane: Lane = "standard",
) -> AsyncIterator[tuple[str, dict]]:
    answer_id = str(uuid.uuid4())
    use_agent = lane == "deep" and getattr(gateway, "supports_tools", False) and not image

    # ── fast lane: direct LLM chat — zero retrieval, zero DB, no cache ─────
    if lane == "fast":
        history_msgs = _history_messages(history)
        messages = [_FAST_SYSTEM, *history_msgs, {"role": "user", "content": question}]
        prompt_tokens = count_tokens(question)
        out_tokens = 0
        text_parts: list[str] = []
        yield ("sources", {"chunks": []})
        yield ("meta", {"lane": "fast"})
        try:
            async for token in gateway.chat_stream("fast", messages):
                out_tokens += len(token.split())  # stream chunks may contain several tokens
                text_parts.append(token)
                yield ("tokens", {"text": token})
        except Exception as exc:  # noqa: BLE001
            logger.exception("fast generation failed: %s", exc)
            text_parts.append(f"\n[generation failed: {exc}]")
            yield ("tokens", {"text": f"\n[generation failed: {exc}]"})
        yield ("citations", {"citations": [], "grounded": False})
        yield (
            "done",
            {
                "answer_id": answer_id,
                "usage": {"in": prompt_tokens, "out": out_tokens, "lane": lane},
                "cost_usd": round(estimate_cost_usd(settings.fast_model, {"in": prompt_tokens, "out": out_tokens}), 6),
            },
        )
        return

    # ── global lane: map-reduce over community summaries (P5) ───────────────
    if lane == "deep" and not image and global_intent(question):
        global_result = None
        try:
            global_result = await global_answer(gateway, question, corpus)
        except Exception as exc:  # noqa: BLE001 — degrade to deep/standard serving
            logger.warning("global answer skipped: %s", exc)
        if global_result is not None:
            communities = [
                {
                    "id": c["id"],
                    "summary": c["summary"],
                    "entity_count": c["entity_count"],
                    "members": c["members"],
                }
                for c in global_result["communities"]
            ]
            yield ("sources", {"communities": communities})
            yield ("meta", {"lane": "deep", "mode": "global"})
            for token in global_result["answer"].split(" "):
                yield ("tokens", {"text": token + " "})
            citations = [
                {
                    "n": i,
                    "community_id": c["id"],
                    "doc": "Community",
                    "summary": c["summary"][:160],
                }
                for i, c in enumerate(global_result["communities"], start=1)
            ]
            yield ("citations", {"citations": citations, "grounded": True, "mode": "global"})
            yield (
                "done",
                {
                    "answer_id": answer_id,
                    "usage": {
                        "in": global_result["in"],
                        "out": global_result["out"],
                        "lane": lane,
                        "mode": "global",
                    },
                    "cost_usd": global_result["cost_usd"],
                },
            )
            return

    # ── retrieval / initial sources ───────────────────────────────────────
    image_hits: list = []
    hits: list[ChunkHit] = []
    if image:
        try:
            mime, image_bytes = parse_image_data_url(image)
            query_vec = await get_image_embedder().embed_image(image_bytes, mime)
            image_hits = await image_search(session, query_vec, corpus=corpus, top_k=3)
        except Exception as exc:  # noqa: BLE001
            logger.warning("image query failed: %s", exc)
        sources = {"chunks": []}
        if image_hits:
            sources["images"] = [
                {"id": h.image.id, "doc": h.doc_title, "caption": h.image.caption, "score": h.score}
                for h in image_hits
            ]
        yield ("sources", sources)
        yield ("meta", {"lane": lane})
    elif use_agent:
        # The agent drives retrieval via its tools — emit an empty placeholder now;
        # the rich, final source list arrives with the answer.
        yield ("sources", {"chunks": [], "agent": True})
        yield ("meta", {"lane": "deep"})
    else:
        hits, _rewritten, meta = await retrieve_context(
            session, gateway, question, corpus, config={"graph_boost": lane == "deep"}
        )
        yield ("sources", _sources_payload(hits))
        if meta:
            yield ("meta", {"lane": lane, "filters": meta})
        else:
            yield ("meta", {"lane": lane})

    # ── generation ────────────────────────────────────────────────────────
    out_tokens = 0
    text_parts: list[str] = []
    agent_sources: list[dict] | None = None
    assembled_citations: list[dict] = []

    if use_agent:
        usage_est: dict = {"in": 0}
        try:
            async for event, data in agent_events(session, gateway, question, corpus, history, usage=usage_est):
                if event == "thinking":
                    yield ("thinking", data)
                elif event == "tokens":
                    out_tokens += len(data["text"].split())
                    text_parts.append(data["text"])
                    yield ("tokens", data)
                elif event == "agent_done":
                    agent_sources = data["sources"]
        except Exception as exc:  # noqa: BLE001
            logger.exception("agent failed: %s", exc)
            text_parts.append(f"\n[agent error: {exc}]")
            yield ("tokens", {"text": f"\n[agent error: {exc}]"})
        if agent_sources is not None:
            hits = [
                ChunkHit(
                    chunk=Chunk(id=s["chunk_id"], doc_id="", text=s["text"], chunk_index=0, tokens=0, embedding=None),
                    score=float(s.get("score") or 0.0),
                    doc_title=s["doc"],
                )
                for s in agent_sources
            ]
            yield ("sources", {"chunks": [{"id": s["chunk_id"], "doc": s["doc"], "text": s["text"][:400], "score": s.get("score")} for s in agent_sources]})
        prompt_tokens = usage_est.get("in", 0)
    else:
        image_captions = [
            {"doc": h.doc_title, "caption": h.image.caption or "", "tags": h.image.tags} for h in image_hits
        ]
        assembled = assemble_context(question, hits, image_captions=image_captions)
        history_msgs = _history_messages(history)
        messages = [assembled.messages[0], *history_msgs, assembled.messages[1]]
        prompt_tokens = count_tokens(assembled.user_text)
        try:
            async for token in gateway.chat_stream("generation", messages):
                out_tokens += len(token.split())  # stream chunks may contain several tokens
                text_parts.append(token)
                yield ("tokens", {"text": token})
        except Exception as exc:  # noqa: BLE001
            logger.exception("generation failed: %s", exc)
            text_parts.append(f"\n[generation failed: {exc}]")
            yield ("tokens", {"text": f"\n[generation failed: {exc}]"})

    answer = "".join(text_parts)
    usage = {"in": prompt_tokens, "out": out_tokens, "lane": lane}

    # ── citations (grounded) ──────────────────────────────────────────────
    if agent_sources is not None:
        assembled_citations = [
            {"n": s["n"], "chunk_id": s["chunk_id"], "doc": s["doc"]} for s in agent_sources
        ]
    else:
        assembled_citations = [{"n": n, "chunk_id": h.chunk.id, "doc": h.doc_title} for n, h in enumerate(hits, start=1)]

    emitted = parse_citations(answer)
    if emitted:
        kept = [c for c in assembled_citations if c["n"] in emitted]
        dropped = [c["n"] for c in assembled_citations if c["n"] not in emitted]
        yield ("citations", {"citations": kept, "dropped": dropped, "grounded": True})
    else:
        yield ("citations", {"citations": assembled_citations, "grounded": False})

    # ── contradiction scan ────────────────────────────────────────────────
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
