"""Local embeddings (free/offline).

Text: `sentence-transformers` (default `BAAI/bge-m3`, 1024-dim, multilingual).
Images: CLIP (`clip-ViT-B-32`, 512-dim). Both are lazy-loaded and run in worker
threads so the event loop never blocks. `...=mock` yields deterministic vectors
for tests / no-download dev.
"""

import asyncio
import hashlib
import io

from PIL import Image
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
        vectors = await asyncio.to_thread(
            model.encode, texts, normalize_embeddings=True, batch_size=16, show_progress_bar=False
        )
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


# ── image embeddings (CLIP) ────────────────────────────────────────────────────


class ImageEmbedder:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model: SentenceTransformer | None = None

    def _load(self) -> SentenceTransformer:
        if self._model is None:
            self._model = SentenceTransformer(self.model_name)
        return self._model

    async def embed_image(self, data: bytes, mime: str = "image/png") -> list[float]:
        model = await asyncio.to_thread(self._load)
        image = Image.open(io.BytesIO(data)).convert("RGB")
        vector = await asyncio.to_thread(model.encode, image, normalize_embeddings=True)
        return vector.tolist()


class MockImageEmbedder:
    """Deterministic hash of image bytes (8-dim) — tests only."""

    dim = 8

    async def embed_image(self, data: bytes, mime: str = "image/png") -> list[float]:
        digest = hashlib.sha256(data).digest()
        vec = [b / 255.0 for b in digest[: self.dim]]
        norm = sum(x * x for x in vec) ** 0.5 or 1.0
        return [x / norm for x in vec]


_IMAGE_EMBEDDER = None


def get_image_embedder(settings: Settings | None = None) -> ImageEmbedder | MockImageEmbedder:
    global _IMAGE_EMBEDDER
    if _IMAGE_EMBEDDER is None:
        s = settings or get_settings()
        _IMAGE_EMBEDDER = MockImageEmbedder() if s.clip_model == "mock" else ImageEmbedder(s.clip_model)
    return _IMAGE_EMBEDDER
