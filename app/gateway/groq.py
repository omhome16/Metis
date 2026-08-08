"""Groq provider — OpenAI-compatible SDK at https://api.groq.com/openai/v1."""

import json
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from app.core.config import Settings
from app.gateway.base import ChatResult, LLMClient
from app.core.logging import get_logger

logger = get_logger(__name__)


class GroqProvider(LLMClient):
    name = "groq"

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
            model=model, messages=messages, temperature=temperature, max_tokens=max_tokens, stream=True
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
            logger.warning("structured response was not valid JSON: %r", resp.choices[0].message.content[:200])
            return {}
