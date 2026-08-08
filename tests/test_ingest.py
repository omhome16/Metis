import uuid
from unittest.mock import AsyncMock, patch

from sqlalchemy import delete, select

from app.api.routes import ingest as ingest_module
from app.db.models import Document, IngestJob, Vault
from app.db.session import async_session_factory


async def _cleanup(corpus: str) -> None:
    async with async_session_factory() as session:
        await session.execute(delete(Document).where(Document.corpus == corpus))
        await session.execute(delete(IngestJob).where(IngestJob.corpus == corpus))
        await session.execute(delete(Vault).where(Vault.name == corpus))  # ingest auto-creates vault rows
        await session.commit()


async def test_ingest_txt_file(client, tmp_path, require_db):
    corpus = f"test-{uuid.uuid4().hex[:8]}"
    content = f"Hello Metis! {uuid.uuid4()}".encode()  # unique bytes → unique content_hash
    with (
        patch.object(ingest_module, "UPLOAD_DIR", tmp_path),
        patch.object(ingest_module, "enqueue_ingest_job", new=AsyncMock(return_value=True)),
    ):
        resp = await client.post(
            "/api/v1/ingest",
            data={"corpus": corpus},
            files={"files": ("hello.txt", content, "text/plain")},
        )
    assert resp.status_code == 202
    body = resp.json()
    assert body["files_added"] == 1
    job_id = body["job_id"]

    # job status
    resp = await client.get(f"/api/v1/ingest/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"

    # document row stored with raw bytes
    async with async_session_factory() as session:
        docs = (await session.execute(select(Document).where(Document.corpus == corpus))).scalars().all()
        assert len(docs) == 1
        assert docs[0].format == "txt"
        assert (tmp_path / job_id).exists()

    # idempotency: identical bytes again → 0 added
    with (
        patch.object(ingest_module, "UPLOAD_DIR", tmp_path),
        patch.object(ingest_module, "enqueue_ingest_job", new=AsyncMock(return_value=True)),
    ):
        resp = await client.post(
            "/api/v1/ingest",
            data={"corpus": corpus},
            files={"files": ("hello.txt", content, "text/plain")},
        )
    assert resp.json()["files_added"] == 0

    await _cleanup(corpus)


async def test_ingest_rejects_unknown_type(client, require_db):
    resp = await client.post(
        "/api/v1/ingest",
        data={"corpus": "test"},
        files={"files": ("evil.exe", b"MZ", "application/octet-stream")},
    )
    assert resp.status_code == 400


async def test_job_status_404(client, require_db):
    resp = await client.get("/api/v1/ingest/does-not-exist")
    assert resp.status_code == 404
