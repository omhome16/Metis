"""P6: ollama provider — OpenAI-compatible client, tested with httpx MockTransport (no server)."""

import json

import httpx
from httpx import Request, Response

from app.core.config import Settings
from app.gateway.gateway import LLMGateway
from app.gateway.ollama import OllamaProvider


def _handler(content: str = "hello from ollama") -> httpx.MockTransport:
    def handle(request: Request) -> Response:
        payload = json.loads(request.content)
        if payload.get("stream"):
            chunks = [
                {
                    "id": "1",
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {"content": "hel"}, "finish_reason": None}],
                },
                {
                    "id": "1",
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {"content": "lo"}, "finish_reason": None}],
                },
                {
                    "id": "1",
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                },
            ]
            body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
            return Response(200, text=body, headers={"content-type": "text/event-stream"})
        return Response(
            200,
            json={
                "id": "cmpl-1",
                "choices": [{"message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 3},
            },
        )

    return httpx.MockTransport(handle)


def _settings(**overrides) -> Settings:
    defaults = dict(groq_api_key="", gemini_api_key="", ollama_model="qwen3:8b")
    defaults.update(overrides)
    return Settings(**defaults)


def _provider(settings: Settings, handler: httpx.MockTransport) -> OllamaProvider:
    return OllamaProvider(settings, transport=handler)


async def test_ollama_chat():
    provider = _provider(_settings(), _handler("hello from ollama"))
    result = await provider.chat(
        [{"role": "user", "content": "hi"}], model="qwen3:8b", temperature=0.3, max_tokens=64
    )
    assert result.text == "hello from ollama"
    assert result.provider == "ollama"
    assert result.usage == {"in": 11, "out": 3}


async def test_ollama_chat_stream():
    provider = _provider(_settings(), _handler())
    parts = [
        p async for p in provider.chat_stream([{"role": "user", "content": "hi"}], model="qwen3:8b")
    ]
    assert "".join(parts) == "hello"


async def test_ollama_structured():
    provider = _provider(_settings(), _handler('{"ok": true}'))
    out = await provider.structured(
        [{"role": "user", "content": "x"}], model="qwen3:8b", json_schema={}
    )
    assert out == {"ok": True}


async def test_ollama_supports_tools_config():
    no_tools = _provider(_settings(ollama_tools=False), _handler())
    assert no_tools.supports_tools is False
    with_tools = _provider(_settings(ollama_tools=True), _handler())
    assert with_tools.supports_tools is True


async def test_gateway_routes_generation_to_ollama():
    """Task-routed: with no groq/gemini keys, an injected ollama client serves generation."""
    settings = _settings()
    provider = _provider(settings, _handler("local answer"))
    gateway = LLMGateway(settings=settings, clients={"ollama": provider})
    result = await gateway.chat("generation", [{"role": "user", "content": "q"}])
    assert result.text == "local answer"
    assert result.provider == "ollama"
    # _model_for passes the configured ollama model for every task
    assert gateway._model_for("ollama", "generation") == "qwen3:8b"
    assert gateway._model_for("ollama", "judge") == "qwen3:8b"


async def test_gateway_mock_stays_default_without_keys():
    """No keys, no ollama model → only the mock is registered (P6 requirement)."""
    settings = Settings(groq_api_key="", gemini_api_key="", ollama_model="")
    gateway = LLMGateway(settings=settings)
    assert set(gateway._clients) == {"mock"}
    result = await gateway.chat("generation", [{"role": "user", "content": "hi"}])
    assert result.provider == "mock"
