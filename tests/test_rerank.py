from app.db.models import Chunk
from app.rag.rerank import MockReranker
from app.rag.retrieval import ChunkHit


def _hit(cid: str, text: str) -> ChunkHit:
    return ChunkHit(chunk=Chunk(id=cid, doc_id="d", text=text, chunk_index=0, tokens=5), score=0.1, doc_title="t")


async def test_mock_reranker_orders_by_overlap():
    reranker = MockReranker()
    hits = [
        _hit("c1", "Completely unrelated sentence about weather."),
        _hit("c2", "FastAPI was created by Sebastián Ramírez in 2018."),
    ]
    ranked = await reranker.rerank("Who created FastAPI?", hits, top_k=5)
    assert [h.chunk.id for h in ranked] == ["c2", "c1"]


async def test_mock_reranker_top_k():
    reranker = MockReranker()
    hits = [_hit(f"c{i}", f"word{i} about fastapi") for i in range(10)]
    ranked = await reranker.rerank("fastapi", hits, top_k=3)
    assert len(ranked) == 3


async def test_mock_reranker_empty():
    assert await MockReranker().rerank("q", [], top_k=5) == []
