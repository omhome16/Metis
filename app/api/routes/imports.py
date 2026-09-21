"""Text-source imports: URLs, Readwise highlights, Zotero libraries.

All three converge on one path: extract text → save as a `.txt` document →
enqueue the standard ingest job (chunk → embed → graph). No new pipelines.
`save_text_document` is the shared entrypoint (notes.py uses it too).

Connector credentials are supplied per request (never stored): Readwise wants
an account token, Zotero an API key + user id.
"""

import hashlib
import uuid
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, SessionDep, owned_vault
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Document, IngestJob, Vault
from app.schemas.api import IngestResponse
from app.workers.enqueue import enqueue_ingest_job

logger = get_logger(__name__)
router = APIRouter(prefix="/imports", tags=["imports"])

UPLOAD_DIR = Path(settings.upload_dir)
MAX_FETCH_BYTES = 5 * 1024 * 1024
FETCH_TIMEOUT = 20.0
_USER_AGENT = "Mozilla/5.0 (compatible; MetisKnowledgeLibrary/0.1)"


class UrlImportRequest(BaseModel):
    corpus: str = Field(..., min_length=1, max_length=128)
    url: str = Field(..., min_length=8, max_length=2048)


class ReadwiseImportRequest(BaseModel):
    corpus: str = Field(..., min_length=1, max_length=128)
    token: str = Field(..., min_length=8, max_length=256)
    max_books: int = Field(25, ge=1, le=100)


class ZoteroImportRequest(BaseModel):
    corpus: str = Field(..., min_length=1, max_length=128)
    api_key: str = Field(..., min_length=8, max_length=256)
    user_id: str = Field(..., min_length=1, max_length=64)
    max_items: int = Field(50, ge=1, le=200)


async def save_text_document(
    session: AsyncSession,
    corpus: str,
    title: str,
    text: str,
    source_url: str | None = None,
    fmt: str = "txt",
    tags: list[str] | None = None,
) -> tuple[str, bool]:
    """Persist a text blob as a Document + single-file IngestJob, enqueue it.

    Returns (job_id, created). Idempotent on content: identical text already
    stored (or twice in one batch) is skipped, mirroring file ingest.
    """
    digest = hashlib.sha256(text.encode()).hexdigest()
    existing = (
        await session.execute(select(Document).where(Document.content_hash == digest))
    ).scalar_one_or_none()
    if existing is not None:
        return existing.ingest_job_id or "", False

    await session.execute(
        pg_insert(Vault).values(name=corpus).on_conflict_do_nothing(index_elements=["name"])
    )
    job_id = str(uuid.uuid4())
    session.add(IngestJob(id=job_id, corpus=corpus))

    job_dir = UPLOAD_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    path = job_dir / f"{uuid.uuid4().hex}.{fmt}"
    path.write_text(text, encoding="utf-8")

    session.add(
        Document(
            id=str(uuid.uuid4()),
            title=title[:512],
            corpus=corpus,
            source_url=source_url,
            format=fmt,
            content_hash=digest,
            ingest_job_id=job_id,
            file_path=path.as_posix(),
            tags=tags or [],
        )
    )
    await session.commit()
    await enqueue_ingest_job(job_id)
    return job_id, True


