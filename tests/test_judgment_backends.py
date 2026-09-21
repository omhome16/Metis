"""The two opt-in comparison backends: judgment reranking and lane routing.

Both exist to be *measured* rather than assumed, so the tests pin the boring part
that matters: when no backend is available, each one keeps its original behavior.
"""

from app.db.models import Chunk
from app.judgment import ChoiceAnswer, MockJudgmentClient, NoulAnswer, calibration
from app.rag import rerank as rerank_mod
from app.rag import router as router_mod
from app.rag.retrieval import ChunkHit


def _hit(cid: str, text: str) -> ChunkHit:
    return ChunkHit(
        chunk=Chunk(id=cid, doc_id="d", text=text, chunk_index=0, tokens=5),
        score=0.5,
        doc_title="t",
    )


def _client(monkeypatch, module, answers):
    client = MockJudgmentClient(answers)
    monkeypatch.setattr(calibration, "get_judgment_client", lambda: client)
    monkeypatch.setattr(module, "get_judgment_client", lambda: client)
    return client


# ── rerank ─────────────────────────────────────────────────────────────────


async def test_judgment_reranker_orders_by_probability(monkeypatch):
    hits = [_hit("a", "unrelated"), _hit("b", "the answer"), _hit("c", "meh")]
    client = _client(
        monkeypatch,
        rerank_mod,
        {
            "c0": NoulAnswer(noul=0.1),
            "c1": NoulAnswer(noul=0.95),
            "c2": NoulAnswer(noul=0.4),
        },
    )
    ranked = await rerank_mod.JudgmentReranker().rerank("q", hits, top_k=2)
    assert [h.chunk.id for h in ranked] == ["b", "c"]
    assert len(client.calls) == 1  # every candidate in one request
    assert set(client.calls[0][1]) == {"c0", "c1", "c2"}


async def test_judgment_reranker_keeps_retrieval_order_without_a_backend(monkeypatch):
    monkeypatch.setattr(rerank_mod, "get_judgment_client", lambda: None)
    hits = [_hit("a", "x"), _hit("b", "y")]
    ranked = await rerank_mod.JudgmentReranker().rerank("q", hits, top_k=1)
    assert [h.chunk.id for h in ranked] == ["a"]  # degraded, not crashed


async def test_judgment_reranker_rejects_an_incomplete_ranking(monkeypatch):
    """A partial response is not a ranking — fall back rather than guess."""
    hits = [_hit("a", "x"), _hit("b", "y")]
    _client(monkeypatch, rerank_mod, {"c0": NoulAnswer(noul=0.9)})
    ranked = await rerank_mod.JudgmentReranker().rerank("q", hits, top_k=2)
    assert [h.chunk.id for h in ranked] == ["a", "b"]


async def test_judgment_reranker_handles_no_hits(monkeypatch):
    _client(monkeypatch, rerank_mod, {})
    assert await rerank_mod.JudgmentReranker().rerank("q", [], top_k=5) == []


def test_get_reranker_selects_by_config():
    from app.core.config import Settings

    assert isinstance(
        rerank_mod.get_reranker(Settings(rerank_model="mock")), rerank_mod.MockReranker
    )


# ── router ─────────────────────────────────────────────────────────────────


async def test_judgment_router_picks_a_lane(monkeypatch):
    _client(monkeypatch, router_mod, {"lane": ChoiceAnswer(choice="deep", confidence=0.9)})
    monkeypatch.setattr(router_mod.settings, "router_backend", "judgment", raising=False)
    assert await router_mod.route_question("compare A and B", None) == "deep"


async def test_judgment_router_falls_back_to_heuristics(monkeypatch):
    monkeypatch.setattr(router_mod, "get_judgment_client", lambda: None)
    monkeypatch.setattr(router_mod.settings, "router_backend", "judgment", raising=False)
    assert await router_mod.route_question("hello", None) == "fast"


async def test_judgment_router_rejects_an_unknown_lane(monkeypatch):
    _client(monkeypatch, router_mod, {"lane": ChoiceAnswer(choice="sideways", confidence=0.1)})
    monkeypatch.setattr(router_mod.settings, "router_backend", "judgment", raising=False)
    assert await router_mod.route_question("tell me about fastapi", None) == "standard"


async def test_heuristic_router_is_the_default(monkeypatch):
    monkeypatch.setattr(router_mod.settings, "router_backend", "heuristic", raising=False)
    monkeypatch.setattr(router_mod.settings, "router_llm", False, raising=False)
    client = _client(monkeypatch, router_mod, {"lane": ChoiceAnswer(choice="deep", confidence=0.9)})
    assert await router_mod.route_question("hello", None) == "fast"
    assert client.calls == []  # no network hop unless configured


async def test_explicit_mode_still_wins(monkeypatch):
    assert await router_mod.route_question("anything", None, mode="deep") == "deep"
