"""Background ingestion worker (arq): normalize, chunk, embed, and index documents."""

import base64
from pathlib import Path

from pypdf import PdfReader
from sqlalchemy import select

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Document, IngestJob
from app.db.session import async_session_factory
from app.db.versions import bump_corpus_version
from app.gateway.gateway import get_gateway
from app.graph.extraction import extract_entities
from app.graph.store import get_graph_store
from app.rag.chunking import chunk_into_parents, chunk_text
from app.rag.embeddings import get_embedder, get_image_embedder
from app.rag.retrieval import store_chunks, store_image, store_parents
from app.rag.vision import describe_image, mime_for_file

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
    """Arq job: extract → chunk → embed → persist chunks → build graph; per-file isolation."""
    embedder = get_embedder()
    gateway = get_gateway()
    graph = get_graph_store()
    graph_ok = await graph.ping()
    if not graph_ok:
        logger.warning("Neo4j unreachable — graph build skipped for job %s", job_id)

    async with async_session_factory() as session:
        job = await session.get(IngestJob, job_id)
        if job is None:
            logger.warning("job %s not found — skipping", job_id)
            return
        job.status = "running"
        await session.commit()

        docs = (
            (await session.execute(select(Document).where(Document.ingest_job_id == job_id))).scalars().all()
        )
        total = max(len(docs), 1)
        for i, doc in enumerate(docs):
            try:
                if doc.format == "image":
                    await _process_image(session, gateway, graph, graph_ok, doc)
                else:
                    await _process_text(session, gateway, graph, graph_ok, embedder, doc)
                job.progress = round(((i + 1) / total) * 100, 1)
            except Exception as exc:  # per-file error isolation
                job.per_file_errors[str(doc.id)] = f"{type(exc).__name__}: {exc}"
                logger.exception("failed to process %s", doc.file_path)
            await session.commit()

        job.status = "done" if not job.per_file_errors else "failed"
        if docs and not job.per_file_errors:
            await bump_corpus_version(session, job.corpus)
        await session.commit()
        logger.info("job %s finished: %d docs, errors=%s", job_id, len(docs), job.per_file_errors)


async def _process_text(session, gateway, graph, graph_ok, embedder, doc) -> None:
    """Extract → chunk (flat or parent-child) → embed → store → build document graph."""
    text = extract_text(doc.format, doc.file_path or "")
    doc.raw_text = text
    await session.commit()
    rows = await _build_chunks(session, embedder, doc, text)
    if not rows:
        return
    if graph_ok:
        extracted = await extract_entities(gateway, text, use_llm=settings.graph_llm_extract)
        await graph.upsert_document_graph(
            doc_id=doc.id,
            title=doc.title,
            corpus=doc.corpus,
            chunks=[(r.id, r.text, r.chunk_index) for r in rows],
            entities=extracted.get("entities", []),
            relations=extracted.get("relations", []),
        )


async def _build_chunks(session, embedder, doc, text: str) -> list:
    """Parent-child (P3.1) when enabled; flat chunks otherwise.

    Parents (~2000 chars, un-embedded) are stored first; their children
    (~400 chars) are embedded and point back via `parent_id`.
    """
    if not settings.parent_child:
        chunks = chunk_text(text, settings.chunk_size, settings.chunk_overlap)
        if not chunks:
            return []
        embeddings = await embedder.embed_texts(chunks)
        return await store_chunks(session, doc.id, chunks, embeddings)

    parents = chunk_into_parents(text, settings.parent_size)
    if not parents:
        return []
    per_parent_children: list[list[str]] = []
    start_indices: list[int] = []
    for parent in parents:
        children = chunk_text(parent, settings.child_size, settings.child_overlap)
        per_parent_children.append(children or [parent])
        start_indices.append(sum(len(g) for g in per_parent_children[:-1]))
    parent_rows = await store_parents(session, doc.id, parents, start_indices=start_indices)
    child_texts = [c for group in per_parent_children for c in group]
    parent_ids = [
        parent_rows[i].id for i, group in enumerate(per_parent_children) for _ in group
    ]
    embeddings = await embedder.embed_texts(child_texts)
    return await store_chunks(session, doc.id, child_texts, embeddings, parent_ids=parent_ids)


async def _process_image(session, gateway, graph, graph_ok, doc) -> None:
    """CLIP-embed the image, describe it with vision, store ImageRecord + graph node."""
    data = Path(doc.file_path).read_bytes()
    mime = mime_for_file(doc.file_path)
    embedding = await get_image_embedder().embed_image(data, mime)
    desc = await describe_image(gateway, base64.b64encode(data).decode(), mime)
    await store_image(session, doc.id, doc.file_path, desc["caption"], desc["tags"], embedding)
    if graph_ok:
        await graph.upsert_image(doc.id, doc.title, doc.corpus, desc["caption"], desc["tags"])
