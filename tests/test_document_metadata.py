"""PATCH /documents/{id}/metadata updates only the provided fields."""

import uuid

from sqlalchemy import delete

from app.db.models import Document
from app.db.session import async_session_factory


async def _seed_document(corpus: str = "default") -> str:
    doc_id = str(uuid.uuid4())
    async with async_session_factory() as s:
        s.add(
            Document(
                id=doc_id,
                title="Meta test",
                corpus=corpus,
                format="txt",
                content_hash=uuid.uuid4().hex,
            )
        )
        await s.commit()
    return doc_id


async def test_patch_metadata_partial(client, require_db):
    doc_id = await _seed_document()
    r = await client.patch(f"/api/v1/documents/{doc_id}/metadata", json={"author": "Ada Lovelace"})
    assert r.status_code == 200
    body = r.json()
    assert body["author"] == "Ada Lovelace"
    assert body["tags"] == []

    r2 = await client.patch(
        f"/api/v1/documents/{doc_id}/metadata",
        json={"tags": ["math"], "doc_date": "2026-01-15T00:00:00Z"},
    )
    assert r2.status_code == 200
    assert r2.json()["tags"] == ["math"]
    assert r2.json()["doc_date"] == "2026-01-15T00:00:00Z"
    assert r2.json()["author"] == "Ada Lovelace"

    async with async_session_factory() as s:
        await s.execute(delete(Document).where(Document.id == doc_id))
        await s.commit()


async def test_patch_metadata_missing_doc_404(client, require_db):
    r = await client.patch("/api/v1/documents/nope/metadata", json={"author": "x"})
    assert r.status_code == 404
