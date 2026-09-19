"""Groq provider — OpenAI-compatible SDK at https://api.groq.com/openai/v1."""

from app.core.config import Settings
from app.gateway.openai_compat import OpenAICompatProvider, build_openai_client


class GroqProvider(OpenAICompatProvider):
    name = "groq"
    supports_tools = True
    requires_json_hint = True  # Groq rejects json_object unless the prompt says "json"

    def __init__(self, settings: Settings):
        super().__init__(
            build_openai_client(
                api_key=settings.groq_api_key,
                base_url=settings.groq_base_url,
                timeout=settings.request_timeout,
            )
        )
