from app.gateway.gateway import LLMGateway
from app.gateway.mock import MockProvider
from app.graph.extraction import (
    extract_document,
    extract_entities,
    extract_entities_fallback,
    normalize_entity_name,
)


def test_fallback_finds_capitalized_entities():
    text = (
        "Mahatma Gandhi led the Indian independence movement. Gandhi met Nehru in Delhi. "
        "Nehru became the first Prime Minister."
    )
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


# ── P8 tiered whole-document extraction ──────────────────────────────────────


async def test_extract_document_t1_covers_beyond_8000_chars():
    """T1 must extract entities from past the old 8000-char single-shot limit."""
    gw = LLMGateway(clients={"mock": MockProvider()})
    text = ("Alice and Bob met in London. " * 300) + ("Carol and Dan debated in Paris. " * 300)
    assert len(text) > 8000
    result = await extract_document(gw, text, mode="t1", use_llm=False)
    names = {e["name"] for e in result["entities"]}
    assert "Carol" in names and "Dan" in names  # beyond the old window
    assert "Alice" in names and "Bob" in names


async def test_extract_document_t1_dedupes_by_canonical():
    """Repeated entities across parent chunks must collapse onto one node."""
    gw = LLMGateway(clients={"mock": MockProvider()})
    text = "Hobbes and Locke discussed the State of Nature. " * 120
    result = await extract_document(gw, text, mode="t1", use_llm=False)
    keys = [normalize_entity_name(e["name"]) for e in result["entities"]]
    assert len(keys) == len(set(keys))  # no duplicate canonical keys
    assert "Hobbes" in {e["name"] for e in result["entities"]}


async def test_extract_document_t2_merges_llm_windows():
    """T2 = t1 coverage + LLM typed relations on sampled windows, merged."""
    gw = LLMGateway(clients={"mock": MockProvider()})
    text = "Socrates and Plato argued in Athens. " * 150
    result = await extract_document(gw, text, mode="t2", windows=3, use_llm=True)
    names = {e["name"] for e in result["entities"]}
    assert "Socrates" in names and "Plato" in names
    keys = [normalize_entity_name(e["name"]) for e in result["entities"]]
    assert len(keys) == len(set(keys))


async def test_extract_document_t2_without_llm_is_t1():
    """use_llm=False must never touch the gateway — pure local, full coverage."""

    class _NeverCalled:
        def structured(self, *a, **k):
            raise AssertionError("gateway must not be called when use_llm=False")

    gw = LLMGateway(clients={"mock": _NeverCalled()})  # type: ignore[arg-type]
    text = "Aristotle wrote about Ethics. " * 100
    result = await extract_document(gw, text, mode="t2", use_llm=False)
    assert any(e["name"] == "Aristotle" for e in result["entities"])


async def test_extract_document_t3_without_keys_falls_back_to_t1(monkeypatch):
    """t3 needs an API key — without one it degrades to local extraction."""
    from app.graph import extraction as mod

    monkeypatch.setattr(mod.settings, "groq_api_key", "")
    monkeypatch.setattr(mod.settings, "gemini_api_key", "")
    monkeypatch.setattr(mod.settings, "ollama_model", "")
    gw = LLMGateway(clients={"mock": MockProvider()})
    text = "Heraclitus and Parmenides disagreed about change. " * 80
    result = await extract_document(gw, text, mode="t3", use_llm=True)
    names = {e["name"] for e in result["entities"]}
    assert "Heraclitus" in names and "Parmenides" in names


async def test_extract_document_unknown_mode_falls_back_to_t1():
    gw = LLMGateway(clients={"mock": MockProvider()})
    text = "Zeno of Elea proposed paradoxes. " * 60
    result = await extract_document(gw, text, mode="t9", use_llm=False)
    assert any(e["name"] == "Zeno" for e in result["entities"])
