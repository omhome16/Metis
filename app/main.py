"""METIS API entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import corpora, health, ingest
from app.core.config import settings
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(ingest.router, prefix=settings.api_prefix)
app.include_router(corpora.router, prefix=settings.api_prefix)
