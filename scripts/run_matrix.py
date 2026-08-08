"""Config-matrix eval: compare retrieval configs on a golden dataset.

Usage: uv run python -m scripts.run_matrix tech   (or arts)
"""

import asyncio
import sys

from app.core.logging import setup_logging
from app.db.session import async_session_factory
from app.evals.runner import run_eval
from app.gateway.gateway import get_gateway

CONFIGS = [
    {"name": "hybrid+rerank+graph", "rerank_enabled": True, "graph_boost": True, "top_k_rerank": 5},
    {"name": "hybrid only", "rerank_enabled": False, "graph_boost": False, "top_k_rerank": 10},
    {"name": "vector only", "rerank_enabled": False, "graph_boost": False, "top_k_rerank": 10},
]


async def main() -> None:
    setup_logging("INFO")
    dataset_id = sys.argv[1] if len(sys.argv) > 1 else "tech"
    print(f"=== METIS eval matrix — dataset '{dataset_id}' ===\n")
    results = []
    async with async_session_factory() as session:
        gateway = get_gateway()
        for cfg in CONFIGS:
            report = await run_eval(session, gateway, dataset_id, {k: v for k, v in cfg.items() if k != "name"})
            m = report["metrics"]
            results.append((cfg["name"], m))
            print(
                f"{cfg['name']:<22} faith={m['faithfulness']:.3f} relev={m['answer_relevancy']:.3f} "
                f"prec={m['context_precision']:.3f} recall={m['context_recall']:.3f} "
                f"cite={m['citation_correctness']:.3f} p50={m['latency_p50']}s cost=${m['cost_total_usd']}"
            )
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
