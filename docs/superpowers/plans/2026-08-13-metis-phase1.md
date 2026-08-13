# Metis 2026 Roadmap — Phase 1 Implementation Plan (Vector & Schema Foundations)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Metis a production-grade vector index (HNSW + halfvec), document metadata columns, and corpus versioning — the foundation for every later phase.

**Architecture:** Migration `0006` reshapes `chunks.embedding`/`images.embedding` to `halfvec` (variable-length — the 8-dim mock keeps working) and adds HNSW cosine indexes; session GUCs (`hnsw.ef_search`, `hnsw.iterative_scan`) set via an engine connect event; a `corpus_versions` table bumped on ingest success and vault delete; a metadata PATCH endpoint; corpus version surfaced on `/healthz` and the ask response header.

**Tech Stack:** SQLAlchemy 2 async, pgvector (0.8.x compose image), Alembic async migrations, FastAPI, pytest (DB-gated `require_db` fixtures).

## Global Constraints

- CPU-only torch; `transformers<5`; never touch model pins.
- `METIS_EMBED_DIM` default 1024; mock embedder is 8-dim — the `halfvec` column MUST stay variable-length (no `halfvec(1024)`).
- Every schema change = numbered alembic migration (next is `0006`).
- Tests hermetic: DB-dependent tests use the `require_db` fixture and skip when Postgres is down.
- `uv run ruff check .` + `ruff format --check .` must pass; `uv run pytest` green.
- API/SSE ask contract unchanged; all new behavior additive.

---

### Task 1: Migration `0006` — halfvec columns, HNSW indexes, metadata, corpus_versions

**Files:**
- Create: `alembic/versions/0006_vector_foundations.py`
- Create: `tests/test_migrations.py`
- Modify: `app/db/models.py`

**Interfaces:**
- Produces: `chunks.embedding`/`images.embedding` as `halfvec` with `ix_chunks_embedding_hnsw`/`ix_images_embedding_hnsw` HNSW indexes; `documents.tags ARRAY`, `documents.doc_date`, `documents.author`; table `corpus_versions(corpus PK, version, updated_at)`; ORM: `Document.tags/doc_date/author`, `CorpusVersion` model, `Chunk.embedding`/`ImageRecord.embedding` typed as `HalfVectorType` (imported with a `try/except` for the older `HalfVector` name).

- [ ] **Step 1: Write the failing migration smoke test**

```python
"""DB-gated smoke test: 0006 migration shape."""
import pytest
from sqlalchemy import text


@pytest.mark.require_db
async def test_halfvec_columns_and_indexes(client):
    from app.db.session import engine
    async with engine.connect() as conn:
        hnsw = (await conn.execute(
            text("SELECT count(*) FROM pg_indexes WHERE indexname IN "
                 "('ix_chunks_embedding_hnsw','ix_images_embedding_hnsw')")
        )).scalar()
        assert hnsw == 2
        # corpus_versions table exists and is empty
        row = (await conn.execute(
            text("SELECT version FROM corpus_versions WHERE corpus='smoke'")
        )).scalar_one_or_none()
        assert row is None
        # document metadata columns exist
        cols = set((await conn.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name='documents'")
        )).scalars())
        assert {"tags", "doc_date", "author"} <= cols
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_migrations.py -v`
Expected: FAIL (`corpus_versions` relation does not exist).

- [ ] **Step 3: Write migration `0006_vector_foundations.py`**

```python
"""vector foundations: halfvec + HNSW indexes, doc metadata, corpus_versions

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-13
"""
from alembic import op

revision: str = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # halfvec columns (variable-length: keeps mock 8-dim embeddings working)
    op.execute("ALTER TABLE chunks ALTER COLUMN embedding TYPE halfvec USING embedding::halfvec")
    op.execute("ALTER TABLE images ALTER COLUMN embedding TYPE halfvec USING embedding::halfvec")
    # HNSW cosine indexes
    op.execute("CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw ON chunks "
               "USING hnsw (embedding halfvec_cosine_ops) WITH (m = 16, ef_construction = 64)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_images_embedding_hnsw ON images "
               "USING hnsw (embedding halfvec_cosine_ops) WITH (m = 16, ef_construction = 64)")
    # document metadata
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{}'")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_date TIMESTAMPTZ")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS author VARCHAR(256)")
    # corpus versioning
    op.execute("CREATE TABLE IF NOT EXISTS corpus_versions ("
               "corpus VARCHAR(128) PRIMARY KEY, "
               "version INT NOT NULL DEFAULT 0, "
               "updated_at TIMESTAMPTZ NOT NULL DEFAULT now())")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS corpus_versions")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS author")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS doc_date")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS tags")
    op.execute("DROP INDEX IF EXISTS ix_images_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
    op.execute("ALTER TABLE images ALTER COLUMN embedding TYPE vector USING embedding::vector")
    op.execute("ALTER TABLE chunks ALTER COLUMN embedding TYPE vector USING embedding::vector")
```

