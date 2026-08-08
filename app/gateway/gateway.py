"""LLM gateway — routes tasks to preferred providers with a fallback chain.

Task → preferred provider (blueprint §7):
  generation → groq (llama-3.3-70b) · fast → groq (8B) · extraction/vision/judge → gemini.
Any provider can back up any other; the deterministic mock is the final safety net.
"""

from collections.abc import AsyncIterator
from functools import lru_cache

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.gateway.base import ChatResult, LLMClient
from app.gateway.gemini import GeminiProvider
from app.gateway.groq import GroqProvider
from app.gateway.mock import MockProvider

logger = get_logger(__name__)

# task → preferred provider
TASK_PROVIDER: dict[str, str] = {
    "generation": "groq",
    "fast": "groq",
    "extraction": "gemini",
    "vision": "gemini",
    "judge": "gemini",
}

# Estimated $/1M tokens (in, out) for cost metering — approximate, update from consoles.
PRICING: dict[str, tuple[float, float]] = {
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "llama-3.1-8b-instant": (0.05, 0.08),
    "gemini-2.5-flash": (0.30, 2.50),
}


def estimate_cost_usd(model: str, usage: dict) -> float:
    price_in, price_out = PRICING.get(model, (0.0, 0.0))
    return (usage.get("in", 0) / 1e6 * price_in) + (usage.get("out", 0) / 1e6 * price_out)


class LLMGateway:
    def __init__(self, settings: Settings | None = None, clients: dict[str, LLMClient] | None = None):
        self._settings = settings or get_settings()
        self._clients: dict[str, LLMClient] = dict(clients or {})
        self._clients.setdefault("mock", MockProvider())
        if "groq" not in self._clients and self._settings.groq_api_key:
            self._clients["groq"] = GroqProvider(self._settings)
        if "gemini" not in self._clients and self._settings.gemini_api_key:
            self._clients["gemini"] = GeminiProvider(self._settings)

    # ── provider routing ────────────────────────────────────────────────────
    def _candidates(self, task: str) -> list[LLMClient]:
        preferred = TASK_PROVIDER.get(task, self._settings.primary_provider)
        others = [p for p in ("groq", "gemini", "mock") if p != preferred]
        order = [preferred, *others]
        return [self._clients[p] for p in order if p in self._clients]

    def _model_for(self, provider_name: str, task: str) -> str:
        if provider_name == "groq":
            return self._settings.fast_model if task == "fast" else self._settings.generation_model
        return self._settings.vision_model  # gemini flash family covers extraction/vision/judge

    # ── calls ───────────────────────────────────────────────────────────────
    async def chat(
        self,
        task: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> ChatResult:
        max_tokens = max_tokens or self._settings.max_tokens
        errors: list[str] = []
        for client in self._candidates(task):
            try:
                return await client.chat(messages, self._model_for(client.name, task), temperature, max_tokens)
            except Exception as exc:  # noqa: BLE001 — any failure falls through the chain
                errors.append(f"{client.name}: {exc}")
                logger.warning("provider %s failed for task %s: %s", client.name, task, exc)
        raise RuntimeError(f"all LLM providers failed for task '{task}': {' | '.join(errors)}")

    async def chat_stream(
        self,
        task: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        max_tokens = max_tokens or self._settings.max_tokens
        errors: list[str] = []
        for client in self._candidates(task):
            try:
                async for token in client.chat_stream(messages, self._model_for(client.name, task), temperature, max_tokens):
                    yield token
                return
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{client.name}: {exc}")
                logger.warning("stream provider %s failed for task %s: %s", client.name, task, exc)
        raise RuntimeError(f"all LLM providers failed for task '{task}': {' | '.join(errors)}")

    async def structured(self, task: str, messages: list[dict], json_schema: dict) -> dict:
        errors: list[str] = []
        for client in self._candidates(task):
            try:
                return await client.structured(messages, self._model_for(client.name, task), json_schema)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{client.name}: {exc}")
                logger.warning("structured provider %s failed for task %s: %s", client.name, task, exc)
        raise RuntimeError(f"all LLM providers failed for structured task '{task}': {' | '.join(errors)}")

    async def describe_image(self, image_b64: str, prompt: str, mime_type: str = "image/png") -> str:
        errors: list[str] = []
        for client in self._candidates("vision"):
            try:
                return await client.describe_image(image_b64, prompt, mime_type)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{client.name}: {exc}")
        raise RuntimeError(f"vision failed: {' | '.join(errors)}")


@lru_cache
def get_gateway() -> LLMGateway:
    return LLMGateway(get_settings())
