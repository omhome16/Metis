"""Product-surface routes: notes, contradiction report, export, scoped ask.

All Postgres-backed (DB-gated). These run against the mock embedder/gateway,
so they exercise the plumbing, not answer quality.
"""

import uuid


async def _mk_vault(client, name: str):
    assert (await client.post("/api/v1/vaults", json={"name": name})).status_code == 201


async def test_note_roundtrip(client, require_db):
    name = f"notes-{uuid.uuid4().hex[:8]}"
    await _mk_vault(client, name)
    r = await client.post(
        f"/api/v1/vaults/{name}/notes",
        json={"title": "Answer about consensus", "content": "Hobbes and Locke disagree on..."},
    )
    assert r.status_code == 202, r.text
    assert r.json()["files_added"] == 1
    # Idempotent: the same content again is a skip, not a duplicate document.
    r2 = await client.post(
        f"/api/v1/vaults/{name}/notes",
        json={"title": "Answer about consensus", "content": "Hobbes and Locke disagree on..."},
    )
    assert r2.status_code == 202
    assert r2.json()["files_added"] == 0
    # Unknown vault → 404.
    missing = await client.post(
        "/api/v1/vaults/does-not-exist/notes", json={"title": "x", "content": "y"}
    )
    assert missing.status_code == 404


async def test_contradiction_report_empty_vault(client, require_db):
    name = f"report-{uuid.uuid4().hex[:8]}"
    await _mk_vault(client, name)
    r = await client.post(f"/api/v1/vaults/{name}/contradiction-report")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["checked_chunks"] == 0
    assert body["flagged"] == []


async def test_export_zip(client, require_db):
    name = f"export-{uuid.uuid4().hex[:8]}"
    await _mk_vault(client, name)
    await client.post(
        f"/api/v1/vaults/{name}/notes", json={"title": "Note", "content": "Some content here"}
    )
    r = await client.get(f"/api/v1/vaults/{name}/export")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"].startswith("attachment")
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
    assert f"{name}/README.md" in names
    assert any("/documents/" in n for n in names)


async def test_url_import_rejects_bad_url(client, require_db):
    name = f"imports-{uuid.uuid4().hex[:8]}"
    await _mk_vault(client, name)
    r = await client.post(
        "/api/v1/imports/url", json={"corpus": name, "url": "ftp://not-http.example.com"}
    )
    assert r.status_code == 422
    # Unroutable host → the fetch fails cleanly with 502, never a 500.
    dead = await client.post(
        "/api/v1/imports/url", json={"corpus": name, "url": "http://127.0.0.1:1/page"}
    )
    assert dead.status_code == 502


async def test_ask_validates_document_selection(client, require_db):
    """document_ids outside the vault are a 422, never a silent empty answer."""
    name = f"scope-{uuid.uuid4().hex[:8]}"
    await _mk_vault(client, name)
    r = await client.post(
        "/api/v1/ask",
        json={
            "question": "What do the documents say about evidence?",
            "corpus": name,
            "document_ids": ["not-a-real-id"],
            "stream": False,
        },
    )
    assert r.status_code == 422
    assert "not in this vault" in r.json()["detail"]
