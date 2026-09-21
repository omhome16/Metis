"""Vault-wide contradiction report (POST /vaults/{name}/contradiction-report).

The ask-time scan only sees the handful of chunks a question retrieved — a
disagreement between two documents nobody asks about together stays invisible.
This endpoint makes the differentiator a *product surface*: embed-pre-filtered
candidate pairs across the whole vault, judged by the same two-judge contract
as the live scan (Jev Noul when configured, LLM JSON otherwise), confirmed
pairs persisted to the graph so future ask-time scans know they exist.

Synchronous and capped (chunks/pairs) so a big vault costs minutes, not hours.
"""

from typing import Annotated

import numpy as np
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, owned_vault
from app.db.models import Chunk, Document
from app.db.session import get_session
from app.gateway.gateway import get_gateway
from app.graph.store import get_graph_store
from app.rag.contradiction import check_contradiction

router = APIRouter(prefix="/vaults", tags=["reports"])

# Scan budget: latest chunks per vault (children carry embeddings). 240 chunks
# → ≤ 28k pairs screened locally; only in-band pairs reach the judge.
MAX_CHUNKS = 240
MAX_JUDGED_PAIRS = 12
# Same "suspicious band" as the ask-time scan: same subject, not near-identical.
SIMILARITY_BAND = (0.70, 0.95)


class ContradictionPair(BaseModel):
    a: dict
    b: dict
    probability: float | None = None
    reason: str = ""


class ContradictionReport(BaseModel):
    checked_chunks: int = 0
    candidate_pairs: int = 0
    judged_pairs: int = 0
    flagged: list[ContradictionPair] = Field(default_factory=list)


def _top_pairs(chunks: list[Chunk], limit: int) -> list[tuple[Chunk, Chunk, float]]:
    """Cosine screen all pairs, return the most-alike in-band ones (cross-doc first)."""
    if len(chunks) < 2:
        return []
    matrix = np.asarray([c.embedding for c in chunks], dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    matrix /= norms
    sims = matrix @ matrix.T
    pairs: list[tuple[float, int, int]] = []
    for i in range(len(chunks)):
        for j in range(i + 1, len(chunks)):
            sim = float(sims[i, j])
            if SIMILARITY_BAND[0] <= sim <= SIMILARITY_BAND[1]:
                pairs.append((sim, i, j))
    pairs.sort(key=lambda p: -p[0])
    # Prefer pairs from different documents (the interesting kind), then fill
    # with same-document pairs up to the cap.
    cross = [(s, i, j) for s, i, j in pairs if chunks[i].doc_id != chunks[j].doc_id]
    same = [(s, i, j) for s, i, j in pairs if chunks[i].doc_id == chunks[j].doc_id]
    picked = (cross + same)[:limit]
    return [(chunks[i], chunks[j], s) for s, i, j in picked]


@router.post("/{name}/contradiction-report", response_model=ContradictionReport)
async def contradiction_report(
    name: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: CurrentUser = None,
) -> ContradictionReport:
    await owned_vault(session, user, name)
    rows = (
        await session.execute(
            select(Chunk, Document.title)
            .join(Document, Document.id == Chunk.doc_id)
            .where(Document.corpus == name, Chunk.embedding.is_not(None))
            .order_by(Document.ingested_at.desc(), Chunk.chunk_index)
            .limit(MAX_CHUNKS)
        )
    ).all()
    if len(rows) < 2:
        return ContradictionReport()

    chunks = [r[0] for r in rows]
    titles = {r[0].id: r[1] for r in rows}
    candidates = _top_pairs(chunks, MAX_JUDGED_PAIRS)
    report = ContradictionReport(checked_chunks=len(chunks), candidate_pairs=len(candidates))

    if not candidates:
        return report

    gateway = get_gateway()
    store = get_graph_store()
    graph_ok = False
    try:
        graph_ok = await store.ping()
    except Exception:  # noqa: BLE001 — graph persistence is best-effort
        graph_ok = False

    for a, b, sim in candidates:
        verdict = await check_contradiction(gateway, a.text, b.text)
        if not verdict.get("contradicts"):
            continue
        if graph_ok:
            try:
                await store.add_contradiction(a.id, b.id)
            except Exception:  # noqa: BLE001
                pass
        report.flagged.append(
            ContradictionPair(
                a={"chunk_id": a.id, "doc": titles.get(a.id, ""), "text": a.text[:400]},
                b={"chunk_id": b.id, "doc": titles.get(b.id, ""), "text": b.text[:400]},
                probability=round(float(verdict.get("probability", sim)), 3)
                if verdict.get("probability") is not None
                else None,
                reason=str(verdict.get("reason", ""))[:300],
            )
        )
    report.judged_pairs = len(candidates)
    return report
