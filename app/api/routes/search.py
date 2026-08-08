"""`GET /api/v1/search` — raw retrieval results (vector-only until M5 hybrid)."""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.rag.embeddings import get_embedder
from app.rag.retrieval import vector_search

router = APIRouter(tags=["search"])


class SearchResult(BaseModel):
    chunk_id: str
    doc: str
    text: str
    score: float


@router.get("/search", response_model=list[SearchResult])
async def search(
    q: str = Query(..., min_length=1),
    top_k: int = Query(5, ge=1, le=50),
    corpus: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[SearchResult]:
    embedder = get_embedder()
    query_vec = await embedder.embed_query(q)
    hits = await vector_search(session, query_vec, corpus=corpus, top_k=top_k or settings.top_k_rerank)
    return [SearchResult(chunk_id=h.chunk.id, doc=h.doc_title, text=h.chunk.text[:400], score=h.score) for h in hits]
