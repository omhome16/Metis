"""Optional Langfuse tracing (blueprint §14 observability).

No-op when LANGFUSE keys are unset — the app never depends on Langfuse being up.
"""

from contextlib import nullcontext
from functools import lru_cache

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@lru_cache
def get_tracer():
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    try:
        from langfuse import Langfuse

        return Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host or "https://cloud.langfuse.com",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("langfuse init failed (tracing disabled): %s", exc)
        return None


def trace_span(tracer, name: str, input: dict | None = None):
    """Start a Langfuse span, or a nullcontext when tracing is disabled."""
    if tracer is None:
        return nullcontext()
    return tracer.start_span(name=name, input=input)


def flush_tracer(tracer) -> None:
    if tracer is not None:
        try:
            tracer.flush()
        except Exception:  # noqa: BLE001
            pass
