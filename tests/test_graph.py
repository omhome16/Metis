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
    # unique entity names keep the seeded graph isolated from live data (the
    # live graph now has real "Gandhi" nodes that would saturate LIMIT 10)
    ent_a = _uniq("Ea")
    ent_b = _uniq("Eb")
    chunk_a = _uniq("ca")
    chunk_b = _uniq("cb")
    await store.upsert_document_graph(
        doc_id=_uniq("doc"),
        title="t",
        corpus="test-graph",
        chunks=[(chunk_a, f"{ent_a} marched to the sea.", 0), (chunk_b, f"{ent_b} wrote about {ent_a}.", 1)],
        entities=[{"name": ent_a, "type": "Person"}, {"name": ent_b, "type": "Person"}],
        relations=[],
    )
    ids = await store.neighbor_chunk_ids([ent_a], max_hops=1, limit=10)
    assert chunk_a in ids  # direct mention
    ids_2hop = await store.neighbor_chunk_ids([ent_a], max_hops=2, limit=10)
    assert chunk_b in ids_2hop  # via ent_a→ent_b RELATED_TO (co-occurrence) → chunk_b
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


async def test_entity_normalization_aliases(require_graph):
    """P2.2: 'Neo4j', 'neo4j' and 'Neo4j,' collapse onto ONE canonical node."""
    store = require_graph
    await store.init_schema()
    chunk = _uniq("chunk")
    await store.upsert_document_graph(
        doc_id=_uniq("doc"),
        title="t",
        corpus="test-graph",
        chunks=[(chunk, "Neo4j stores the graph. neo4j is fast.", 0)],
        entities=[
            {"name": "Neo4j", "type": "Technology"},
            {"name": "neo4j", "type": "Technology"},
            {"name": "Neo4j,", "type": "Technology"},
        ],
        relations=[],
    )
    async with store._driver.session() as session:
        rec = await (
            await session.run(
                "MATCH (e:Entity) WHERE e.canonical = 'neo4j' RETURN count(e) AS c"
            )
        ).single()
        assert rec["c"] == 1
        rec = await (
            await session.run(
                "MATCH (a:Alias) WHERE a.name IN ['neo4j', 'Neo4j,'] RETURN count(a) AS c"
            )
        ).single()
        assert rec["c"] == 2
    assert await store.neighbor_chunk_ids(["neo4j"], max_hops=1) == [chunk]


async def test_alias_merge_idempotent_reingest(require_graph):
    """P2.2: re-ingesting the same names never creates duplicate nodes/edges."""
    store = require_graph
    await store.init_schema()
    doc = _uniq("doc")
    chunk = _uniq("chunk")
    entities = [
        {"name": "MetisApp", "type": "Technology"},
        {"name": "GraphQL", "type": "Technology"},
    ]
    for _ in range(2):
        await store.upsert_document_graph(
            doc_id=doc,
            title="t",
            corpus="test-graph",
            chunks=[(chunk, "MetisApp uses GraphQL.", 0)],
            entities=entities,
            relations=[],
        )
    async with store._driver.session() as session:
        rec = await (
            await session.run(
                "MATCH (e:Entity {canonical: 'metisapp'}) RETURN count(e) AS c"
            )
        ).single()
        assert rec["c"] == 1
        rec = await (
            await session.run(
                "MATCH (a:Entity {canonical: 'metisapp'})-[r:RELATED_TO]->(b:Entity"
                " {canonical: 'graphql'}) "  # noqa: E501
                "RETURN count(r) AS c"
            )
        ).single()
        assert rec["c"] == 1
        # one MENTIONS edge from the chunk after re-ingest — no duplicate edges
        rec = await (
            await session.run(
                "MATCH (a:Entity {canonical: 'metisapp'})<-[:MENTIONS]-() RETURN count(*) AS c"
            )
        ).single()
        assert rec["c"] == 1
