"""Groq provider — OpenAI-compatible SDK at https://api.groq.com/openai/v1."""

import json
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from app.core.config import Settings
from app.core.logging import get_logger
from app.gateway.base import ChatResult, LLMClient, ToolStreamChunk, parse_tool_call_deltas

logger = get_logger(__name__)


class GroqProvider(LLMClient):
    name = "groq"
    supports_tools = True

    def __init__(self, settings: Settings):
        self._client = AsyncOpenAI(
            api_key=settings.groq_api_key,
            base_url=settings.groq_base_url,
            timeout=settings.request_timeout,
        )

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
        # Groq requires the word "json" somewhere in the prompt when
        # response_format=json_object is set — inject a hint if absent.
        msgs = list(messages)
        if not any("json" in str(m.get("content", "")).lower() for m in msgs):
            msgs.append({"role": "user", "content": "Respond in JSON."})
        resp = await self._client.chat.completions.create(
            model=model,
            messages=msgs,
            temperature=0,
            max_tokens=1024,
            response_format={"type": "json_object"},
        )
        try:
            return json.loads(resp.choices[0].message.content or "{}")
        except json.JSONDecodeError:
            logger.warning(
                "structured response was not valid JSON: %r", resp.choices[0].message.content[:200]
            )
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
                fragments.append(
                    {
                        "index": tc.index,
                        "id": tc.id or "",
                        "name": (tc.function.name if tc.function else None) or "",
                        "arguments": (tc.function.arguments if tc.function else None) or "",
                    }
                )
        if fragments:
            calls = parse_tool_call_deltas(fragments)
            if calls:
                yield ToolStreamChunk(tool_calls=calls)
