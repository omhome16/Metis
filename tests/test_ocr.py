"""P6: OCR + explicit extraction_status on zero-text PDFs.

The deterministic part (blank PDF → extraction_status=empty + no chunks) runs
anywhere. The pytesseract path needs the tesseract binary — skipped when absent.
"""

import shutil
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete

from app.db.models import Chunk, Document
from app.db.session import async_session_factory
from app.rag.embeddings import get_embedder
from app.workers.ingest import _process_text, extract_text

HAS_TESSERACT = shutil.which("tesseract") is not None


def _make_pdf(tmp_path: Path, *, with_text: bool) -> str:
    """Build a tiny PDF via PyMuPDF — text layer when asked, image-only otherwise."""
    import pymupdf

    path = tmp_path / f"{uuid.uuid4().hex[:8]}.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    if with_text:
        page.insert_text((72, 72), "Hello Metis OCR", fontsize=14)
    doc.save(path)
    doc.close()
    return str(path)


async def _make_doc(session, file_path: str, corpus: str) -> Document:
    doc = Document(
        title="ocr fixture",
        corpus=corpus,
        format="pdf",
        content_hash=uuid.uuid4().hex,
        file_path=file_path,
    )
    session.add(doc)
    await session.commit()
    return doc


async def _cleanup(doc_id: str) -> None:
    async with async_session_factory() as session:
        await session.execute(delete(Chunk).where(Chunk.doc_id == doc_id))
        await session.execute(delete(Document).where(Document.id == doc_id))
        await session.commit()


async def test_blank_pdf_marks_empty_without_ocr(require_db, tmp_path):
    """Zero-text PDF without an OCR engine → extraction_status=empty, no chunks."""
    from unittest.mock import AsyncMock

    path = _make_pdf(tmp_path, with_text=False)
    assert extract_text("pdf", path).strip() == ""

    async with async_session_factory() as session:
        doc = await _make_doc(session, path, f"test-ocr-{uuid.uuid4().hex[:6]}")
        await _process_text(session, AsyncMock(), AsyncMock(), False, get_embedder(), doc)
        await session.refresh(doc)
        assert doc.extraction_status == "empty"
        assert doc.raw_text.strip() == ""
        doc_id = doc.id
    await _cleanup(doc_id)


@pytest.mark.skipif(not HAS_TESSERACT, reason="tesseract binary not on PATH")
async def test_ocr_recovers_scanned_pdf(require_db, tmp_path):
    """METIS_OCR_ENGINE=pytesseract recovers text from an image-only PDF."""
    from unittest.mock import AsyncMock, patch

    from app.workers import ingest

    path = _make_pdf(tmp_path, with_text=False)
    assert extract_text("pdf", path).strip() == ""

    async with async_session_factory() as session:
        doc = await _make_doc(session, path, f"test-ocr-{uuid.uuid4().hex[:6]}")
        with patch.object(ingest.settings, "ocr_engine", "pytesseract"):
            await _process_text(session, AsyncMock(), AsyncMock(), False, get_embedder(), doc)
        await session.refresh(doc)
        assert doc.extraction_status == "ocr"
        assert "metis" in doc.raw_text.lower()
        doc_id = doc.id
    await _cleanup(doc_id)


async def test_regular_pdf_keeps_ok_status(require_db, tmp_path):
    """A PDF with a real text layer is indexed as usual (extraction_status=ok)."""
    from unittest.mock import AsyncMock

    path = _make_pdf(tmp_path, with_text=True)
    async with async_session_factory() as session:
        doc = await _make_doc(session, path, f"test-ocr-{uuid.uuid4().hex[:6]}")
        await _process_text(session, AsyncMock(), AsyncMock(), False, get_embedder(), doc)
        await session.refresh(doc)
        assert doc.extraction_status == "ok"
        assert "Hello Metis OCR" in doc.raw_text
        doc_id = doc.id
    await _cleanup(doc_id)
