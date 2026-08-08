"""arq worker configuration."""

from arq.connections import RedisSettings

from app.core.config import settings
from app.workers.ingest import process_ingest_job


class WorkerSettings:
    functions = [process_ingest_job]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 10
    job_timeout = 600

    async def on_shutdown(self, ctx):  # noqa: ANN001
        from app.graph.store import get_graph_store

        try:
            await get_graph_store().close()
        except Exception:  # noqa: BLE001
            pass
