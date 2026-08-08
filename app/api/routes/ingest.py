"""Ingestion API: upload files, store raw documents, track a background job."""

import hashlib
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Document, IngestJob
from app.db.session import get_session
from app.schemas.api import IngestResponse, JobStatus
from app.workers.enqueue import enqueue_ingest_job

logger = get_logger(__name__)
router = APIRouter(prefix="/ingest", tags=["ingest"])

UPLOAD_DIR = Path("uploads")

# Extension → document format
_ALLOWED: dict[str, str] = {
    ".pdf": "pdf",
    ".md": "md",
    ".markdown": "md",
    ".txt": "txt",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
}


def infer_format(filename: str | None) -> str | None:
    if not filename:
        return None
    return _ALLOWED.get(Path(filename).suffix.lower())


@router.post("", response_model=IngestResponse, status_code=202)
async def ingest_files(
    files: Annotated[list[UploadFile], File(...)],
    corpus: Annotated[str, Form()] = "default",
    session: AsyncSession = Depends(get_session),
) -> IngestResponse:
    job_id = str(uuid.uuid4())
    job = IngestJob(id=job_id, corpus=corpus)
    session.add(job)

    job_dir = UPLOAD_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    added = 0
    for upload in files:
        fmt = infer_format(upload.filename)
        if fmt is None:
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {upload.filename}")
        content = await upload.read()
        digest = hashlib.sha256(content).hexdigest()
        # Idempotent ingestion: same bytes → skip.
        existing = (
            await session.execute(select(Document).where(Document.content_hash == digest))
        ).scalar_one_or_none()
        if existing:
            logger.info("duplicate skipped: %s", upload.filename)
            continue
        path = job_dir / f"{uuid.uuid4().hex}{Path(upload.filename).suffix}"
        path.write_bytes(content)
        session.add(
            Document(
                id=str(uuid.uuid4()),
                title=Path(upload.filename).stem,
                corpus=corpus,
                format=fmt,
                content_hash=digest,
                file_path=str(path),
            )
        )
        added += 1

    await session.commit()
    await enqueue_ingest_job(job_id)
    return IngestResponse(job_id=job_id, status="queued", files_added=added)


@router.get("/{job_id}", response_model=JobStatus)
async def job_status(job_id: str, session: AsyncSession = Depends(get_session)) -> JobStatus:
    job = await session.get(IngestJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return JobStatus(
        job_id=job.id, corpus=job.corpus, status=job.status, progress=job.progress, per_file_errors=job.per_file_errors
    )
