"""Document metadata management (tags, doc_date, author)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Document
from app.db.session import get_session
from app.schemas.api import DocumentMetaOut, DocumentMetaUpdate

logger = get_logger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])


@router.patch("/{doc_id}/metadata", response_model=DocumentMetaOut)
async def update_document_metadata(
    doc_id: str,
    payload: DocumentMetaUpdate,
    session: AsyncSession = Depends(get_session),
) -> DocumentMetaOut:
    doc = await session.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")
    if payload.tags is not None:
        doc.tags = payload.tags
    if payload.doc_date is not None:
        doc.doc_date = payload.doc_date
    if payload.author is not None:
        doc.author = payload.author
    await session.commit()
    return DocumentMetaOut(id=doc.id, tags=doc.tags, doc_date=doc.doc_date, author=doc.author)
