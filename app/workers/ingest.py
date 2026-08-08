"""Background ingestion worker (arq): normalize uploaded files to raw text."""

from pathlib import Path

from pypdf import PdfReader
from sqlalchemy import select

from app.core.logging import get_logger
from app.db.models import Document, IngestJob
from app.db.session import async_session_factory

logger = get_logger(__name__)


def extract_text(fmt: str, file_path: str) -> str:
    """Normalize a stored file to plain text. Images return '' (handled in Phase 4)."""
    if fmt == "pdf":
        reader = PdfReader(file_path)
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if fmt in {"md", "txt"}:
        return Path(file_path).read_text(encoding="utf-8", errors="replace")
    return ""


async def process_ingest_job(ctx: dict, job_id: str) -> None:
    """Arq job: extract raw text for every pending document of this job and update progress."""
    async with async_session_factory() as session:
        job = await session.get(IngestJob, job_id)
        if job is None:
            logger.warning("job %s not found — skipping", job_id)
            return
        job.status = "running"
        await session.commit()

        docs = (
            (
                await session.execute(
                    select(Document).where(Document.file_path.like(f"uploads/{job_id}/%"))
                )
            )
            .scalars()
            .all()
        )
        total = max(len(docs), 1)
        for i, doc in enumerate(docs):
            try:
                doc.raw_text = extract_text(doc.format, doc.file_path or "")
                job.progress = round(((i + 1) / total) * 100, 1)
            except Exception as exc:  # per-file error isolation
                job.per_file_errors[str(doc.id)] = f"{type(exc).__name__}: {exc}"
                logger.exception("failed to parse %s", doc.file_path)
            await session.commit()

        job.status = "done" if not job.per_file_errors else "failed"
        await session.commit()
        logger.info("job %s finished: %d docs, errors=%s", job_id, len(docs), job.per_file_errors)
