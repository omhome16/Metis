"""Reranking (blueprint §8.2 step 5).

Three backends, selected by `METIS_RERANK_BACKEND` / `METIS_RERANK_MODEL`:

* cross-encoder (default) — local `BAAI/bge-reranker-base`, free and offline;
* mock — token-overlap scoring, so tests and no-download dev stay deterministic;
* typesafe — graded relevance judgments, for measuring against the cross-encoder.
"""

import os

# Windows oneDNN/OMP threads crash fresh processes (0xC0000005) when loading
# bge-reranker weights. Pin threads BEFORE sentence-transformers/torch import.
os.environ.setdefault("OMP_NUM_THREADS", "1")

import asyncio  # noqa: E402

from sentence_transformers import CrossEncoder  # noqa: E402

from app.core.config import Settings, get_settings  # noqa: E402
from app.judgment.base import Noul  # noqa: E402
from app.judgment.calibration import ask_quietly, get_judgment_client  # noqa: E402
from app.rag.retrieval import ChunkHit  # noqa: E402


class Reranker:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model: CrossEncoder | None = None

    def _load(self) -> CrossEncoder:
        if self._model is None:
            import torch

            torch.set_num_threads(1)  # Windows oneDNN pool segfaults (0xC0000005) intermittently
            self._model = CrossEncoder(self.model_name)
        return self._model

    async def rerank(self, query: str, hits: list[ChunkHit], top_k: int = 5) -> list[ChunkHit]:
        if not hits:
            return []
        model = await asyncio.to_thread(self._load)
        pairs = [(query, h.chunk.text[:1500]) for h in hits]
        scores = await asyncio.to_thread(model.predict, pairs, show_progress_bar=False)
        # strict=False: a provider returning fewer scores must degrade, not raise.
        for h, s in zip(hits, scores, strict=False):
            h.rerank_score = round(float(s), 4)
        ranked = sorted(zip(hits, scores, strict=False), key=lambda pair: -float(pair[1]))
        return [h for h, _ in ranked[:top_k]]


class MockReranker:
    """Deterministic lexical-overlap reranker for tests / no-download dev."""

    async def rerank(self, query: str, hits: list[ChunkHit], top_k: int = 5) -> list[ChunkHit]:
        query_terms = set(query.lower().split())
        scored = [
            (h, len(set(h.chunk.text.lower().split()) & query_terms) / max(1, len(query_terms)))
            for h in hits
        ]
        for h, s in scored:
            h.rerank_score = round(float(s), 4)
        ranked = sorted(scored, key=lambda pair: -pair[1])
        return [h for h, _ in ranked[:top_k]]


class JudgmentReranker:
    """Rerank a shortlist with graded relevance judgments, in one request.

    **Off by default, and deliberately so.** The local cross-encoder is free, runs
    offline, and is already covered by an enforced eval gate, so this is a
    comparison backend to be measured rather than a presumed upgrade. Switch with
    `METIS_RERANK_BACKEND=typesafe` and compare `context_precision` through
    `scripts/run_matrix.py`; keep whichever wins on your own corpus.

    All candidates ride in one state, so this is one call rather than one per
    candidate (TypeSafe's own rerank cookbook uses a call per candidate).
    """

    name = "typesafe"

    async def rerank(self, query: str, hits: list[ChunkHit], top_k: int = 5) -> list[ChunkHit]:
        client = get_judgment_client()
        if client is None or not hits:
            return hits[:top_k]  # no backend → keep retrieval order, never fail
        names = [f"c{i}" for i in range(len(hits))]
        state = {
            "query": query[:500],
            "candidates": {n: h.chunk.text[:1200] for n, h in zip(names, hits, strict=True)},
        }
        questions = {
            name: Noul(
                instructions=(
                    "Is the passage at `candidates."
                    f"{name}` relevant to answering the QUERY at `query`? That passage "
                    "is the only one to judge."
                ),
                true_description="The passage helps answer the query.",
                false_description="The passage is unrelated to what the query asks.",
            )
            for name in names
        }
        result = await ask_quietly(client, state, questions)
        if result is None:
            return hits[:top_k]
        scored = []
        for name, hit in zip(names, hits, strict=True):
            noul = result.noul(name)
            if noul is None:
                return hits[:top_k]  # an incomplete response is not a ranking
            hit.rerank_score = round(float(noul), 4)
            scored.append((hit, float(noul)))
        ranked = sorted(scored, key=lambda pair: -pair[1])
        return [h for h, _ in ranked[:top_k]]


_RERANKER = None


def get_reranker(settings: Settings | None = None) -> Reranker | MockReranker | JudgmentReranker:
    global _RERANKER
    if _RERANKER is None:
        s = settings or get_settings()
        if s.rerank_backend == "typesafe":
            _RERANKER = JudgmentReranker()
        elif s.rerank_model == "mock":
            _RERANKER = MockReranker()
        else:
            _RERANKER = Reranker(s.rerank_model)
    return _RERANKER
