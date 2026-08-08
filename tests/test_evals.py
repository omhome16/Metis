import uuid
from unittest.mock import patch

from sqlalchemy import delete, select

from app.db.models import Chunk, Document, EvalRun, GoldenQuestion
from app.db.session import async_session_factory
from app.evals.runner import run_eval
from app.rag.embeddings import get_embedder
from app.rag.retrieval import store_chunks


class EvalGateway:
    """Deterministic gateway: streaming chat + canned structured for every judge call."""

    async def chat_stream(self, task, messages, temperature=0.7, max_tokens=None):
        yield "The answer is grounded in the source [1]. "

    async def chat(self, task, messages, temperature=0.7, max_tokens=None):
        from app.gateway.base import ChatResult

        return ChatResult(text="The answer is grounded in the source [1].", model="mock")

    async def structured(self, task, messages, json_schema):
        joined = "\n".join(str(m.get("content", "")) for m in messages)
        if "Extract the atomic" in joined:
            return {"claims": ["The answer is grounded."]}
        if "Generate 3" in joined:
            return {"questions": ["What is the answer?", "Where is it grounded?"]}
        if "useful" in joined:
            return {"useful": True}
        if "present" in joined:
            return {"present": True}
        if "supported" in joined:
            return {"supported": True}
        if "entit" in joined:
            return {"entities": [{"name": "FastAPI", "type": "Technology"}], "relations": []}
        if "rewrite" in joined:
            return {"query": "Who created FastAPI"}
        if "contradict" in joined:
            return {"contradicts": False, "reason": ""}
        return {}


async def _seed_corpus(corpus: str) -> None:
    embedder = get_embedder()
    async with async_session_factory() as session:
        doc = Document(
            id=str(uuid.uuid4()), title="fastapi-notes", corpus=corpus, format="txt",
            content_hash=uuid.uuid4().hex, raw_text="FastAPI was created by Sebastián Ramírez in 2018.",
        )
        session.add(doc)
        await session.commit()
        chunks = ["FastAPI was created by Sebastián Ramírez in 2018.", "Pydantic powers validation."]
        embs = await embedder.embed_texts(chunks)
        await store_chunks(session, doc.id, chunks, embs)


async def _cleanup(corpus: str) -> None:
    async with async_session_factory() as session:
        await session.execute(delete(Chunk).where(Chunk.doc_id.in_(select(Document.id).where(Document.corpus == corpus))))
        await session.execute(delete(Document).where(Document.corpus == corpus))
        await session.execute(delete(GoldenQuestion).where(GoldenQuestion.corpus == corpus))
        await session.execute(delete(EvalRun))
        await session.commit()


async def test_run_eval_end_to_end(require_db):
    corpus = f"eval-{uuid.uuid4().hex[:8]}"
    await _seed_corpus(corpus)

    # dataset uses 'tech' default questions? No — we need questions for our corpus.
    # Seed a question manually so the dataset isn't empty.
    async with async_session_factory() as session:
        session.add(
            GoldenQuestion(
                corpus=corpus,
                question="Who created FastAPI?",
                ground_truth="Sebastián Ramírez created FastAPI in 2018.",
                source_hint="fastapi-notes",
            )
        )
        await session.commit()

    async with async_session_factory() as session:
        report = await run_eval(session, EvalGateway(), corpus, {"rerank_enabled": False, "graph_boost": False})
    assert report["questions"] == 1
    m = report["metrics"]
    for key in ("faithfulness", "answer_relevancy", "context_precision", "context_recall", "citation_correctness"):
        assert key in m and 0.0 <= m[key] <= 1.0
    assert m["latency_p50"] >= 0.0
    assert report["per_question"][0]["retrieved"] >= 1

    # EvalRun persisted
    async with async_session_factory() as session:
        runs = (await session.execute(select(EvalRun))).scalars().all()
        assert any(r.metrics.get("citation_correctness") is not None for r in runs)

    await _cleanup(corpus)


async def test_run_eval_unknown_dataset_404(client, require_db):
    resp = await client.post("/api/v1/evals/run", json={"dataset_id": "does-not-exist", "config": {}})
    assert resp.status_code == 404


async def test_evals_reports_endpoint(client, require_db):
    with patch("app.api.routes.evals.get_gateway", return_value=EvalGateway()):
        resp = await client.get("/api/v1/evals/reports")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