def _extract_article(html: str) -> tuple[str, str]:
    """(title, text) from an HTML page — main/article/body, scripts stripped."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
        tag.decompose()
    title = (soup.title.get_text(strip=True) if soup.title else "")[:512]
    root = soup.find("article") or soup.find("main") or soup.body or soup
    text = root.get_text("\n", strip=True)
    return title, text


@router.post("/url", response_model=IngestResponse, status_code=202)
async def import_url(
    payload: UrlImportRequest,
    session: SessionDep,
    user: CurrentUser = None,
) -> IngestResponse:
    await owned_vault(session, user, payload.corpus)
    if not payload.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="url must start with http(s)://")
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=FETCH_TIMEOUT, headers={"User-Agent": _USER_AGENT}
        ) as client:
            resp = await client.get(payload.url)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"fetch failed: {exc}") from exc
    if len(resp.content) > MAX_FETCH_BYTES:
        raise HTTPException(status_code=413, detail="page too large to import")
    content_type = resp.headers.get("content-type", "")
    if "html" not in content_type and "text" not in content_type:
        raise HTTPException(status_code=422, detail=f"unsupported content-type: {content_type}")
    title, text = _extract_article(resp.text)
    if not text.strip():
        raise HTTPException(status_code=422, detail="no extractable text on that page")
    job_id, created = await save_text_document(
        session, payload.corpus, title or payload.url, text, source_url=str(resp.url)
    )
    return IngestResponse(job_id=job_id, status="queued", files_added=1 if created else 0)


async def _readwise_pages(client: httpx.AsyncClient, token: str, max_books: int) -> list[dict]:
    books: list[dict] = []
    page = 1
    while len(books) < max_books and page <= 10:
        resp = await client.get(
            "https://readwise.io/api/v2/books/",
            params={"page": page, "category": "books"},
            headers={"Authorization": f"Token {token}"},
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if not results:
            break
        books.extend(results)
        if not data.get("next"):
            break
        page += 1
    return books[:max_books]


@router.post("/readwise", response_model=IngestResponse, status_code=202)
async def import_readwise(
    payload: ReadwiseImportRequest,
    session: SessionDep,
    user: CurrentUser = None,
) -> IngestResponse:
    await owned_vault(session, user, payload.corpus)
    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as client:
            books = await _readwise_pages(client, payload.token, payload.max_books)
            added = 0
            for book in books:
                hl_resp = await client.get(
                    "https://readwise.io/api/v2/highlights/",
                    params={"book_id": book["id"], "page_size": 1000},
                    headers={"Authorization": f"Token {payload.token}"},
                )
                hl_resp.raise_for_status()
                highlights = hl_resp.json().get("results", [])[:500]
                if not highlights:
                    continue
                lines = [
                    f"# {book.get('title', 'Untitled')}",
                    f"Author: {book.get('author', 'Unknown')}",
                    "",
                ]
                for h in highlights:
                    body = (h.get("text") or "").strip()
                    if not body:
                        continue
                    loc = h.get("location")
                    suffix = f" (location {loc})" if isinstance(loc, int) else ""
                    lines.append(f"- {body}{suffix}")
                text = "\n".join(lines)
                if not text.strip():
                    continue
                _, created = await save_text_document(
                    session,
                    payload.corpus,
                    book.get("title") or "Untitled",
                    text,
                    source_url=book.get("source_url") or book.get("cover_image_url"),
                    tags=["readwise"],
                )
                if created:
                    added += 1
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Readwise API error: {exc}") from exc
    return IngestResponse(job_id="", status="queued", files_added=added)


@router.post("/zotero", response_model=IngestResponse, status_code=202)
async def import_zotero(
    payload: ZoteroImportRequest,
    session: SessionDep,
    user: CurrentUser = None,
) -> IngestResponse:
    await owned_vault(session, user, payload.corpus)
    headers = {"Zotero-API-Key": payload.api_key}
    base = f"https://api.zotero.org/users/{payload.user_id}"
    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, headers=headers) as client:
            resp = await client.get(f"{base}/items/top", params={"format": "json", "limit": 100})
            resp.raise_for_status()
            items = resp.json()[: payload.max_items]
            added = 0
            for item in items:
                data = item.get("data", {})
                title = (data.get("title") or data.get("name") or "").strip()
                if not title:
                    continue
                creators = ", ".join(
                    c.get("name") or " ".join(filter(None, [c.get("firstName"), c.get("lastName")]))
                    for c in data.get("creators", [])[:5]
                )
                parts = [f"# {title}"]
                if creators:
                    parts.append(f"Authors: {creators}")
                if data.get("date"):
                    parts.append(f"Date: {data['date']}")
                abstract = (data.get("abstractNote") or "").strip()
                if abstract:
                    parts += ["", abstract]
                if data.get("url"):
                    parts += ["", f"URL: {data['url']}"]
                text = "\n".join(parts)
                _, created = await save_text_document(
                    session,
                    payload.corpus,
                    title,
                    text,
                    source_url=data.get("url"),
                    tags=["zotero"],
                )
                if created:
                    added += 1
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Zotero API error: {exc}") from exc
    return IngestResponse(job_id="", status="queued", files_added=added)
