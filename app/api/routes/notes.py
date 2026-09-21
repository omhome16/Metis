"""Save an answer (or any text) as a note document.

Notes are first-class documents: they land in the same chunk → embed → graph
pipeline, so later questions can cite them like any source. The library
compounds — every good answer becomes part of the map.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.imports import save_text_document
from app.core.auth import CurrentUser, owned_vault
from app.db.session import get_session
from app.schemas.api import IngestResponse

router = APIRouter(prefix="/vaults", tags=["notes"])


class NoteCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=512)
    content: str = Field(..., min_length=1, max_length=200_000)
    message_id: str | None = Field(None, max_length=64)


@router.post("/{name}/notes", response_model=IngestResponse, status_code=202)
async def create_note(
    name: str,
    payload: NoteCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: CurrentUser = None,
) -> IngestResponse:
    await owned_vault(session, user, name)
    content = payload.content.strip()
    if payload.message_id:
        content = f"> Saved from message `{payload.message_id}`\n\n{content}"
    job_id, created = await save_text_document(
        session, name, payload.title.strip(), content, fmt="md", tags=["note"]
    )
    return IngestResponse(job_id=job_id, status="queued", files_added=1 if created else 0)
