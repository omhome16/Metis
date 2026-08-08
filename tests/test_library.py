"""Library features: cross-vault graph, journeys, surprises, entity search.

Seeds two test corpora sharing an entity so bridges and paths exist, and forces
LLM narration to fall back to templates (no network calls in tests). Entity names
are namespaced so they never merge with real vault entities.
"""

import uuid
from unittest.mock import patch

from app.graph.store import get_graph_store

NS = f"libtest-{uuid.uuid4().hex[:6]}"
P1, P2, P3 = f"{NS}-plato", f"{NS}-europe", f"{NS}-aristotle"


class NoLLMGateway:
    """Any LLM call fails → library narration falls back to templates."""

    async def structured(self, task, messages, json_schema):
        raise RuntimeError("no LLM in tests")

    async def chat(self, task, messages, temperature=0.7, max_tokens=None):
        raise RuntimeError("no LLM in tests")


async def _seed(store) -> None:
    """Two corpora, one shared entity (europe) → bridge + plato→aristotle path."""
    corpus_a, corpus_b = "Lib", "GraphVault"  # part of TEST_GRAPH_CORPORA (wiped around tests)
    await store.upsert_document_graph(
        doc_id=f"lib-doc-a-{uuid.uuid4().hex[:8]}",
        title="Plato's Republic (test)",
        corpus=corpus_a,
        chunks=[(f"chunk-a1-{uuid.uuid4().hex[:8]}", f"{P1} wrote about justice and the ideal city. {P2} has many traditions.", 0)],
        entities=[{"name": P1, "type": "Person"}, {"name": P2, "type": "Place"}, {"name": "Justice", "type": "Concept"}],
        relations=[],
    )
    await store.upsert_document_graph(
        doc_id=f"lib-doc-b-{uuid.uuid4().hex[:8]}",
        title="Aristotle's Ethics (test)",
        corpus=corpus_b,
        chunks=[(f"chunk-b1-{uuid.uuid4().hex[:8]}", f"{P3} studied ethics and {P2} spans many countries.", 0)],
        entities=[{"name": P3, "type": "Person"}, {"name": P2, "type": "Place"}, {"name": "Ethics", "type": "Concept"}],
        relations=[],
    )


async def test_library_graph_tags_corpora_and_bridges(require_graph):
    store = require_graph
    await _seed(store)
    data = await store.library_graph(node_limit=2000)  # low-degree test entities must survive the degree cutoff
    nodes = {n["name"]: n for n in data["nodes"] if n["label"] == "Entity"}
    assert P1 in nodes and nodes[P1]["corpora"] == ["Lib"]
    assert P3 in nodes and nodes[P3]["corpora"] == ["GraphVault"]
    assert set(nodes[P2]["corpora"]) == {"Lib", "GraphVault"}, "shared entity should span both vaults"
    docs = [n for n in data["nodes"] if n["label"] == "Document"]
    assert any(d["name"] == "Plato's Republic (test)" and d["corpus"] == "Lib" for d in docs)
    assert any(d["name"] == "Aristotle's Ethics (test)" and d["corpus"] == "GraphVault" for d in docs)


async def test_journey_finds_cross_vault_path(require_graph):
    store = require_graph
    await _seed(store)
    path = await store.journey(P1, P3)
    assert path is not None
    names = [n["name"] for n in path["nodes"]]
    assert names[0] == P1 and names[-1] == P3
    assert P2 in names, "path should pass through the shared bridge entity"
    by_name = {n["name"]: n for n in path["nodes"]}
    assert set(by_name[P2]["corpora"]) == {"Lib", "GraphVault"}


async def test_journey_no_path_returns_none(require_graph):
    store = require_graph
    await _seed(store)
    assert await store.journey(P1, f"{NS}-does-not-exist") is None


async def test_surprises_mines_shared_concepts(require_graph):
    store = require_graph
    await _seed(store)
    cards = await store.library_surprises(limit=10)
    shared = [c for c in cards if c["kind"] == "shared" and c["entity"] == P2]
    assert shared, "the shared test entity should surface as a surprise card"
    assert set(shared[0]["vaults"]) == {"Lib", "GraphVault"}


async def test_entity_search(require_graph):
    store = require_graph
    await _seed(store)
    results = await store.search_entities(P1, limit=5)
    assert any(r["name"] == P1 for r in results)


async def test_library_endpoints(client, require_graph):
    store = require_graph
    await _seed(store)
    with patch("app.api.routes.library.get_gateway", return_value=NoLLMGateway()):
        resp = await client.get("/api/v1/library/graph?node_limit=2000")
        assert resp.status_code == 200
        nodes = {n["name"] for n in resp.json()["nodes"]}
        assert {P1, P2, P3} <= nodes

        resp = await client.get(f"/api/v1/library/journey?from={P1}&to={P3}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["found"] is True
        assert [n["name"] for n in body["nodes"]] == [P1, P2, P3]
        assert body["narrative"], "template narrative should always exist"

        resp = await client.get("/api/v1/library/surprises")
        assert resp.status_code == 200
        cards = resp.json()["cards"]
        assert any(c["kind"] == "shared" and c["entity"] == P2 for c in cards)
        assert all(c.get("insight") for c in cards), "every card gets a (template) insight"

        resp = await client.get(f"/api/v1/library/entities?q={NS}")
        assert resp.status_code == 200
        assert any(e["name"] == P1 for e in resp.json()["entities"])
