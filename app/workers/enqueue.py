"""Best-effort job enqueueing. Degrades gracefully when Redis is unavailable (dev)."""

from arq import create_pool

from app.core.logging import get_logger
from app.workers.settings import WorkerSettings

logger = get_logger(__name__)


async def enqueue_ingest_job(job_id: str) -> bool:
    try:
        redis = await create_pool(WorkerSettings.redis_settings)
        await redis.enqueue_job("process_ingest_job", job_id)
        await redis.aclose()
        return True
    except Exception as exc:
        logger.warning("queue unavailable — job %s stays queued in DB: %s", job_id, exc)
        return False
