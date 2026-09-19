"""Shared `LLMClient` implementation for OpenAI-compatible endpoints.

Groq, Gemini (via its OpenAI-compatibility layer), and ollama all speak the same
OpenAI wire format. The four call shapes — chat, streaming, structured JSON, and
tool-calling streams — previously existed as three near-identical copies; they
live here once, and each provider declares only how to build its client and what
it supports.
"""

import json
from collections.abc import AsyncIterator

import httpx
from openai import AsyncOpenAI

from app.core.logging import get_logger
from app.gateway.base import ChatResult, LLMClient, ToolStreamChunk, parse_tool_call_deltas

logger = get_logger(__name__)


def build_openai_client(
    *, api_key: str, base_url: str, timeout: float, transport=None
) -> AsyncOpenAI:
    """Build an `AsyncOpenAI` client for an OpenAI-compatible endpoint.

    `transport` is test-only (an `httpx.MockTransport`) so provider tests never
    touch the network.
    """
    kwargs: dict = {}
    if transport is not None:
        kwargs["http_client"] = httpx.AsyncClient(transport=transport)
    return AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout, **kwargs)


class OpenAICompatProvider(LLMClient):
    """An `LLMClient` backed by an OpenAI-compatible chat-completions endpoint."""

    #: Some endpoints (Groq) reject `response_format=json_object` unless the word
    #: "json" appears somewhere in the prompt. Those set this to True.
    requires_json_hint: bool = False

    def __init__(self, client: AsyncOpenAI):
        self._client = client

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
        msgs = list(messages)
        if self.requires_json_hint and not any(
            "json" in str(m.get("content", "")).lower() for m in msgs
        ):
            msgs.append({"role": "user", "content": "Respond in JSON."})
        resp = await self._client.chat.completions.create(
            model=model,
            messages=msgs,
            temperature=0,
            max_tokens=1024,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content or ""
        try:
            return json.loads(content or "{}")
        except json.JSONDecodeError:
            logger.warning("structured response was not valid JSON: %r", content[:200])
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
