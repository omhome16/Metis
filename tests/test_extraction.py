from app.gateway.gateway import LLMGateway
from app.gateway.mock import MockProvider
from app.graph.extraction import extract_entities, extract_entities_fallback, normalize_entity_name


def test_fallback_finds_capitalized_entities():
    text = "Mahatma Gandhi led the Indian independence movement. Gandhi met Nehru in Delhi. Nehru became the first Prime Minister."
    result = extract_entities_fallback(text)
    names = [e["name"] for e in result["entities"]]
    assert "Gandhi" in names
    assert "Nehru" in names
    assert all(e["type"] for e in result["entities"])


def test_fallback_empty_text():
    assert extract_entities_fallback("")["entities"] == []
    assert extract_entities_fallback("lowercase only, no capitals here")["entities"] == []


async def test_extract_via_mock_gateway():
    gw = LLMGateway(clients={"mock": MockProvider()})
    result = await extract_entities(gw, "Mahatma Gandhi met Nehru in Delhi.")
    assert result["entities"]
    assert all("name" in e and "type" in e for e in result["entities"])


async def test_extract_with_llm_disabled_uses_regex():
    """P2.1: METIS_GRAPH_LLM_EXTRACT=false must not touch the gateway at all."""
    class _NeverCalled:
        def structured(self, *a, **k):
            raise AssertionError("gateway must not be called when use_llm=False")

    gw = LLMGateway(clients={"mock": _NeverCalled()})  # type: ignore[arg-type]
    result = await extract_entities(gw, "Metis uses Neo4j and Postgres.", use_llm=False)
    assert result["relations"] == []  # regex path emits no typed relations
    assert any(e["name"] == "Metis" for e in result["entities"])


def test_normalize_entity_name():
    assert normalize_entity_name("Neo4j") == "neo4j"
    assert normalize_entity_name("  neo4j  ") == "neo4j"
    assert normalize_entity_name("Neo4j,") == "neo4j"
    assert normalize_entity_name("Neo4j.") == "neo4j"
    assert normalize_entity_name("  C++ ") == "c++"
    assert normalize_entity_name("") == ""
    assert normalize_entity_name("multi   word Name") == "multi word name"