- [ ] **Step 4: Update ORM models (`app/db/models.py`)**

```python
try:  # pgvector >= 0.3
    from pgvector.sqlalchemy import HALFVEC as HalfVectorType
except ImportError:  # older pgvector package
    from pgvector.sqlalchemy import HalfVector as HalfVectorType  # type: ignore[no-redef]
```

- `Chunk.embedding`: `Mapped[list[float] | None] = mapped_column(HalfVectorType)`
- `ImageRecord.embedding`: same
- `Document` gains: `tags: Mapped[list[str]] = mapped_column(ARRAY(String(128)), default=list)`, `doc_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))`, `author: Mapped[str | None] = mapped_column(String(256))`
- New model at the end:

```python
class CorpusVersion(Base):
    __tablename__ = "corpus_versions"
    corpus: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
```

- [ ] **Step 5: Run migration + tests**

Run: `uv run alembic upgrade head`
Run: `uv run pytest tests/test_migrations.py -v`
Expected: PASS. Then confirm the index is real: `EXPLAIN SELECT ... ORDER BY embedding <=> :q LIMIT 5` shows `hnsw`.

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/0006_vector_foundations.py app/db/models.py tests/test_migrations.py
git commit -m "feat(db): halfvec + HNSW indexes, doc metadata, corpus_versions (0006)"
```

---

### Task 2: Session GUCs — `hnsw.ef_search` + `hnsw.iterative_scan`

**Files:**
- Modify: `app/core/config.py` (after `cache_similarity_threshold`)
- Modify: `app/db/session.py`
- Test: `tests/test_session_gucs.py`

**Interfaces:**
- Produces: `settings.hnsw_ef_search: int = 120`, `settings.hnsw_iterative_scan: str = "relaxed_order"`; every new connection runs `SET hnsw.ef_search = <n>` + `SET hnsw.iterative_scan = <mode>`.

- [ ] **Step 1: Write the failing test**

```python
"""Session GUCs are applied to new connections."""
import pytest
from sqlalchemy import text


@pytest.mark.require_db
async def test_gucs_applied(client):
    from app.db.session import engine
    async with engine.connect() as conn:
        ef = (await conn.execute(text("SHOW hnsw.ef_search"))).scalar()
        scan = (await conn.execute(text("SHOW hnsw.iterative_scan"))).scalar()
    assert int(ef) >= 100
    assert scan == "relaxed_order"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_session_gucs.py -v`
Expected: FAIL (Postgres defaults `40` / `off`).

- [ ] **Step 3: Add settings**

In `app/core/config.py` after `cache_similarity_threshold`:

```python
    hnsw_ef_search: int = 120
    hnsw_iterative_scan: str = "relaxed_order"  # off | relaxed_order | strict_order
```

- [ ] **Step 4: Wire the engine connect event**

In `app/db/session.py` (import `event` from sqlalchemy):

```python
@event.listens_for(engine.sync_engine, "connect")
def _set_hnsw_gucs(dbapi_connection, _record):  # pragma: no cover - infra dependent
    cur = dbapi_connection.cursor()
    cur.execute(f"SET hnsw.ef_search = {int(settings.hnsw_ef_search)}")
    cur.execute(f"SET hnsw.iterative_scan = {settings.hnsw_iterative_scan}")
    cur.close()
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_session_gucs.py tests/test_migrations.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/core/config.py app/db/session.py tests/test_session_gucs.py
git commit -m "feat(db): set hnsw.ef_search and iterative_scan per connection"
```

---

### Task 3: Corpus version helpers + bumps

**Files:**
- Create: `app/db/versions.py`
- Modify: `app/workers/ingest.py` (bump on successful job with docs added)
- Modify: `app/api/routes/vaults.py` (`delete_vault` bump)
- Test: `tests/test_corpus_versions.py`

**Interfaces:**
- `async def bump_corpus_version(session, corpus: str) -> int` — pg upsert `ON CONFLICT (corpus) DO UPDATE SET version = corpus_versions.version + 1, updated_at = now() RETURNING version`; returns new version.
- `async def get_corpus_version(session, corpus: str) -> int` — returns current version or 0.
- Worker bump: in `ingest.py` worker, when a job completes successfully AND at least one document was created, bump the job's corpus. (Alternative accepted: bump after processing when `added > 0`.)

- [ ] **Step 1: Write the failing test**

```python
"""Corpus version bump/get helpers."""
import pytest
from sqlalchemy import select

