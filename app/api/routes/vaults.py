"""Vaults — named document libraries.

A vault is a corpus with presentation metadata (description, color). This router
provides the frontend with: vault CRUD + stats, per-vault document libraries,
document content/chunks/file access, the per-vault knowledge graph export, and
suggested questions built from the vault's top entities.
"""

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Chunk, Document, ImageRecord, IngestJob, Vault
from app.db.session import get_session
from app.graph.store import get_graph_store
from app.schemas.api import DocumentChunkOut, DocumentSummary, VaultCreate, VaultSummary, VaultUpdate

logger = get_logger(__name__)
router = APIRouter(tags=["vaults"])

_MIME_BY_EXT = {
    ".pdf": "application/pdf",
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
            await session.execute(select(Document.corpus, func.count(Document.id)).where(Document.corpus.in_(corpora)).group_by(Document.corpus))
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
async def list_vaults(session: AsyncSession = Depends(get_session)) -> list[VaultSummary]:
    vaults = (await session.execute(select(Vault).order_by(Vault.name))).scalars().all()
    # Self-heal: any corpus without a vault row gets one.
    missing = set(
        (await session.execute(select(Document.corpus).distinct())).scalars().all()
    ) - {v.name for v in vaults}
    for name in sorted(missing):
        await _ensure_vault_row(session, name)
    if missing:
        await session.commit()
        vaults = (await session.execute(select(Vault).order_by(Vault.name))).scalars().all()
    return await _summaries(session, list(vaults))


@router.post("/vaults", response_model=VaultSummary, status_code=201)
async def create_vault(payload: VaultCreate, session: AsyncSession = Depends(get_session)) -> VaultSummary:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="vault name cannot be empty")
    exists = (await session.execute(select(Vault).where(Vault.name == name))).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail=f"vault '{name}' already exists")
    vault = Vault(name=name, description=payload.description, color=payload.color)
    session.add(vault)
    await session.commit()
    return (await _summaries(session, [vault]))[0]


@router.patch("/vaults/{name}", response_model=VaultSummary)
async def update_vault(name: str, payload: VaultUpdate, session: AsyncSession = Depends(get_session)) -> VaultSummary:
    vault = (await session.execute(select(Vault).where(Vault.name == name))).scalar_one_or_none()
    if vault is None:
        raise HTTPException(status_code=404, detail="vault not found")
    if payload.description is not None:
        vault.description = payload.description
    if payload.color is not None:
        vault.color = payload.color
    await session.commit()
    return (await _summaries(session, [vault]))[0]


