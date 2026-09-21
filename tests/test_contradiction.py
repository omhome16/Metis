import uuid

from app.db.models import Chunk, Document
from app.db.session import async_session_factory
from app.gateway.gateway import LLMGateway
from app.gateway.mock import MockProvider
from app.judgment import MockJudgmentClient, NoulAnswer
from app.rag.contradiction import check_contradiction, parse_citations
from app.rag.pipeline import contradiction_scan
from app.rag.retrieval import ChunkHit


def test_parse_citations():
    assert parse_citations("The answer is four [1][2] and also [7].") == {1, 2, 7}
    assert parse_citations("No citations here.") == set()
    # composite markers render as chips in the frontend — they must ground too
    assert parse_citations("Both agree [1, 2] and [3,4] here.") == {1, 2, 3, 4}


async def test_check_contradiction_mock():
    gw = LLMGateway(clients={"mock": MockProvider()})
    verdict = await check_contradiction(gw, "Apples are red.", "Oranges are orange.")
    assert "contradicts" in verdict
    assert verdict["contradicts"] is False


class ContradictingGateway:
    async def structured(self, task, messages, json_schema):
        return {"contradicts": True, "reason": "claims conflict"}


# ── judgment judge: a calibrated Noul instead of a parsed verdict ───────────


class RecordingJudgeGateway:
    """LLM judge that records whether it was reached at all."""

    def __init__(self):
        self.calls = 0

    async def structured(self, task, messages, json_schema):
        self.calls += 1
        return {"contradicts": True, "reason": "llm fallback used"}


async def _judged(monkeypatch, noul: float, gateway):
    from app.rag import contradiction as contra

    client = MockJudgmentClient({"contradicts": NoulAnswer(noul=noul)})
    monkeypatch.setattr(contra, "get_judgment_client", lambda: client)
    return await check_contradiction(gateway, "Earth is flat.", "Earth is round."), client


async def test_contradiction_judgment_flags_a_confident_contradiction(monkeypatch):
    verdict, client = await _judged(monkeypatch, 0.93, RecordingJudgeGateway())
    assert verdict["contradicts"] is True
    assert "0.93" in verdict["reason"]  # Jev has no explanation: the number is the reason
    assert len(client.calls) == 1


async def test_contradiction_judgment_clears_a_confident_agreement(monkeypatch):
    gateway = RecordingJudgeGateway()
    verdict, _client = await _judged(monkeypatch, 0.04, gateway)
    assert verdict["contradicts"] is False
    assert gateway.calls == 0  # confident judgment → the LLM judge is never asked


async def test_ambiguous_judgment_escalates_to_the_llm_judge(monkeypatch):
    """A Noul near 0.5 is similar probability either way — not a verdict."""
    gateway = RecordingJudgeGateway()
    verdict, _client = await _judged(monkeypatch, 0.52, gateway)
    assert gateway.calls == 1
    assert verdict["reason"] == "llm fallback used"


async def test_contradiction_falls_back_when_no_backend_is_configured(monkeypatch):
    from app.rag import contradiction as contra

    gateway = RecordingJudgeGateway()
    monkeypatch.setattr(contra, "get_judgment_client", lambda: None)
    verdict = await check_contradiction(gateway, "a", "b")
    assert gateway.calls == 1
    assert verdict["contradicts"] is True


async def test_contradiction_judgment_threshold_is_configurable(monkeypatch):
    """Policy is code: the same Noul flips verdicts when the threshold moves."""
    from app.core.config import get_settings
    from app.rag import contradiction as contra

    # 0.72 clears the uncertain band, so the verdict comes from the threshold
    # rather than escalating — which is the property under test.
    client = MockJudgmentClient({"contradicts": NoulAnswer(noul=0.72)})
    monkeypatch.setattr(contra, "get_judgment_client", lambda: client)
    settings = get_settings()
    monkeypatch.setattr(settings, "judgment_contradiction_threshold", 0.5, raising=False)
    strict = await check_contradiction(RecordingJudgeGateway(), "a", "b")
    monkeypatch.setattr(settings, "judgment_contradiction_threshold", 0.8, raising=False)
    lenient = await check_contradiction(RecordingJudgeGateway(), "a", "b")
    assert strict["contradicts"] is True
    assert lenient["contradicts"] is False


def _hit(cid: str, text: str) -> ChunkHit:
    return ChunkHit(
        chunk=Chunk(id=cid, doc_id="d", text=text, chunk_index=0, tokens=5),
        score=0.9,
        doc_title="t",
    )