from app.db.models import CorpusVersion
from app.db.session import async_session_factory
from app.db.versions import bump_corpus_version, get_corpus_version


@pytest.mark.require_db
async def test_bump_and_get(client):
    async with async_session_factory() as s:
        assert await get_corpus_version(s, "vtest") == 0
        assert await bump_corpus_version(s, "vtest") == 1
        assert await bump_corpus_version(s, "vtest") == 2
        assert await get_corpus_version(s, "vtest") == 2
        await s.execute(select(CorpusVersion).where(CorpusVersion.corpus == "vtest"))
        await s.commit()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_corpus_versions.py -v`
Expected: FAIL (`ModuleNotFoundError: app.db.versions`).

- [ ] **Step 3: Write `app/db/versions.py`**

Use `sqlalchemy.dialects.postgresql.insert` upsert with `on_conflict_do_update(index_elements=[CorpusVersion.corpus], set_={"version": CorpusVersion.version + 1, "updated_at": func.now()}).returning(CorpusVersion.version)`.

- [ ] **Step 4: Bump from the ingest worker**

Read `app/workers/ingest.py`; find where a job finishes with documents added. Insert:

```python
from app.db.versions import bump_corpus_version
# inside the worker, after the docs for a job are committed:
if added:
    new_version = await bump_corpus_version(session, corpus)
    logger.info("corpus %s bumped to version %s", corpus, new_version)
```

Do NOT bump when a job produced zero new documents (dedup skip).

- [ ] **Step 5: Bump from vault delete**

In `app/api/routes/vaults.py::delete_vault`, after the documents are deleted (and graph cleanup), call `await bump_corpus_version(session, name)` before commit. Add to the same test file: delete a vault (create via POST /vaults or direct ORM insert + documents), assert version increased.

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_corpus_versions.py -v`
Expected: PASS. Also run the full suite: `uv run pytest` — no regressions.

- [ ] **Step 7: Commit**

```bash
git add app/db/versions.py app/workers/ingest.py app/api/routes/vaults.py tests/test_corpus_versions.py
git commit -m "feat(db): corpus_versions bumped on ingest success and vault delete"
```

---

### Task 4: Metadata endpoint — `PATCH /api/v1/documents/{id}/metadata`

**Files:**
- Create: `app/api/routes/documents.py`
- Modify: `app/main.py` (include router)
- Modify: `app/schemas/api.py` (request/response models)
- Test: `tests/test_document_metadata.py`

**Interfaces:**
- Request body: `{"tags": ["deep-learning", "papers"], "doc_date": "2026-01-15", "author": "Jane Doe"}` — all fields optional; absent keys are NOT overwritten.
- Response: 200 with the full updated metadata; 404 if doc id unknown.
- New router registers under `/documents` with `tags=["documents"]`, wired into `app/main.py` next to the other routers.
- Schema: `DocumentMetaUpdate` (all optional), `DocumentMetaOut(tags, doc_date, author)`.

- [ ] **Step 1: Write the failing test**

```python
"""PATCH /documents/{id}/metadata updates only provided fields."""
import pytest


@pytest.mark.require_db
async def test_patch_metadata_partial(client, seeded_document):
    doc_id, _ = seeded_document  # fixture: ORM-inserted Document with empty tags
    r = await client.patch(f"/api/v1/documents/{doc_id}/metadata",
                           json={"author": "Ada Lovelace"})
    assert r.status_code == 200
    body = r.json()
    assert body["author"] == "Ada Lovelace"
    assert body["tags"] == []  # untouched

    r2 = await client.patch(f"/api/v1/documents/{doc_id}/metadata",
                            json={"tags": ["math"], "doc_date": "2026-01-15"})
    assert r2.status_code == 200
    assert r2.json()["tags"] == ["math"]
    assert r2.json()["doc_date"] == "2026-01-15"
    assert r2.json()["author"] == "Ada Lovelace"  # still untouched


@pytest.mark.require_db
async def test_patch_metadata_missing_doc_404(client):
    r = await client.patch("/api/v1/documents/nope/metadata", json={"author": "x"})
    assert r.status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_document_metadata.py -v`