@router.delete("/vaults/{name}")
async def delete_vault(name: str, session: AsyncSession = Depends(get_session)) -> dict:
    vault = (await session.execute(select(Vault).where(Vault.name == name))).scalar_one_or_none()
    if vault is None:
        raise HTTPException(status_code=404, detail="vault not found")
    store = get_graph_store()
    try:
        if await store.ping():
            await store.delete_vault(name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("vault graph cleanup skipped: %s", exc)
    await session.execute(delete(Document).where(Document.corpus == name))  # chunks/images cascade
    await session.delete(vault)
    await session.commit()
    return {"deleted": name}


@router.get("/vaults/{name}", response_model=VaultSummary)
async def vault_detail(name: str, session: AsyncSession = Depends(get_session)) -> VaultSummary:
    vault = (await session.execute(select(Vault).where(Vault.name == name))).scalar_one_or_none()
    if vault is None:
        raise HTTPException(status_code=404, detail="vault not found")
    return (await _summaries(session, [vault]))[0]


@router.get("/vaults/{name}/documents", response_model=list[DocumentSummary])
async def vault_documents(name: str, session: AsyncSession = Depends(get_session)) -> list[DocumentSummary]:
    vault = (await session.execute(select(Vault).where(Vault.name == name))).scalar_one_or_none()
    if vault is None:
        raise HTTPException(status_code=404, detail="vault not found")
    docs = (
        (await session.execute(select(Document).where(Document.corpus == name).order_by(Document.ingested_at.desc())))
        .scalars()
        .all()
    )
    if not docs:
        return []

    job_ids = {d.ingest_job_id for d in docs if d.ingest_job_id}
    jobs: dict[str, IngestJob] = {}
    if job_ids:
        for job in (await session.execute(select(IngestJob).where(IngestJob.id.in_(job_ids)))).scalars():
            jobs[job.id] = job

    doc_ids = [d.id for d in docs]
    chunk_counts = dict(
        (
            await session.execute(select(Chunk.doc_id, func.count(Chunk.id)).where(Chunk.doc_id.in_(doc_ids)).group_by(Chunk.doc_id))
        ).all()
    )
    image_counts = dict(
        (
            await session.execute(
                select(ImageRecord.doc_id, func.count(ImageRecord.id)).where(ImageRecord.doc_id.in_(doc_ids)).group_by(ImageRecord.doc_id)
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
                ingested_at=d.ingested_at,
            )
        )
    return out


@router.get("/documents/recent", response_model=list[DocumentSummary])
async def recent_documents(limit: int = Query(12, ge=1, le=100), session: AsyncSession = Depends(get_session)) -> list[DocumentSummary]:
    docs = (
        (await session.execute(select(Document).order_by(Document.ingested_at.desc()).limit(limit))).scalars().all()
    )
    if not docs:
        return []
    doc_ids = [d.id for d in docs]
    chunk_counts = dict(
        (
            await session.execute(select(Chunk.doc_id, func.count(Chunk.id)).where(Chunk.doc_id.in_(doc_ids)).group_by(Chunk.doc_id))
        ).all()
    )
    image_counts = dict(
        (
            await session.execute(
                select(ImageRecord.doc_id, func.count(ImageRecord.id)).where(ImageRecord.doc_id.in_(doc_ids)).group_by(ImageRecord.doc_id)
            )
        ).all()
    )
    return [
        DocumentSummary(
            id=d.id,
            title=d.title,
            corpus=d.corpus,
            format=d.format,
            size=Path(d.file_path).stat().st_size if d.file_path and Path(d.file_path).exists() else 0,
            chunk_count=chunk_counts.get(d.id, 0),
            image_count=image_counts.get(d.id, 0),
            status="indexed" if (chunk_counts.get(d.id, 0) or (d.format == "image" and image_counts.get(d.id))) else "pending",
            ingested_at=d.ingested_at,
        )
        for d in docs
    ]


@router.get("/documents/{doc_id}", response_model=DocumentSummary)
async def document_detail(doc_id: str, session: AsyncSession = Depends(get_session)) -> DocumentSummary:
    d = await session.get(Document, doc_id)
    if d is None:
        raise HTTPException(status_code=404, detail="document not found")
    chunk_count = (
        await session.execute(select(func.count(Chunk.id)).where(Chunk.doc_id == doc_id))
    ).scalar_one()
    image_count = (
        await session.execute(select(func.count(ImageRecord.id)).where(ImageRecord.doc_id == doc_id))
    ).scalar_one()
    size = Path(d.file_path).stat().st_size if d.file_path and Path(d.file_path).exists() else 0
    return DocumentSummary(
        id=d.id, title=d.title, corpus=d.corpus, format=d.format, size=size,
        chunk_count=chunk_count or 0, image_count=image_count or 0, ingested_at=d.ingested_at,
    )


@router.get("/documents/{doc_id}/content")
async def document_content(doc_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    d = await session.get(Document, doc_id)
    if d is None:
        raise HTTPException(status_code=404, detail="document not found")
    return {"id": doc_id, "text": d.raw_text or ""}


@router.get("/documents/{doc_id}/chunks", response_model=list[DocumentChunkOut])
async def document_chunks(doc_id: str, session: AsyncSession = Depends(get_session)) -> list[DocumentChunkOut]:
    d = await session.get(Document, doc_id)
    if d is None:
        raise HTTPException(status_code=404, detail="document not found")
    rows = (
        (await session.execute(select(Chunk).where(Chunk.doc_id == doc_id).order_by(Chunk.chunk_index))).scalars().all()
    )
    return [DocumentChunkOut(id=r.id, index=r.chunk_index, text=r.text, tokens=r.tokens) for r in rows]


@router.get("/documents/{doc_id}/file")
async def document_file(doc_id: str, session: AsyncSession = Depends(get_session)) -> FileResponse:
    d = await session.get(Document, doc_id)
    if d is None or not d.file_path:
        raise HTTPException(status_code=404, detail="file not found")
    path = Path(d.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="file not found on disk")
    original = Path(d.file_path).name
    if d.source_url:
        stem = Path(d.source_url).stem or Path(original).stem
        original = f"{stem}{path.suffix}"
    return FileResponse(path, media_type=_mime_for(d.file_path), filename=original)


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    d = await session.get(Document, doc_id)
    if d is None:
        raise HTTPException(status_code=404, detail="document not found")
    store = get_graph_store()
    try:
        if await store.ping():
            await store.delete_document(doc_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("document graph cleanup skipped: %s", exc)
    await session.delete(d)  # chunks/images cascade via FK
    await session.commit()
    return {"deleted": doc_id}


@router.get("/vaults/{name}/graph")
async def vault_graph(name: str) -> dict:
    store = get_graph_store()
    if not await store.ping():
        raise HTTPException(status_code=503, detail="knowledge graph unavailable (Neo4j down?)")
    return await store.vault_graph(name)


@router.get("/vaults/{name}/suggestions")
async def vault_suggestions(name: str) -> dict:
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
