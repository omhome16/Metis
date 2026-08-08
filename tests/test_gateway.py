import pytest

from app.core.config import Settings
from app.gateway.base import LLMClient
from app.gateway.gateway import LLMGateway, estimate_cost_usd


def _settings(**overrides) -> Settings:
    return Settings(**{"groq_api_key": "", "gemini_api_key": "", **overrides})


async def test_mock_chat():
    gw = LLMGateway(_settings())
    result = await gw.chat("generation", [{"role": "user", "content": "What is RAG?"}])
    assert result.text.startswith("[mock:")
    assert result.provider == "mock"
    assert result.usage["in"] >= 0


async def test_mock_chat_stream():
    gw = LLMGateway(_settings())
    tokens = [t async for t in gw.chat_stream("generation", [{"role": "user", "content": "hi"}])]
    assert tokens and "".join(tokens).startswith("[mock:")


async def test_mock_structured_entities():
    gw = LLMGateway(_settings())
    result = await gw.structured("extraction", [{"role": "user", "content": "extract entities from text"}], {})
    assert "entities" in result
    assert result["entities"][0]["name"]


class FailingProvider(LLMClient):
    name = "groq"

    async def chat(self, *args, **kwargs):
        raise RuntimeError("simulated outage")

    async def chat_stream(self, *args, **kwargs):
        raise RuntimeError("simulated outage")
        yield  # pragma: no cover

    async def structured(self, *args, **kwargs):
        raise RuntimeError("simulated outage")


async def test_fallback_chain_when_primary_fails():
    gw = LLMGateway(_settings(primary_provider="groq"), clients={"groq": FailingProvider()})
    result = await gw.chat("generation", [{"role": "user", "content": "hello"}])
    assert result.provider == "mock"


async def test_all_providers_fail_raises():
    gw = LLMGateway(_settings(primary_provider="groq"), clients={"groq": FailingProvider(), "mock": FailingProvider()})
    with pytest.raises(RuntimeError):
        await gw.chat("generation", [{"role": "user", "content": "hello"}])


def test_estimate_cost():
    assert estimate_cost_usd("llama-3.3-70b-versatile", {"in": 1_000_000, "out": 0}) == pytest.approx(0.59)
    assert estimate_cost_usd("unknown-model", {"in": 100, "out": 100}) == 0.0
