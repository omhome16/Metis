"""ReAct agent: a tool-calling reasoning loop for /ask (M9).

The model gets three tools — `search_vault`, `graph_lookup`, `wikipedia` — and
iterates: reason → (call tools | answer). Every tool round emits a `thinking`
event so the frontend can show the agent at work, and every chunk it touches is
remembered so the final answer ships with real, numbered citations.

When the provider cannot call tools (tests, no key), the pipeline falls back to
the direct retrieval→generation path — this module is never a hard dependency.
"""

import json
from collections.abc import AsyncIterator

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.gateway.base import ToolCall
from app.gateway.gateway import LLMGateway
from app.graph.store import get_graph_store
from app.rag.embeddings import get_embedder
from app.rag.rerank import get_reranker
from app.rag.retrieval import ChunkHit, fetch_chunks_by_id, fuse_hybrid, keyword_search, vector_search

logger = get_logger(__name__)

MAX_STEPS = 4
TOOL_RESULT_CHARS = 700

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_vault",
            "description": (
                "Hybrid (vector + keyword) search inside the current document vault. "
                "Returns the most relevant passages with source numbers. Use this for "
                "any question about the vault's documents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"},
                    "top_k": {"type": "integer", "description": "How many passages to return (1-6)", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "graph_lookup",
            "description": (
                "Find chunks connected to an entity in the vault's knowledge graph. "
                "Use to follow conceptual connections — e.g. how one idea relates to others."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity": {"type": "string", "description": "Entity name to expand in the graph"},
                    "top_k": {"type": "integer", "description": "Maximum passages to return (1-6)", "default": 5},
                },
                "required": ["entity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wikipedia",
            "description": (
                "Short encyclopedia summary for general background knowledge. "
                "Never the primary source — always prefer evidence from the vault."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Topic to look up"}},
                "required": ["query"],
            },
        },
    },
]

SYSTEM_PROMPT = (
    "You are Metis, a precise research assistant working inside the user's document vault. "
    "You answer questions by using tools to gather evidence from the vault, then writing a "
    "clear, direct answer grounded in what you found.\n\n"
    "Rules:\n"
    "- Always search the vault before answering questions about the vault's documents. "
    "When the user asks for a definition or explanation, give one.\n"
    "- Prefer vault evidence. Wikipedia is background only — never answer solely from it.\n"
    "- Tool results show a source number for each passage. In your final answer, cite "
    "inline as [n] matching those numbers.\n"
    "- If you cannot find evidence, say so plainly instead of guessing.\n"
    "- Answer in the same language the user wrote in.\n"
)


class AgentMemory:
    """All chunks the agent has seen, numbered for citation stability."""

    def __init__(self) -> None:
        self._items: dict[str, dict] = {}

    def add(self, hits: list[ChunkHit]) -> None:
        for h in hits:
            if h.chunk.id not in self._items:
                score = h.rerank_score if h.rerank_score is not None else h.score
                self._items[h.chunk.id] = {
                    "n": len(self._items) + 1,
                    "chunk_id": h.chunk.id,
                    "doc": h.doc_title,
                    "text": h.chunk.text,
                    "score": score,
                }

    def sources(self) -> list[dict]:
        return sorted(self._items.values(), key=lambda s: s["n"])


# ── tools ─────────────────────────────────────────────────────────────────────


def _blocks_for(memory: AgentMemory, hits: list[ChunkHit]) -> list[dict]:
    blocks: list[dict] = []
    for h in hits:
        item = memory._items.get(h.chunk.id)  # noqa: SLF001 — same module
        if item is None:
            continue
        blocks.append(
            {
                "source": item["n"],
                "doc": h.doc_title,
                "text": h.chunk.text[:TOOL_RESULT_CHARS],
                "score": h.rerank_score if h.rerank_score is not None else h.score,
            }
        )
    return blocks


async def _search_vault(
    session: AsyncSession, corpus: str | None, query: str, top_k: int, memory: AgentMemory
) -> tuple[str, str]:
    """Hybrid vector+keyword search, reranked; returns (tool JSON, human summary)."""
    if not query.strip():
        return json.dumps({"error": "empty query"}), "empty query"
    embedder = get_embedder()
    qv = await embedder.embed_query(query)
    vh = await vector_search(session, qv, corpus=corpus, top_k=12)
    kh = await keyword_search(session, query, corpus=corpus, top_k=12)
    hits = fuse_hybrid(vh, kh, top_k=10)
    hits = await get_reranker().rerank(query, hits, top_k=max(1, min(int(top_k or 5), 6)))
    memory.add(hits)
    return (
        json.dumps({"results": _blocks_for(memory, hits)}, ensure_ascii=False),
        f"found {len(hits)} passage(s)",
    )


async def _graph_lookup(
    session: AsyncSession, corpus: str | None, entity: str, top_k: int, memory: AgentMemory
) -> tuple[str, str]:
    """Expand an entity's neighborhood in the knowledge graph."""
    if not entity.strip():
        return json.dumps({"error": "empty entity"}), "empty entity"
    try:
        store = get_graph_store()
        if not await store.ping():
            return json.dumps({"error": "knowledge graph unavailable"}), "graph unavailable"
        ids = await store.neighbor_chunk_ids([entity], max_hops=2, limit=8)
        if not ids:
            return json.dumps({"results": [], "note": f"no graph data for '{entity}'"}), "no graph data"
        hits = await fetch_chunks_by_id(session, ids[:max(1, int(top_k or 5))])
        memory.add(hits)
        return (
            json.dumps({"results": _blocks_for(memory, hits)}, ensure_ascii=False),
            f"expanded {entity}: {len(hits)} related passage(s)",
        )
    except Exception as exc:  # noqa: BLE001 — graph must never break the agent
        logger.warning("graph_lookup failed for %r: %s", entity, exc)
        return json.dumps({"error": str(exc)}), "graph lookup failed"


