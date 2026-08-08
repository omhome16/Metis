"""Gemini provider — OpenAI-compatible endpoint + image input.

Uses https://generativelanguage.googleapis.com/v1beta/openai/ so one OpenAI-style
client covers chat, streaming, JSON mode, and vision (image_url with base64 data URIs).
"""

import base64
import json
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from app.core.config import Settings
from app.core.logging import get_logger
from app.gateway.base import ChatResult, LLMClient

logger = get_logger(__name__)


class GeminiProvider(LLMClient):
    name = "gemini"

    def __init__(self, settings: Settings):
        self._client = AsyncOpenAI(
            api_key=settings.gemini_api_key,
            base_url=settings.gemini_openai_base_url,
            timeout=settings.request_timeout,
        )
        self._vision_model = settings.vision_model

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

    async def describe_image(self, image_b64: str, prompt: str, mime_type: str = "image/png") -> str:
        data_uri = f"data:{mime_type};base64,{image_b64}"
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ]
        resp = await self._client.chat.completions.create(model=self._vision_model, messages=messages, max_tokens=512)
        return resp.choices[0].message.content or ""
