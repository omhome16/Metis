"""Eval runner (blueprint §12): golden dataset → per-question answers → metrics → EvalRun."""

import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import EvalRun
from app.evals import metrics
from app.evals.datasets import ensure_dataset_seeded, load_questions
from app.gateway.gateway import LLMGateway, estimate_cost_usd
from app.rag.chunking import count_tokens
from app.rag.context import assemble_context
from app.rag.embeddings import get_embedder
from app.rag.pipeline import retrieve_context

logger = get_logger(__name__)


async def answer_question(
    session: AsyncSession,
    gateway: LLMGateway,
    question: str,
    ground_truth: str,
    corpus: str | None,
    config: dict,
) -> dict:
    """Non-streaming ask used by the harness (retrieval + generation, no SSE)."""
    t0 = time.perf_counter()
    hits, _ = await retrieve_context(session, gateway, question, corpus, config=config)
    assembled = assemble_context(question, hits)
    try:
        result = await gateway.chat("generation", assembled.messages)
        answer = result.text
    except Exception as exc:  # noqa: BLE001
        answer = f"[generation failed: {exc}]"
    latency = time.perf_counter() - t0
    usage = {"in": count_tokens(assembled.user_text), "out": len(answer.split())}
    return {
        "question": question,
        "ground_truth": ground_truth,
        "answer": answer,
        "contexts": [h.chunk.text for h in hits],
        "context_ids": [h.chunk.id for h in hits],
        "latency": round(latency, 3),
        "cost_usd": estimate_cost_usd(settings.generation_model, usage),
        "retrieved": len(hits),
    }


async def run_eval(
    session: AsyncSession,
    gateway: LLMGateway,
    dataset_id: str,
    config: dict | None = None,
) -> dict:
    config = config or {}
    await ensure_dataset_seeded(session, dataset_id)
    questions = await load_questions(session, dataset_id)
    if not questions:
        raise ValueError(f"no golden questions for dataset '{dataset_id}'")

    embedder = get_embedder()
    per_question: list[dict] = []
    for q in questions:
        per_question.append(await answer_question(session, gateway, q.question, q.ground_truth, dataset_id, config))

    latencies = sorted(r["latency"] for r in per_question)
    n = len(latencies)
    citation_scores = [metrics.citation_correctness(r["answer"], r["context_ids"])[0] for r in per_question]

    report_metrics = {
        "faithfulness": await _avg_metric(lambda r: metrics.faithfulness(gateway, r["answer"], r["contexts"]), per_question),
        "answer_relevancy": await _avg_metric(
            lambda r: metrics.answer_relevancy(gateway, r["question"], r["answer"], embedder.embed_query), per_question
        ),
        "context_precision": await _avg_metric(lambda r: metrics.context_precision(gateway, r["question"], r["contexts"]), per_question),
        "context_recall": await _avg_metric(lambda r: metrics.context_recall(gateway, r["ground_truth"], r["contexts"]), per_question),
        "citation_correctness": _avg(citation_scores),
        "latency_p50": round(latencies[n // 2], 3) if n else 0.0,
        "latency_p95": round(latencies[min(n - 1, int(n * 0.95))], 3) if n else 0.0,
        "cost_total_usd": round(sum(r["cost_usd"] for r in per_question), 6),
    }

    run = EvalRun(id=str(uuid.uuid4()), config=config, metrics=report_metrics)
    session.add(run)
    await session.commit()

    return {
        "run_id": run.id,
        "dataset_id": dataset_id,
        "config": config,
        "questions": n,
        "metrics": report_metrics,
        "per_question": [
            {k: r[k] for k in ("question", "answer", "latency", "retrieved", "cost_usd")} for r in per_question
        ],
    }


async def _avg_metric(fn, rows: list[dict]) -> float:
    values = [await fn(r) for r in rows]
    return _avg(values)


def _avg(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0