async def test_contradiction_scan_emits_alert(require_graph):
    _store = require_graph
    await _store.init_schema()
    hits = [
        _hit(str(uuid.uuid4()), "Earth is flat."),
        _hit(str(uuid.uuid4()), "Earth is round."),
    ]
    alert = await contradiction_scan(ContradictingGateway(), hits)
    assert alert is not None
    assert alert["alert"] == "sources disagree"
    assert len(alert["chunks"]) == 2

    # second call: edge now exists → still flagged
    alert2 = await contradiction_scan(ContradictingGateway(), hits)
    assert alert2 is not None


async def test_contradiction_scan_single_hit():
    assert await contradiction_scan(ContradictingGateway(), [_hit("c1", "only one")]) is None


# ── P8: pairwise embedding pre-filter ────────────────────────────────────────


def _vec(seed: float, dim: int = 16) -> list[float]:
    v = [0.0] * dim
    v[0] = seed
    v[1] = (1.0 - seed * seed) ** 0.5
    return v


class CountingGateway:
    """Judge that records how many pairs actually reached the LLM."""

    def __init__(self):
        self.calls = 0

    async def structured(self, task, messages, json_schema):
        self.calls += 1
        return {"contradicts": False, "reason": "no conflict"}


async def test_contradiction_scan_judges_only_suspicious_band(require_db, require_graph):
    """P8: far-apart pairs (different subjects) must not cost an LLM judge call."""
    doc_id = str(uuid.uuid4())
    c1, c2, c3 = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    async with async_session_factory() as session:
        session.add(Document(id=doc_id, title="t", corpus="test-contra"))
        session.add(
            Chunk(
                id=c1,
                doc_id=doc_id,
                text="Earth is flat.",
                chunk_index=0,
                tokens=5,
                embedding=_vec(1.0),
            )
        )
        session.add(
            Chunk(
                id=c2,
                doc_id=doc_id,
                text="Earth is round.",
                chunk_index=1,
                tokens=5,
                embedding=_vec(0.9),
            )
        )
        session.add(
            Chunk(
                id=c3,
                doc_id=doc_id,
                text="Pasta is delicious.",
                chunk_index=2,
                tokens=5,
                embedding=_vec(0.1),
            )
        )
        await session.commit()
    hits = [
        ChunkHit(
            chunk=Chunk(id=c1, doc_id=doc_id, text="Earth is flat.", chunk_index=0, tokens=5),
            score=0.9,
            doc_title="t",
        ),
        ChunkHit(
            chunk=Chunk(id=c2, doc_id=doc_id, text="Earth is round.", chunk_index=1, tokens=5),
            score=0.8,
            doc_title="t",
        ),
        ChunkHit(
            chunk=Chunk(id=c3, doc_id=doc_id, text="Pasta is delicious.", chunk_index=2, tokens=5),
            score=0.7,
            doc_title="t",
        ),
    ]
    gw = CountingGateway()
    alert = await contradiction_scan(gw, hits)
    assert alert is None  # judge says no conflict
    assert gw.calls == 1  # only the (c1, c2) pair — c3 never reached the LLM

    await _cleanup_contra(doc_id)


async def test_contradiction_scan_skips_judge_when_band_clear(require_db, require_graph):
    """Embeddings complete + nothing in the suspicious band → zero judge calls.

    Different subjects must not burn judge quota just because a top-2 fallback
    exists; the fallback only applies when embeddings are unavailable.
    """
    doc_id = str(uuid.uuid4())
    c1, c2 = str(uuid.uuid4()), str(uuid.uuid4())
    async with async_session_factory() as session:
        session.add(Document(id=doc_id, title="t", corpus="test-contra"))
        session.add(
            Chunk(
                id=c1,
                doc_id=doc_id,
                text="Earth is flat.",
                chunk_index=0,
                tokens=5,
                embedding=_vec(1.0),
            )
        )
        session.add(
            Chunk(
                id=c2,
                doc_id=doc_id,
                text="Pasta is delicious.",
                chunk_index=1,
                tokens=5,
                embedding=_vec(0.1),
            )
        )
        await session.commit()
    hits = [
        ChunkHit(
            chunk=Chunk(id=c1, doc_id=doc_id, text="Earth is flat.", chunk_index=0, tokens=5),
            score=0.9,
            doc_title="t",
        ),
        ChunkHit(
            chunk=Chunk(id=c2, doc_id=doc_id, text="Pasta is delicious.", chunk_index=1, tokens=5),
            score=0.8,
            doc_title="t",
        ),
    ]
    gw = CountingGateway()
    assert await contradiction_scan(gw, hits) is None
    assert gw.calls == 0  # pre-filter cleared every pair → judge never invoked

    await _cleanup_contra(doc_id)


async def _cleanup_contra(doc_id: str) -> None:
    from sqlalchemy import delete

    async with async_session_factory() as session:
        await session.execute(delete(Chunk).where(Chunk.doc_id == doc_id))
        await session.execute(delete(Document).where(Document.id == doc_id))
        await session.commit()