async def _wikipedia(query: str, memory: AgentMemory) -> tuple[str, str]:
    """Best-effort encyclopedia summary. Never the primary source."""
    try:
        async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as client:
            url = "https://en.wikipedia.org/api/rest_v1/page/summary/" + query.strip().replace(" ", "_")
            resp = await client.get(url)
            if resp.status_code == 404:
                titles = await _wikipedia_suggest(client, query)
                if not titles:
                    return json.dumps({"error": "no wikipedia page found"}), "no wikipedia page"
                resp = await client.get(
                    "https://en.wikipedia.org/api/rest_v1/page/summary/" + titles[0].replace(" ", "_")
                )
            if resp.status_code != 200:
                return json.dumps({"error": f"http {resp.status_code}"}), "wikipedia unavailable"
            data = resp.json()
            extract = (data.get("extract") or "")[:800]
            return json.dumps({"title": data.get("title", query), "summary": extract}, ensure_ascii=False), (
                "wikipedia summary fetched (background only)"
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("wikipedia lookup failed for %r: %s", query, exc)
        return json.dumps({"error": "wikipedia unavailable"}), "wikipedia unavailable"


async def _wikipedia_suggest(client: httpx.AsyncClient, query: str) -> list[str]:
    try:
        resp = await client.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "opensearch", "search": query, "limit": 1, "format": "json"},
        )
        if resp.status_code == 200:
            return resp.json()[1] or []
    except Exception:  # noqa: BLE001
        pass
    return []


async def _execute_tool(
    session: AsyncSession, corpus: str | None, call: ToolCall, memory: AgentMemory
) -> tuple[str, str]:
    try:
        args = call.arguments or {}
        if call.name == "search_vault":
            return await _search_vault(session, corpus, str(args.get("query", "")), int(args.get("top_k", 5)), memory)
        if call.name == "graph_lookup":
            return await _graph_lookup(session, corpus, str(args.get("entity", "")), int(args.get("top_k", 5)), memory)
        if call.name == "wikipedia":
            return await _wikipedia(str(args.get("query", "")), memory)
        return json.dumps({"error": f"unknown tool '{call.name}'"}), "unknown tool"
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc)}), "tool error"


# ── the loop ──────────────────────────────────────────────────────────────────


async def agent_events(
    session: AsyncSession,
    gateway: LLMGateway,
    question: str,
    corpus: str | None,
    history: list[dict] | None = None,
    usage: dict | None = None,
) -> AsyncIterator[tuple[str, dict]]:
    """Yield ('thinking'|'tokens'|'agent_done', data) tuples.

    `agent_done` carries `{"answer": str, "sources": [...]}` — the final answer
    text and every numbered source the agent touched (for citations).
    """
    memory = AgentMemory()
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in (history or [])[-8:]:
        role = m.get("role")
        content = m.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content[:4000]})
    messages.append(
        {
            "role": "user",
            "content": (
                f"QUESTION: {question}\n"
                f"(You are inside the vault '{corpus or 'default'}'. "
                "Use the tools to gather evidence, then answer with inline [n] citations.)"
            ),
        }
    )

    final_text = ""
    step = 0
    while step < MAX_STEPS:
        step += 1
        buf: list[str] = []
        calls: list[ToolCall] = []
        try:
            async for chunk in gateway.chat_tools_stream("generation", messages, TOOL_SCHEMAS):
                if chunk.text:
                    buf.append(chunk.text)
                if chunk.tool_calls:
                    calls = chunk.tool_calls
        except Exception as exc:  # noqa: BLE001 — tool-calling failed; give up the loop
            logger.warning("agent step %d failed (%s) — falling back to direct answer", step, exc)
            final_text = "".join(buf).strip()
            break

        if usage is not None:
            usage["in"] = usage.get("in", 0) + _count_messages(messages)

        if not calls:
            final_text = "".join(buf).strip()
            break

        # The model asked for tools → run them and feed results back.
        reasoning = "".join(buf).strip()
        tc_payload = [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
            for c in calls
        ]
        messages.append({"role": "assistant", "content": reasoning, "tool_calls": tc_payload})
        for call in calls:
            result, summary = await _execute_tool(session, corpus, call, memory)
            yield ("thinking", {"step": step, "tool": call.name, "args": call.arguments, "result": summary})
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

    if not final_text:
        final_text = (
            "I searched the vault but could not assemble a confident answer from the "
            "passages I found. Try rephrasing, or ask about something this vault covers."
        )
    for token in final_text.split(" "):
        yield ("tokens", {"text": token + " "})
    yield ("agent_done", {"answer": final_text, "sources": memory.sources()})


def _count_messages(messages: list[dict]) -> int:
    total = 0
    for m in messages:
        content = m.get("content") or ""
        if isinstance(content, str):
            total += len(content) // 4
        else:
            total += sum(len(str(p).split()) for p in content)
    return max(1, total)
