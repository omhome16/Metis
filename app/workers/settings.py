"""arq worker configuration."""

from arq.connections import RedisSettings

from app.core.config import settings
from app.workers.ingest import process_ingest_job
from app.workers.reorg import run_reorg_job


class WorkerSettings:
    functions = [process_ingest_job, run_reorg_job]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    # Large batches (big PDFs, long texts) embed on CPU for many minutes — never
    # let arq's default 10-minute timeout kill a job mid-document.
    max_jobs = 2
    job_timeout = 3600

    async def on_shutdown(self, ctx):  # noqa: ANN001
        from app.graph.store import get_graph_store

        try:
            await get_graph_store().close()
        except Exception:  # noqa: BLE001
            pass
