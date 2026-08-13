"""Config-matrix eval: compare retrieval configs on a golden dataset.

Usage: uv run python -m scripts.run_matrix tech   (or Philosophy)

Each config in `CONFIGS` is passed to the retrieval pipeline as an eval override
(see `retrieve_context(..., config=...)` in app/rag/pipeline.py).

Thresholds (P6): enforced when Postgres is reachable, skipped otherwise
(skip-if-down semantics — the CI job starts DBs via compose services, so a
green PR must meet them there). Exits non-zero on any breach.
"""

import asyncio
import sys

from app.core.config import settings
from app.core.logging import setup_logging
from app.db.session import async_session_factory
from app.evals.runner import run_eval
from app.gateway.gateway import get_gateway

THRESHOLDS = {
    "faithfulness": 0.90,
    "context_precision": 0.80,
    "citation_correctness": 1.0,
}

CONFIGS = [
    {"name": "hybrid+rerank+graph", "rerank_enabled": True, "graph_boost": True, "top_k_rerank": 5},
    {"name": "hybrid only", "rerank_enabled": False, "graph_boost": False, "top_k_rerank": 10},
    {
        "name": "rerank only (no graph)",
        "rerank_enabled": True,
        "graph_boost": False,
        "top_k_rerank": 5,
    },
    # P3.1 comparison: parent-child (small-to-big) vs flat (kill switch). On
    # corpora ingested flat, parent_child is a no-op — re-ingest to compare.
    {
        "name": "parent-child",
        "rerank_enabled": True,
        "graph_boost": True,
        "top_k_rerank": 5,
        "parent_child": True,
    },
    {
        "name": "flat (no parent-child)",
        "rerank_enabled": True,
        "graph_boost": True,
        "top_k_rerank": 5,
        "parent_child": False,
    },
    # P3.2 comparison: metadata filters on/off.
    {
        "name": "metadata filter off",
        "rerank_enabled": True,
        "graph_boost": True,
        "top_k_rerank": 5,
        "metadata_filter": False,
    },
]


def _db_reachable() -> bool:
    import asyncpg

    async def _probe() -> bool:
        try:
            dsn = settings.db_url.replace("postgresql+asyncpg://", "postgresql://")
            conn = await asyncpg.connect(dsn, timeout=3)
            await conn.close()
            return True
        except Exception:
            return False

    try:
        return asyncio.run(_probe())
    except Exception:
        return False


async def main() -> None:
    setup_logging("INFO")
    if not _db_reachable():
        print("Postgres not reachable — skipping matrix (thresholds not enforced).")
        return
    dataset_id = sys.argv[1] if len(sys.argv) > 1 else "tech"
    print(f"=== METIS eval matrix — dataset '{dataset_id}' ===\n")
    failures: list[tuple[str, str, float | None, float]] = []
    async with async_session_factory() as session:
        gateway = get_gateway()
        for cfg in CONFIGS:
            report = await run_eval(
                session, gateway, dataset_id, {k: v for k, v in cfg.items() if k != "name"}
            )
            m = report["metrics"]
            line = (
                f"{cfg['name']:<22} faith={m['faithfulness']:.3f} "
                f"relev={m['answer_relevancy']:.3f} "
                f"prec={m['context_precision']:.3f} recall={m['context_recall']:.3f} "
                f"cite={m['citation_correctness']:.3f} "
                f"p50={m['latency_p50']}s cost=${m['cost_total_usd']}"
            )
            for metric, threshold in THRESHOLDS.items():
                value = m.get(metric)
                if value is None or value < threshold:
                    failures.append((cfg["name"], metric, value, threshold))
                    line += f"  [FAIL {metric} < {threshold}]"
            print(line)
    if failures:
        print("\nTHRESHOLDS NOT MET (fail PR):")
        for cfg_name, metric, value, threshold in failures:
            got = f"{value:.3f}" if value is not None else "n/a (judge unavailable)"
            print(f"  {cfg_name}: {metric} = {got} (required >= {threshold})")
        raise SystemExit(1)
    print("\nDone — all thresholds met.")


if __name__ == "__main__":
    asyncio.run(main())
