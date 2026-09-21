"""Vaults — named document libraries.

A vault is a corpus with presentation metadata (description, color). This router
provides the frontend with: vault CRUD + stats, per-vault document libraries,
document content/chunks/file access, the per-vault knowledge graph export, and
suggested questions built from the vault's top entities.
"""

import io
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, owned_vault
from app.core.logging import get_logger
from app.db.models import Chunk, Document, ImageRecord, IngestJob, Vault
from app.db.session import get_session
from app.db.versions import bump_corpus_version
from app.graph.store import get_graph_store
from app.schemas.api import (
    DocumentChunkOut,
    DocumentSummary,
    VaultCreate,
    VaultSummary,
    VaultUpdate,
)

logger = get_logger(__name__)
router = APIRouter(tags=["vaults"])

_MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".epub": "application/epub+zip",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def _mime_for(path: str) -> str:
    return _MIME_BY_EXT.get(Path(path).suffix.lower(), "application/octet-stream")


async def _ensure_vault_row(session: AsyncSession, name: str) -> None:
    """Race-safe insert of a vault row (used when corpora appear without one)."""
    await session.execute(
        pg_insert(Vault).values(name=name).on_conflict_do_nothing(index_elements=["name"])
    )


async def _counts_by_corpus(session: AsyncSession, corpora: list[str]) -> tuple[dict, dict, dict]:
    """doc/chunk/image counts keyed by corpus, for the given corpus list."""
    if not corpora:
        return {}, {}, {}
    doc_counts = dict(
        (
            await session.execute(
                select(Document.corpus, func.count(Document.id))
                .where(Document.corpus.in_(corpora))
                .group_by(Document.corpus)
            )
        ).all()
    )
    chunk_counts = dict(
        (
            await session.execute(
                select(Document.corpus, func.count(Chunk.id))
                .join(Chunk, Chunk.doc_id == Document.id)
                .where(Document.corpus.in_(corpora))
                .group_by(Document.corpus)
            )
        ).all()
    )
    image_counts = dict(
        (
            await session.execute(
                select(Document.corpus, func.count(ImageRecord.id))
                .join(ImageRecord, ImageRecord.doc_id == Document.id)
                .where(Document.corpus.in_(corpora))
                .group_by(Document.corpus)
            )
        ).all()
    )
    return doc_counts, chunk_counts, image_counts


async def _graph_entity_counts(corpora: list[str]) -> dict[str, int]:
    store = get_graph_store()
    if not corpora:
        return {}
    try:
        if not await store.ping():
            return {}
        return {corpus: await store.entity_count(corpus) for corpus in corpora}
    except Exception as exc:  # noqa: BLE001
        logger.warning("entity counts skipped: %s", exc)
        return {}


async def _summaries(session: AsyncSession, vaults: list[Vault]) -> list[VaultSummary]:
    corpora = [v.name for v in vaults]
    doc_counts, chunk_counts, image_counts = await _counts_by_corpus(session, corpora)
    entity_counts = await _graph_entity_counts(corpora)
    return [
        VaultSummary(
            name=v.name,
            description=v.description,
            color=v.color,
            doc_count=doc_counts.get(v.name, 0),
            chunk_count=chunk_counts.get(v.name, 0),
            image_count=image_counts.get(v.name, 0),
            entity_count=entity_counts.get(v.name, 0),
            created_at=v.created_at,
        )
        for v in sorted(vaults, key=lambda x: (x.name or "").lower())
    ]


@router.get("/vaults", response_model=list[VaultSummary])
async def list_vaults(
    session: AsyncSession = Depends(get_session), user: CurrentUser = None
) -> list[VaultSummary]:
    stmt = select(Vault).order_by(Vault.name)
    if user is not None:
        stmt = stmt.where(Vault.owner_id == user.id)
    vaults = (await session.execute(stmt)).scalars().all()
    # Self-heal: any corpus without a vault row gets one. Users mode skips the
    # sweep — orphan adoption happens at registration, never per-request.
    if user is None:
        missing = set(
            (await session.execute(select(Document.corpus).distinct())).scalars().all()
        ) - {v.name for v in vaults}
        for name in sorted(missing):
            await _ensure_vault_row(session, name)
        if missing:
            await session.commit()
            vaults = (await session.execute(stmt)).scalars().all()
    return await _summaries(session, list(vaults))


