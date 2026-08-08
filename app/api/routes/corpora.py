"""Corpus listing with counts (graph stats are filled in once Neo4j is wired up)."""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chunk, Document, ImageRecord
from app.db.session import get_session
from app.schemas.api import CorpusSummary

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

    return [
        CorpusSummary(
            corpus=corpus,
            doc_count=doc_counts.get(corpus, 0),
            chunk_count=chunk_counts.get(corpus, 0),
            image_count=image_counts.get(corpus, 0),
        )
        for corpus in sorted(doc_counts)
    ]
