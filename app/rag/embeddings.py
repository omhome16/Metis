"""Local embeddings (free/offline).

Real model: `sentence-transformers` (default `BAAI/bge-m3`, 1024-dim, multilingual).
Lazy-loaded on first use; runs in a worker thread so the event loop never blocks.
`METIS_EMBED_MODEL=mock` yields deterministic vectors for tests / no-download dev.
"""

import asyncio
import hashlib

from sentence_transformers import SentenceTransformer

from app.core.config import Settings, get_settings


class Embedder:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model: SentenceTransformer | None = None

    def _load(self) -> SentenceTransformer:
        if self._model is None:
            self._model = SentenceTransformer(self.model_name)
        return self._model

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        model = await asyncio.to_thread(self._load)
        vectors = await asyncio.to_thread(model.encode, texts, normalize_embeddings=True, batch_size=16)
        return [v.tolist() for v in vectors]

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_texts([text]))[0]


class MockEmbedder:
    """Deterministic hashed vectors (8-dim) — no model download, tests only."""

    dim = 8

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._hash_vec(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._hash_vec(text)

    @staticmethod
    def _hash_vec(text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        vec = [b / 255.0 for b in digest[:8]]
        norm = sum(x * x for x in vec) ** 0.5 or 1.0
        return [x / norm for x in vec]


_EMBEDDER = None


def get_embedder(settings: Settings | None = None) -> Embedder | MockEmbedder:
    global _EMBEDDER
    if _EMBEDDER is None:
        s = settings or get_settings()
        _EMBEDDER = MockEmbedder() if s.embed_model == "mock" else Embedder(s.embed_model)
    return _EMBEDDER
