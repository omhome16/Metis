"""Corpus listing with doc/chunk/image counts and best-effort graph entity counts."""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Chunk, Document, ImageRecord
from app.db.session import get_session
from app.graph.store import get_graph_store
from app.schemas.api import CorpusSummary

logger = get_logger(__name__)

router = APIRouter(tags=["corpora"])


@router.get("/corpora", response_model=list[CorpusSummary])
async def list_corpora(session: AsyncSession = Depends(get_session)) -> list[CorpusSummary]:
    doc_counts = dict(
        (await session.execute(select(Document.corpus, func.count(Document.id)).group_by(Document.corpus))).all()
    )
    chunk_counts = dict(
        (
            await session.execute(
                select(Document.corpus, func.count(Chunk.id))
                .join(Chunk, Chunk.doc_id == Document.id)
                .group_by(Document.corpus)
            )
        ).all()
    )
    image_counts = dict(
        (
            await session.execute(
                select(Document.corpus, func.count(ImageRecord.id))
                .join(ImageRecord, ImageRecord.doc_id == Document.id)
                .group_by(Document.corpus)
            )
        ).all()
    )

    graph = get_graph_store()
    graph_ok = await graph.ping() if doc_counts else False

    summaries = []
    for corpus in sorted(doc_counts):
        entity_count = 0
        if graph_ok:
            try:
                entity_count = await graph.entity_count(corpus)
            except Exception as exc:  # noqa: BLE001
                logger.warning("entity count failed for %s: %s", corpus, exc)
        summaries.append(
            CorpusSummary(
                corpus=corpus,
                doc_count=doc_counts.get(corpus, 0),
                chunk_count=chunk_counts.get(corpus, 0),
                image_count=image_counts.get(corpus, 0),
                entity_count=entity_count,
            )
        )
    return summaries
