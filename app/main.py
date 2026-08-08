"""METIS API entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import ask, cache, corpora, evals, graph, health, ingest, search
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.limits import RateLimiter, RateLimitMiddleware
from app.core.logging import get_logger, setup_logging
from app.db.session import engine

logger = get_logger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging(settings.log_level)
    logger.info("Starting %s (env=%s)", settings.app_name, settings.env)
    yield
    logger.info("Shutting down")
    await engine.dispose()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

# CORS is added last → outermost, so its headers are present even on 429s.
app.add_middleware(RateLimitMiddleware, limiter=RateLimiter(settings.rate_limit_max, settings.rate_limit_window))
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
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
