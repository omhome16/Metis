"""The `LLMClient` interface — one shape for chat, streaming, structured JSON, and vision."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field


@dataclass
class ChatResult:
    text: str
    model: str
    usage: dict = field(default_factory=lambda: {"in": 0, "out": 0})
    provider: str = ""


class LLMClient(ABC):
    """Base class for an LLM provider adapter."""

    name: str = "base"

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> ChatResult:
        """Single non-streaming completion."""

    @abstractmethod
    def chat_stream(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> AsyncIterator[str]:
        """Token-by-token streaming completion."""

    @abstractmethod
    async def structured(self, messages: list[dict], model: str, json_schema: dict) -> dict:
        """JSON-mode completion (for entity extraction, judging, rewriting)."""

    async def describe_image(self, image_b64: str, prompt: str, mime_type: str = "image/png") -> str:
        raise NotImplementedError(f"{self.name} does not support vision")
