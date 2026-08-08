import uuid

from app.db.models import Chunk
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
    return ChunkHit(chunk=Chunk(id=cid, doc_id="d", text=text, chunk_index=0, tokens=5), score=0.9, doc_title="t")


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