Expected: FAIL (404/405 — route absent). Create the `seeded_document` fixture in the test file (insert `Document` via `async_session_factory`, yield `(id, session_cleanup)`).

- [ ] **Step 3: Implement the endpoint**

`documents.py`: GET-less router with only PATCH. Load the document by id → 404 if missing → apply only provided fields → commit → return `DocumentMetaOut`. Note `Document.tags` default is a list; merge = replace provided lists wholesale (not append).

- [ ] **Step 4: Wire into `app/main.py`** — import and `app.include_router(documents.router, prefix=settings.api_prefix)`.

- [ ] **Step 5: Run tests + ruff**

Run: `uv run pytest tests/test_document_metadata.py -v`; then `uv run ruff check app/api/routes/documents.py app/main.py app/schemas/api.py`.

- [ ] **Step 6: Commit**

```bash
git add app/api/routes/documents.py app/main.py app/schemas/api.py tests/test_document_metadata.py
git commit -m "feat(api): PATCH /documents/{id}/metadata for tags, date, author"
```

---

### Task 5: Surface corpus version on `/healthz` + ask response header

**Files:**
- Modify: `app/api/routes/health.py`
- Modify: `app/api/routes/ask.py` (response header)
- Test: `tests/test_corpus_versions.py` (extend) + `tests/test_health.py`

**Interfaces:**
- `/healthz` gains `"corpus_versions": {"default": 3, ...}` — latest version per corpus (empty dict when DB down or no corpora). `status` logic unchanged.
- `POST /api/v1/ask` responses include header `X-Metis-Corpus-Version: <int>` — the version of the corpus the ask ran against (0 when unknown).

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.require_db
async def test_healthz_includes_corpus_versions(client):
    r = await client.get("/api/v1/healthz")
    assert r.status_code == 200
    body = r.json()
    assert "corpus_versions" in body
    assert isinstance(body["corpus_versions"], dict)


@pytest.mark.require_db
async def test_ask_response_has_corpus_version_header(client):
    # seed a document in corpus "default", then ask (mock provider)
    r = await client.post("/api/v1/ask", json={"message": "hi", "corpus": "default"})
    assert r.status_code == 200
    assert "x-metis-corpus-version" in r.headers
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_corpus_versions.py tests/test_health.py -v`
Expected: FAIL (missing keys/headers).

- [ ] **Step 3: Implement**

- health.py: in `healthz`, if DB is up, `SELECT corpus, version FROM corpus_versions` → dict; else `{}`.
- ask.py: where the ask request is handled, resolve the corpus version once at the start (`get_corpus_version(session, corpus)`); set `response.headers["X-Metis-Corpus-Version"] = str(version)` on the final SSE/JSON response. For SSE, headers are set on the StreamingResponse creation. If corpus empty/unknown → `"0"`.
- Check how ask.py returns its response (SSE via StreamingResponse or plain JSON) and attach the header in BOTH paths if two exist.

- [ ] **Step 4: Run tests + full suite**

Run: `uv run pytest tests/test_corpus_versions.py tests/test_health.py -v`; then `uv run pytest` (no regressions).

- [ ] **Step 5: Commit**

```bash
git add app/api/routes/health.py app/api/routes/ask.py tests/test_corpus_versions.py tests/test_health.py
git commit -m "feat(api): surface corpus version on healthz and ask header"
```

---

### Task 6: Phase 1 verification — full gate

- [ ] **Step 1: Run the full verification gate**

```bash
uv run alembic upgrade head
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Expected: all green, ruff clean.

- [ ] **Step 2: Manual sanity check (DBs up)**

```bash
uv run uvicorn app.main:app --port 8011
```

- Upload a small PDF/md to a test corpus; `GET /api/v1/healthz` shows `corpus_versions`; `PATCH` metadata on the doc; `POST /api/v1/ask` returns the `X-Metis-Corpus-Version` header.

- [ ] **Step 3: Baseline eval numbers**

Run: `uv run python -m scripts.run_matrix tech` and record numbers in the plan's "Phase Result" section below (this becomes the comparison baseline for later phases).

- [ ] **Step 4: Commit any stragglers; mark this plan complete; update `docs/superpowers/plans/` README if one exists (index of plans).**

---

## Phase Result (fill at the end)

- Eval matrix (tech): faithfulness=?, context precision=?, citation correctness=?
- Migration applied cleanly: yes/no
- Notable decisions / deviations: ...