@router.post("/vaults", response_model=VaultSummary, status_code=201)
async def create_vault(
    payload: VaultCreate,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = None,
) -> VaultSummary:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="vault name cannot be empty")
    exists = (await session.execute(select(Vault).where(Vault.name == name))).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail=f"vault '{name}' already exists")
    vault = Vault(
        name=name,
        description=payload.description,
        color=payload.color,
        owner_id=user.id if user is not None else None,
    )
    session.add(vault)
    await session.commit()
    return (await _summaries(session, [vault]))[0]


@router.patch("/vaults/{name}", response_model=VaultSummary)
async def update_vault(
    name: str,
    payload: VaultUpdate,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = None,
) -> VaultSummary:
    vault = await owned_vault(session, user, name)
    if payload.description is not None:
        vault.description = payload.description
    if payload.color is not None:
        vault.color = payload.color
    await session.commit()
    return (await _summaries(session, [vault]))[0]


@router.delete("/vaults/{name}")
async def delete_vault(
    name: str,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = None,
) -> dict:
    vault = await owned_vault(session, user, name)
    store = get_graph_store()
    try:
        if await store.ping():
            await store.delete_vault(name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("vault graph cleanup skipped: %s", exc)
    await session.execute(delete(Document).where(Document.corpus == name))  # chunks/images cascade
    await session.delete(vault)
    await bump_corpus_version(session, name)
    await session.commit()
    return {"deleted": name}


@router.get("/vaults/{name}", response_model=VaultSummary)
async def vault_detail(
    name: str,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = None,
) -> VaultSummary:
    vault = await owned_vault(session, user, name)
    return (await _summaries(session, [vault]))[0]


@router.get("/vaults/{name}/documents", response_model=list[DocumentSummary])
async def vault_documents(
    name: str,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = None,
) -> list[DocumentSummary]:
    await owned_vault(session, user, name)
    docs = (
        (
            await session.execute(
                select(Document)
                .where(Document.corpus == name)
                .order_by(Document.ingested_at.desc())
            )
        )
        .scalars()
        .all()
    )
    if not docs:
        return []

    job_ids = {d.ingest_job_id for d in docs if d.ingest_job_id}
    jobs: dict[str, IngestJob] = {}
    if job_ids:
        for job in (
            await session.execute(select(IngestJob).where(IngestJob.id.in_(job_ids)))
        ).scalars():
            jobs[job.id] = job

    doc_ids = [d.id for d in docs]
    chunk_counts = dict(
        (
            await session.execute(
                select(Chunk.doc_id, func.count(Chunk.id))
                .where(Chunk.doc_id.in_(doc_ids))
                .group_by(Chunk.doc_id)
            )
        ).all()
    )
    image_counts = dict(
        (
            await session.execute(
                select(ImageRecord.doc_id, func.count(ImageRecord.id))
                .where(ImageRecord.doc_id.in_(doc_ids))
                .group_by(ImageRecord.doc_id)
            )
        ).all()
    )

    out: list[DocumentSummary] = []
    for d in docs:
        job = jobs.get(d.ingest_job_id) if d.ingest_job_id else None
        failed = bool(job and job.per_file_errors and d.id in job.per_file_errors)
        chunk_count = chunk_counts.get(d.id, 0)
        image_count = image_counts.get(d.id, 0)
        if failed:
            status = "error"
        elif d.format == "image":
            status = "indexed" if image_count else "pending"
        else:
            status = "indexed" if chunk_count else "pending"
        size = 0
        if d.file_path:
            try:
                size = Path(d.file_path).stat().st_size
            except OSError:
                size = 0
        out.append(
            DocumentSummary(
                id=d.id,
                title=d.title,
                corpus=d.corpus,
                format=d.format,
                size=size,
                chunk_count=chunk_count,
                image_count=image_count,
                status=status,
                extraction_status=d.extraction_status,
                ingested_at=d.ingested_at,
            )
        )
    return out


@router.get("/documents/recent", response_model=list[DocumentSummary])
async def recent_documents(
    limit: int = Query(12, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = None,
) -> list[DocumentSummary]:
    stmt = select(Document).order_by(Document.ingested_at.desc()).limit(limit)
    if user is not None:
        owned = select(Vault.name).where(Vault.owner_id == user.id)
        stmt = (
            select(Document)
            .where(Document.corpus.in_(owned))
            .order_by(Document.ingested_at.desc())
            .limit(limit)
        )
    docs = (await session.execute(stmt)).scalars().all()
    if not docs:
        return []
    doc_ids = [d.id for d in docs]
    chunk_counts = dict(
        (
            await session.execute(
                select(Chunk.doc_id, func.count(Chunk.id))
                .where(Chunk.doc_id.in_(doc_ids))
                .group_by(Chunk.doc_id)
            )
        ).all()
    )
    image_counts = dict(
        (
            await session.execute(
                select(ImageRecord.doc_id, func.count(ImageRecord.id))
                .where(ImageRecord.doc_id.in_(doc_ids))
                .group_by(ImageRecord.doc_id)
            )
        ).all()
    )
    return [
        DocumentSummary(
            id=d.id,
            title=d.title,
            corpus=d.corpus,
            format=d.format,
            size=Path(d.file_path).stat().st_size
            if d.file_path and Path(d.file_path).exists()
            else 0,
            chunk_count=chunk_counts.get(d.id, 0),
            image_count=image_counts.get(d.id, 0),
            status="indexed"
            if (chunk_counts.get(d.id, 0) or (d.format == "image" and image_counts.get(d.id)))
            else "pending",
            extraction_status=d.extraction_status,
            ingested_at=d.ingested_at,
        )
        for d in docs
    ]


@router.get("/documents/{doc_id}", response_model=DocumentSummary)
async def document_detail(
    doc_id: str,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = None,
) -> DocumentSummary:
    d = await session.get(Document, doc_id)
    if d is None:
        raise HTTPException(status_code=404, detail="document not found")
    if user is not None:
        await owned_vault(session, user, d.corpus)
    chunk_count = (
        await session.execute(select(func.count(Chunk.id)).where(Chunk.doc_id == doc_id))
    ).scalar_one()
    image_count = (
        await session.execute(
            select(func.count(ImageRecord.id)).where(ImageRecord.doc_id == doc_id)
        )
    ).scalar_one()
    size = Path(d.file_path).stat().st_size if d.file_path and Path(d.file_path).exists() else 0
    return DocumentSummary(
        id=d.id,
        title=d.title,
        corpus=d.corpus,
        format=d.format,
        size=size,
        chunk_count=chunk_count or 0,
        image_count=image_count or 0,
        extraction_status=d.extraction_status,
        ingested_at=d.ingested_at,
    )


@router.get("/documents/{doc_id}/content")
async def document_content(
    doc_id: str,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = None,
) -> dict:
    d = await session.get(Document, doc_id)
    if d is None:
        raise HTTPException(status_code=404, detail="document not found")
    if user is not None:
        await owned_vault(session, user, d.corpus)
    return {"id": doc_id, "text": d.raw_text or ""}


@router.get("/documents/{doc_id}/chunks", response_model=list[DocumentChunkOut])
async def document_chunks(
    doc_id: str,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = None,
) -> list[DocumentChunkOut]:
    d = await session.get(Document, doc_id)
    if d is None:
        raise HTTPException(status_code=404, detail="document not found")
    if user is not None:
        await owned_vault(session, user, d.corpus)
    rows = (
        (
            await session.execute(
                select(Chunk).where(Chunk.doc_id == doc_id).order_by(Chunk.chunk_index)
            )
        )
        .scalars()
        .all()
    )
    return [
        DocumentChunkOut(id=r.id, index=r.chunk_index, text=r.text, tokens=r.tokens) for r in rows
    ]


@router.get("/documents/{doc_id}/file")
async def document_file(
    doc_id: str,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = None,
) -> FileResponse:
    d = await session.get(Document, doc_id)
    if d is None or not d.file_path:
        raise HTTPException(status_code=404, detail="file not found")
    if user is not None:
        await owned_vault(session, user, d.corpus)
    path = Path(d.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="file not found on disk")
    original = Path(d.file_path).name
    if d.source_url:
        stem = Path(d.source_url).stem or Path(original).stem
        original = f"{stem}{path.suffix}"
    return FileResponse(path, media_type=_mime_for(d.file_path), filename=original)


@router.delete("/documents/{doc_id}")
async def delete_document(
    doc_id: str,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = None,
) -> dict:
    d = await session.get(Document, doc_id)
    if d is None:
        raise HTTPException(status_code=404, detail="document not found")
    if user is not None:
        await owned_vault(session, user, d.corpus)
    store = get_graph_store()
    try:
        if await store.ping():
            await store.delete_document(doc_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("document graph cleanup skipped: %s", exc)
    await session.delete(d)  # chunks/images cascade via FK
    await bump_corpus_version(session, d.corpus)
    await session.commit()
    return {"deleted": doc_id}


@router.get("/vaults/{name}/graph")
async def vault_graph(
    name: str,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = None,
) -> dict:
    await owned_vault(session, user, name)
    store = get_graph_store()
    if not await store.ping():
        raise HTTPException(status_code=503, detail="knowledge graph unavailable (Neo4j down?)")
    return await store.vault_graph(name)


@router.get("/vaults/{name}/suggestions")
async def vault_suggestions(
    name: str,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = None,
) -> dict:
    await owned_vault(session, user, name)
    store = get_graph_store()
    entities: list[str] = []
    try:
        if await store.ping():
            data = await store.vault_graph(name, node_limit=8, edge_limit=0)
            entities = [n["name"] for n in data["nodes"] if n["label"] == "Entity"][:6]
    except Exception as exc:  # noqa: BLE001
        logger.warning("suggestions failed: %s", exc)
    questions: list[str] = []
    for e in entities:
        questions.append(f"What is {e}?")
    for e in entities:
        questions.append(f"How does {e} relate to the rest of this vault?")
    questions.extend(
        [
            "Summarize the key ideas in this vault.",
            "What are the main relationships between concepts here?",
        ]
    )
    return {"vault": name, "questions": questions[:6]}


@router.get("/vaults/{name}/export")
async def export_vault(
    name: str,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = None,
) -> Response:
    """Obsidian-friendly export: one markdown file per document (frontmatter),
    plus graph entities/relations when Neo4j is reachable."""
    await owned_vault(session, user, name)
    docs = (
        (
            await session.execute(
                select(Document).where(Document.corpus == name).order_by(Document.ingested_at.asc())
            )
        )
        .scalars()
        .all()
    )

    def _slug(title: str, doc_id: str) -> str:
        base = re.sub(r"[^\w\- ]+", "", title).strip()[:80] or doc_id[:8]
        return base

    buffer = io.BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            f"{name}/README.md",
            f"# {name}\n\nExported from Metis on {datetime.now(UTC).date()}. "
            f"{len(docs)} documents. Each file below is a source document with "
            "YAML frontmatter (title, author, date, source URL, tags).",
        )
        for d in docs:
            slug = _slug(d.title, d.id)
            counter = 2
            while slug in used:
                slug = f"{_slug(d.title, d.id)} {counter}"
                counter += 1
            used.add(slug)
            fm = [
                "---",
                f"title: {d.title}",
                f"vault: {d.corpus}",
                f"format: {d.format}",
                f"ingested: {d.ingested_at.date().isoformat() if d.ingested_at else ''}",
            ]
            if d.author:
                fm.append(f"author: {d.author}")
            if d.source_url:
                fm.append(f"source: {d.source_url}")
            if d.tags:
                fm.append(f"tags: [{', '.join(d.tags)}]")
            fm += ["---", ""]
            body = (d.raw_text or "").strip() or "_(no extracted text)_"
            zf.writestr(f"{name}/documents/{slug}.md", "\n".join(fm) + "\n" + body + "\n")

        # Graph companion files (best-effort — export must survive Neo4j down).
        try:
            store = get_graph_store()
            if await store.ping():
                graph = await store.vault_graph(name)
                entity_lines = [f"# Entities in {name}", ""]
                for node in graph.get("nodes", []):
                    if node.get("label") == "Entity":
                        entity_lines.append(f"- {node.get('name', '')}")
                zf.writestr(f"{name}/graph/entities.md", "\n".join(entity_lines) + "\n")
                rel_lines = [f"# Relations in {name}", ""]
                for edge in graph.get("edges", []):
                    rel_lines.append(
                        f"- {edge.get('source', '')} —[{edge.get('type', '')}]→ {edge.get('target', '')}"
                    )
                zf.writestr(f"{name}/graph/relations.md", "\n".join(rel_lines) + "\n")
        except Exception as exc:  # noqa: BLE001 — export must survive graph downtime
            logger.warning("graph export skipped: %s", exc)

    buffer.seek(0)
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}-metis-export.zip"'},
    )
