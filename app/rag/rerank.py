"""Reranking (blueprint §8.2 step 5).

Real: local cross-encoder `BAAI/bge-reranker-base` (free/offline). Mock: token-overlap
scoring so tests and no-download dev stay deterministic.
"""

import asyncio

from sentence_transformers import CrossEncoder

from app.core.config import Settings, get_settings
from app.rag.retrieval import ChunkHit


class Reranker:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model: CrossEncoder | None = None

    def _load(self) -> CrossEncoder:
        if self._model is None:
            self._model = CrossEncoder(self.model_name)
        return self._model

    async def rerank(self, query: str, hits: list[ChunkHit], top_k: int = 5) -> list[ChunkHit]:
        if not hits:
            return []
        model = await asyncio.to_thread(self._load)
        pairs = [(query, h.chunk.text[:1500]) for h in hits]
        scores = await asyncio.to_thread(model.predict, pairs, show_progress_bar=False)
        ranked = sorted(zip(hits, scores), key=lambda pair: -float(pair[1]))
        return [h for h, _ in ranked[:top_k]]


class MockReranker:
    """Deterministic lexical-overlap reranker for tests / no-download dev."""

    async def rerank(self, query: str, hits: list[ChunkHit], top_k: int = 5) -> list[ChunkHit]:
        query_terms = set(query.lower().split())
        scored = [
            (h, len(set(h.chunk.text.lower().split()) & query_terms) / max(1, len(query_terms)))
            for h in hits
        ]
        ranked = sorted(scored, key=lambda pair: -pair[1])
        return [h for h, _ in ranked[:top_k]]


_RERANKER = None


def get_reranker(settings: Settings | None = None) -> Reranker | MockReranker:
    global _RERANKER
    if _RERANKER is None:
        s = settings or get_settings()
        _RERANKER = MockReranker() if s.rerank_model == "mock" else Reranker(s.rerank_model)
    return _RERANKER
