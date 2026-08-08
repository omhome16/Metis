"""METIS API entrypoint."""

import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Windows' mimetypes registry lacks .woff2 — register so @font-face loads.
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")

from app.api.routes import ask, cache, conversations, corpora, evals, graph, health, ingest, library, search, vaults
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.limits import RateLimiter, RateLimitMiddleware
from app.core.logging import get_logger, setup_logging
from app.db.session import engine
from app.graph.store import get_graph_store

logger = get_logger("app")
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)  # keep tests importable before the frontend ships


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging(settings.log_level)
    logger.info("Starting %s (env=%s)", settings.app_name, settings.env)
    try:
        await get_graph_store().init_schema()  # idempotent constraints
    except Exception as exc:  # noqa: BLE001
        logger.warning("graph schema init skipped: %s", exc)
    yield
    logger.info("Shutting down")
    await engine.dispose()
    try:
        await get_graph_store().close()  # close the Neo4j driver pool
    except Exception as exc:  # noqa: BLE001
        logger.warning("graph store close failed: %s", exc)


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

# CORS is added last → outermost, so its headers are present even on 429s.
app.add_middleware(RateLimitMiddleware, limiter=RateLimiter(settings.rate_limit_max, settings.rate_limit_window))
# allow_credentials is only valid with explicit origins — skip it for the wildcard dev default.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=settings.cors_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
register_exception_handlers(app)

app.include_router(health.router)
app.include_router(ingest.router, prefix=settings.api_prefix)
app.include_router(corpora.router, prefix=settings.api_prefix)
app.include_router(ask.router, prefix=settings.api_prefix)
app.include_router(search.router, prefix=settings.api_prefix)
app.include_router(graph.router, prefix=settings.api_prefix)
app.include_router(evals.router, prefix=settings.api_prefix)
app.include_router(cache.router, prefix=settings.api_prefix)
app.include_router(vaults.router, prefix=settings.api_prefix)
app.include_router(conversations.router, prefix=settings.api_prefix)
app.include_router(library.router, prefix=settings.api_prefix)

# Static frontend (SPA) — API routes above always win; unknown API paths 404.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/{full_path:path}", include_in_schema=False)
async def _spa(full_path: str):
    if full_path.startswith("api/") or full_path in {"docs", "openapi.json", "redoc"}:
        raise HTTPException(status_code=404, detail="not found")
    index = STATIC_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="frontend not built")
    return FileResponse(index)
