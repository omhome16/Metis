"""Vault API tests (migration 0004): CRUD, document library, graph export."""

import io

import pytest

from app.db.models import Chunk, Document, Vault
from app.db.session import async_session_factory


@pytest.fixture
async def clean_vaults(require_db):
    """Remove only test-created vaults/docs before and after — never touches real vaults."""
    async def _clean() -> None:
        from sqlalchemy import delete, or_

        async with async_session_factory() as s:
            test_names = ("Alpha", "Lib", "GraphVault", "AutoVault")
            await s.execute(
                delete(Document).where(or_(Document.corpus.in_(test_names), Document.corpus.like("test-%")))
            )
            await s.execute(
                delete(Vault).where(or_(Vault.name.in_(test_names), Vault.name.like("test-%")))
            )
            await s.commit()

    await _clean()
    yield
    await _clean()


async def test_vault_crud(client, clean_vaults):
    # create
    r = await client.post("/api/v1/vaults", json={"name": "Alpha", "description": "test vault", "color": "#2E6B4E"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Alpha"
    assert body["doc_count"] == 0

    # duplicate → 409
    r2 = await client.post("/api/v1/vaults", json={"name": "Alpha"})
    assert r2.status_code == 409

    # list contains it
    r3 = await client.get("/api/v1/vaults")
    names = [v["name"] for v in r3.json()]
    assert "Alpha" in names

    # patch
    r4 = await client.patch("/api/v1/vaults/Alpha", json={"description": "updated"})
    assert r4.status_code == 200
    assert r4.json()["description"] == "updated"

    # detail
    r5 = await client.get("/api/v1/vaults/Alpha")
    assert r5.json()["name"] == "Alpha"

    # delete
    r6 = await client.delete("/api/v1/vaults/Alpha")
    assert r6.status_code == 200
    r7 = await client.get("/api/v1/vaults/Alpha")
    assert r7.status_code == 404


async def test_document_library(client, require_db, require_graph, clean_vaults):
    await client.post("/api/v1/vaults", json={"name": "Lib"})

    # insert a document directly into the DB (as the worker would after indexing)
    doc_id = "doc-0001"
    async with async_session_factory() as s:
        s.add(
            Document(
                id=doc_id,
                title="FastAPI Notes",
                corpus="Lib",
                format="md",
                content_hash="hash-lib-1",
                raw_text="FastAPI is built on Starlette.",
                file_path="demo/fastapi-notes.md",
            )
        )
        for i, text in enumerate(["FastAPI is built on Starlette.", "It uses Pydantic models."]):
            s.add(Chunk(id=f"chunk-{doc_id}-{i}", doc_id=doc_id, text=text, chunk_index=i, tokens=5))
        await s.commit()

    # documents list shows the doc with chunk count + indexed status
    r = await client.get("/api/v1/vaults/Lib/documents")
    assert r.status_code == 200
    docs = r.json()
    assert len(docs) == 1
    d = docs[0]
    assert d["id"] == doc_id
    assert d["chunk_count"] == 2
    assert d["status"] == "indexed"

    # content
    r = await client.get(f"/api/v1/documents/{doc_id}/content")
    assert "Starlette" in r.json()["text"]

    # chunks
    r = await client.get(f"/api/v1/documents/{doc_id}/chunks")
    assert len(r.json()) == 2
    assert r.json()[0]["index"] == 0

    # file
    r = await client.get(f"/api/v1/documents/{doc_id}/file")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/")

    # delete document → cascades chunks
    r = await client.delete(f"/api/v1/documents/{doc_id}")
    assert r.status_code == 200
    async with async_session_factory() as s:
        remaining = (await s.execute(Chunk.__table__.select().where(Chunk.doc_id == doc_id))).scalars().all()
        assert len(remaining) == 0


async def test_vault_graph_export(client, require_db, require_graph, clean_vaults):
    await client.post("/api/v1/vaults", json={"name": "GraphVault"})

    # build a small graph directly
    from app.graph.store import get_graph_store

    store = get_graph_store()
    await store.upsert_document_graph(
        doc_id="gdoc-1",
        title="Graph Doc",
        corpus="GraphVault",
        chunks=[("gchunk-1", "Alpha relates to Beta and Gamma here.", 0)],
        entities=[{"name": "Alpha", "type": "Concept"}, {"name": "Beta", "type": "Concept"}],
        relations=[],
    )

    r = await client.get("/api/v1/vaults/GraphVault/graph")
    assert r.status_code == 200
    data = r.json()
    names = {n["name"] for n in data["nodes"]}
    assert "Alpha" in names
    assert "Beta" in names
    kinds = {e["kind"] for e in data["edges"]}
    assert kinds  # RELATED_TO and/or MENTIONS edges present
    assert any(e["source"] == "gdoc-1" for e in data["edges"])  # doc ↔ entity link

    # suggestions include entity questions
    r2 = await client.get("/api/v1/vaults/GraphVault/suggestions")
    assert r2.status_code == 200
    questions = r2.json()["questions"]
    assert any("Alpha" in q for q in questions)


async def test_ingest_creates_vault_row(client, clean_vaults, require_db):
    """Uploading to a corpus auto-creates the vault row (self-heal)."""
    r = await client.post(
        "/api/v1/ingest",
        data={"corpus": "AutoVault"},
        files={"files": ("hello.md", io.BytesIO(b"# Hello\n\nWorld."), "text/markdown")},
    )
    assert r.status_code == 202
    async with async_session_factory() as s:
        from sqlalchemy import select

        v = (await s.execute(select(Vault).where(Vault.name == "AutoVault"))).scalar_one_or_none()
        assert v is not None
