import uuid

from app.db.models import Chunk, Document
from app.db.session import async_session_factory
from app.gateway.gateway import LLMGateway
from app.gateway.mock import MockProvider
from app.rag.contradiction import check_contradiction, parse_citations
from app.rag.pipeline import contradiction_scan
from app.rag.retrieval import ChunkHit


def test_parse_citations():
    assert parse_citations("The answer is four [1][2] and also [7].") == {1, 2, 7}
    assert parse_citations("No citations here.") == set()


async def test_check_contradiction_mock():
    gw = LLMGateway(clients={"mock": MockProvider()})
    verdict = await check_contradiction(gw, "Apples are red.", "Oranges are orange.")
    assert "contradicts" in verdict
    assert verdict["contradicts"] is False


class ContradictingGateway:
    async def structured(self, task, messages, json_schema):
        return {"contradicts": True, "reason": "claims conflict"}


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
