"""Ollama provider — local LLM via its OpenAI-compatible endpoint.

Runs entirely on CPU; model size is the main cost driver (a 7-8B model is the
practical ceiling on laptop hardware). Register only when `ollama_model` is
configured; the mock stays the default when no provider is configured.
"""

from app.core.config import Settings
from app.gateway.openai_compat import OpenAICompatProvider, build_openai_client


class OllamaProvider(OpenAICompatProvider):
    name = "ollama"

    def __init__(self, settings: Settings, transport=None):
        # `transport` is test-only (httpx MockTransport) — no real server needed.
        super().__init__(
            build_openai_client(
                api_key=settings.ollama_api_key or "ollama",
                base_url=settings.ollama_base_url,
                timeout=settings.request_timeout,
                transport=transport,
            )
        )
        self.supports_tools = settings.ollama_tools
