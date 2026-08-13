"""Ollama provider (P6) — local LLM via its OpenAI-compatible endpoint.

Runs entirely on CPU; model size is the main cost driver (a 7-8B model is the
practical ceiling on laptop hardware). Register only when `ollama_model` is
configured; the mock stays the default when no provider is configured.
"""

import json
from collections.abc import AsyncIterator

import httpx
from openai import AsyncOpenAI

from app.core.config import Settings
from app.core.logging import get_logger
from app.gateway.base import ChatResult, LLMClient, ToolStreamChunk, parse_tool_call_deltas

logger = get_logger(__name__)


class OllamaProvider(LLMClient):
    name = "ollama"

    def __init__(self, settings: Settings, transport=None):
        # `transport` is test-only (httpx MockTransport) — no real server needed.
        self._settings = settings
        kwargs = {}
        if transport is not None:
            kwargs["http_client"] = httpx.AsyncClient(transport=transport)
        self._client = AsyncOpenAI(
            api_key=settings.ollama_api_key or "ollama",
            base_url=settings.ollama_base_url,
            timeout=settings.request_timeout,
            **kwargs,
        )
        self.supports_tools = settings.ollama_tools

    async def chat(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> ChatResult:
        resp = await self._client.chat.completions.create(
            model=model, messages=messages, temperature=temperature, max_tokens=max_tokens
        )
        return ChatResult(
            text=resp.choices[0].message.content or "",
            model=model,
            provider=self.name,
            usage={"in": resp.usage.prompt_tokens or 0, "out": resp.usage.completion_tokens or 0},
        )

    async def chat_stream(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream:
            if chunk.choices:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta

    async def structured(self, messages: list[dict], model: str, json_schema: dict) -> dict:
        resp = await self._client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
            max_tokens=1024,
            response_format={"type": "json_object"},
        )
        try:
            return json.loads(resp.choices[0].message.content or "{}")
        except json.JSONDecodeError:
            snippet = (resp.choices[0].message.content or "")[:200]
            logger.warning("structured response was not valid JSON: %r", snippet)
            return {}

    async def chat_tools_stream(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> AsyncIterator[ToolStreamChunk]:
        stream = await self._client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools or None,
            stream=True,
        )
        fragments: list[dict] = []
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                yield ToolStreamChunk(text=delta.content)
            for tc in delta.tool_calls or []:
                name = (tc.function.name if tc.function else None) or ""
                arguments = (tc.function.arguments if tc.function else None) or ""
                fragments.append(
                    {"index": tc.index, "id": tc.id or "", "name": name, "arguments": arguments}
                )
        if fragments:
            calls = parse_tool_call_deltas(fragments)
            if calls:
                yield ToolStreamChunk(tool_calls=calls)
