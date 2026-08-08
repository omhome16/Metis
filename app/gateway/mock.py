"""Deterministic mock provider — used in tests and as a last-resort fallback.

It never calls the network. Chat echoes a labelled summary of the last user message;
structured returns shape-appropriate JSON so downstream parsing code stays testable.
"""

import json
from collections.abc import AsyncIterator

from app.gateway.base import ChatResult, LLMClient


class MockProvider(LLMClient):
    name = "mock"

    def _last_user(self, messages: list[dict]) -> str:
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content")
                if isinstance(content, str):
                    return content
                return str(content)
        return ""

    async def chat(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> ChatResult:
        text = f"[mock:{model}] {self._last_user(messages)[:80]}"
        return ChatResult(text=text, model=model, provider=self.name, usage={"in": 16, "out": len(text)})

    async def chat_stream(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> AsyncIterator[str]:
        text = f"[mock:{model}] {self._last_user(messages)[:80]}"
        for token in text.split(" "):
            yield token + " "

    async def structured(self, messages: list[dict], model: str, json_schema: dict) -> dict:
        prompt = "\n".join(str(m.get("content", "")) for m in messages).lower()
        if "entit" in prompt:  # covers "entity" and "entities"
            return {
                "entities": [{"name": "Gandhi", "type": "Person"}, {"name": "India", "type": "Place"}],
                "relations": [{"source": "Gandhi", "target": "India", "type": "RELATED_TO"}],
            }
        if "rewrite" in prompt:
            return {"query": self._last_user(messages)[:80]}
        if "contradict" in prompt:
            return {"contradicts": False, "reason": ""}
        return {}

    async def describe_image(self, image_b64: str, prompt: str, mime_type: str = "image/png") -> str:
        return json.dumps(
            {"caption": "A mock description of the uploaded image.", "tags": ["mock", "test"]}
        )
