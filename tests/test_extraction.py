from app.gateway.gateway import LLMGateway
from app.gateway.mock import MockProvider
from app.graph.extraction import extract_entities, extract_entities_fallback


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
