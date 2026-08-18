"""Automatic library reorganization (P8): the graph organizes itself.

After an ingest batch, `run_reorg_job` runs community detection (local — GDS or
LPA, no LLM), invalidates summaries of communities whose membership changed, and
re-summarizes only those (LLM spend bounded to deltas). Every run is recorded in
`reorg_runs` — debounce state AND the audit log surfaced at
`GET /api/v1/library/reorganizations`.

Debounce policy (runtime-settable): batch → every batch; debounced → ≥ min_docs
ingested since the last run OR > 24h elapsed; nightly → > 24h elapsed only.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.core.logging import get_logger
from app.core.runtime_settings import get_setting
from app.db.models import Document, IngestJob, ReorgRun
from app.db.session import async_session_factory
from app.gateway.gateway import get_gateway
from app.graph.communities import (
    detect_communities,
    invalidate_stale_summaries,
    summarize_communities,
)
from app.graph.store import get_graph_store

logger = get_logger(__name__)

_DEBOUNCE_HOURS = 24


def should_run(
    policy: str,
    docs_since_last: int,
    last_run_at: datetime | None,
    min_docs: int = 3,
    now: datetime | None = None,
) -> bool:
    """Debounce policy: batch → always; debounced → ≥ min_docs or > 24h; nightly → > 24h only."""
    if policy == "batch":
        return True
    if last_run_at is None:
        return True
    elapsed = (now or datetime.now(UTC)) - last_run_at
    if policy == "nightly":
        return elapsed >= timedelta(hours=_DEBOUNCE_HOURS)
    return docs_since_last >= min_docs or elapsed >= timedelta(hours=_DEBOUNCE_HOURS)


async def _docs_since(session, since: datetime | None) -> int:
    stmt = (
        select(func.count(Document.id))
        .join(IngestJob, Document.ingest_job_id == IngestJob.id)
        .where(IngestJob.status == "done")
    )
    if since is not None:
        stmt = stmt.where(IngestJob.created_at > since)
    return int((await session.execute(stmt)).scalar_one())


async def _last_run(session) -> datetime | None:
    row = (
        await session.execute(select(ReorgRun.run_at).order_by(ReorgRun.run_at.desc()).limit(1))
    ).first()
    return row[0] if row else None


async def _count_communities(store) -> int:
    try:
        async with store._driver.session() as session:
            rec = await (
                await session.run(
                    "MATCH (e:Entity) WHERE e.community_id IS NOT NULL "
                    "RETURN count(DISTINCT e.community_id) AS n"
                )
            ).single()
            return int(rec["n"]) if rec else 0
    except Exception:  # noqa: BLE001
        return 0


async def record_reorg_run(
    trigger: str,
    docs_since_last: int,
    communities_before: int,
    communities_after: int,
    summaries_made: int,
    detail: dict | None = None,
) -> None:
    """Write one `reorg_runs` row (shared by the auto job and the manual endpoint)."""
    try:
        async with async_session_factory() as session:
            session.add(
                ReorgRun(
                    triggered_by=trigger,
                    docs_since_last=docs_since_last,
                    communities_before=communities_before,
                    communities_after=communities_after,
                    summaries_made=summaries_made,
                    detail=detail,
                )
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — logging must never break a reorg
        logger.warning("reorg run record failed: %s", exc)


async def run_reorg_job(ctx: dict, trigger: str = "auto") -> dict:
    """arq job: debounce → detect → refresh stale summaries → record the run. Never raises."""
    store = get_graph_store()
    if not await store.ping():
        logger.warning("reorg skipped: Neo4j unreachable")
        return {"skipped": "neo4j-unreachable"}
    policy = str(await get_setting("graph.reorg_policy", "debounced"))
    min_docs = int(await get_setting("graph.reorg_min_docs", 3))
    try:
        async with async_session_factory() as session:
            last = await _last_run(session)
            docs_since = await _docs_since(session, last)
        if not should_run(policy, docs_since, last, min_docs):
            logger.info("reorg debounced (policy=%s, docs=%d)", policy, docs_since)
            return {"skipped": "debounced", "docs_since_last": docs_since, "policy": policy}
        before = await _count_communities(store)
        detected = await detect_communities(store)
        invalidated = await invalidate_stale_summaries(store)
        summarized = await summarize_communities(get_gateway(), store)
        after = await _count_communities(store)
        await record_reorg_run(
            trigger,
            docs_since,
            before,
            after,
            summarized.get("summaries", 0),
            {"detected": detected, "invalidated_summaries": invalidated},
        )
        logger.info(
            "reorg done: %d→%d communities, %d summaries, %d invalidated",
            before,
            after,
            summarized.get("summaries", 0),
            invalidated,
        )
        return {
            "ran": True,
            "communities_before": before,
            "communities_after": after,
            "summaries_made": summarized.get("summaries", 0),
            "invalidated_summaries": invalidated,
        }
    except Exception as exc:  # noqa: BLE001 — reorg must never break ingest
        logger.warning("reorg failed: %s", exc)
        return {"skipped": "error", "error": str(exc)}
