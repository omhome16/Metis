import uuid

import pytest

from app.graph.store import get_graph_store


def _uniq(suffix: str) -> str:
    return f"{suffix}-{uuid.uuid4().hex[:6]}"


async def test_schema_and_upsert(require_graph):
    store = require_graph
    await store.init_schema()
    doc_id = _uniq("doc")
    chunk_id = _uniq("chunk")
    await store.upsert_document_graph(
        doc_id=doc_id,
        title="History Notes",
        corpus="test-graph",
        chunks=[(chunk_id, "Mahatma Gandhi met Nehru in Delhi.", 0)],
        entities=[
            {"name": "Gandhi", "type": "Person"},
            {"name": "Nehru", "type": "Person"},
            {"name": "Delhi", "type": "Place"},
        ],
        relations=[],
    )
    # scoped to the test corpus — the live graph may hold real entities
    n_entities = await store.entity_count("test-graph")
    assert n_entities == 3
    async with store._driver.session() as session:
        rec = await (await session.run("MATCH (d:Document {corpus: 'test-graph'}) RETURN count(d) AS c")).single()
    assert rec["c"] == 1


async def test_neighbor_chunk_ids(require_graph):
    store = require_graph
    chunk_a = _uniq("ca")
    chunk_b = _uniq("cb")
    await store.upsert_document_graph(
        doc_id=_uniq("doc"),
        title="t",
        corpus="test-graph",
        chunks=[(chunk_a, "Gandhi marched to the sea.", 0), (chunk_b, "Nehru wrote about Gandhi.", 1)],
        entities=[{"name": "Gandhi", "type": "Person"}, {"name": "Nehru", "type": "Person"}],
        relations=[],
    )
    ids = await store.neighbor_chunk_ids(["Gandhi"], max_hops=1, limit=10)
    assert chunk_a in ids  # direct mention
    ids_2hop = await store.neighbor_chunk_ids(["Gandhi"], max_hops=2, limit=10)
    assert chunk_b in ids_2hop  # via Gandhi→Nehru RELATED_TO (co-occurrence) → chunk_b
    assert await store.neighbor_chunk_ids([], max_hops=1) == []


async def test_connections_shortest_path(require_graph):
    store = require_graph
    await store.upsert_document_graph(
        doc_id=_uniq("doc"),
        title="t",
        corpus="test-graph",
        chunks=[(_uniq("c1"), "Gandhi met Nehru.", 0), (_uniq("c2"), "Nehru met Patel.", 1)],
        entities=[
            {"name": "Gandhi", "type": "Person"},
            {"name": "Nehru", "type": "Person"},
            {"name": "Patel", "type": "Person"},
        ],
        relations=[],
    )
    paths = await store.connections("Gandhi", "Patel", max_paths=3)
    assert paths
    assert paths[0]["path"][0] == "Gandhi"
    assert paths[0]["path"][-1] == "Patel"


async def test_explore(require_graph):
    store = require_graph
    await store.upsert_document_graph(
        doc_id=_uniq("doc"),
        title="t",
        corpus="test-graph",
        chunks=[(_uniq("c1"), "Gandhi met Nehru in Delhi.", 0)],
        entities=[
            {"name": "Gandhi", "type": "Person"},
            {"name": "Nehru", "type": "Person"},
            {"name": "Delhi", "type": "Place"},
        ],
        relations=[],
    )
    neighbors = await store.explore("Gandhi", depth=2, limit=20)
    values = [n["value"] for n in neighbors]
    assert "Nehru" in values
