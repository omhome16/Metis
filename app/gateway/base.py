"""The `LLMClient` interface — one shape for chat, streaming, structured JSON, and vision."""

import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field


@dataclass
class ChatResult:
    text: str
    model: str
    usage: dict = field(default_factory=lambda: {"in": 0, "out": 0})
    provider: str = ""


@dataclass
class ToolCall:
    """A function-call the model requested."""

    id: str
    name: str
    arguments: dict = field(default_factory=dict)  # parsed JSON args


@dataclass
class ToolStreamChunk:
    """One frame of a tool-calling stream.

    `text` is a content delta (usually empty on tool-call rounds). When the model
    decides to invoke tools, the final chunk carries the complete `tool_calls`.
    """

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


def parse_tool_call_deltas(deltas: list[dict]) -> list[ToolCall]:
    """Assemble OpenAI-style streaming `delta.tool_calls` fragments into ToolCalls.

    Each provider passes the raw fragments it collected:
      [{"index": 0, "id": "...", "name": "...", "arguments": "{...}"}]
    Fragments are accumulated by index, then parsed into ToolCall objects.
    """
    slots: dict[int, dict] = {}
    for frag in deltas:
        idx = frag.get("index", 0)
        slot = slots.setdefault(idx, {"id": "", "name": "", "arguments": ""})
        if frag.get("id"):
            slot["id"] = frag["id"]
        if frag.get("name"):
            slot["name"] += frag["name"]
        if frag.get("arguments"):
            slot["arguments"] += frag["arguments"]
    calls: list[ToolCall] = []
    for idx in sorted(slots):
        slot = slots[idx]
        try:
            args = json.loads(slot["arguments"] or "{}")
        except json.JSONDecodeError:
            args = {}
        calls.append(ToolCall(id=slot["id"], name=slot["name"], arguments=args))
    return calls


class LLMClient(ABC):
    """Base class for an LLM provider adapter."""

    name: str = "base"
    supports_tools: bool = False

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

    async def chat_tools_stream(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> AsyncIterator[ToolStreamChunk]:
        """Tool-calling completion. Providers without function calling raise NotImplementedError."""
        raise NotImplementedError(f"{self.name} does not support tool calling")
