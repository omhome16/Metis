"""Gemini provider — OpenAI-compatible endpoint + image input.

Uses https://generativelanguage.googleapis.com/v1beta/openai/ so one OpenAI-style
client covers chat, streaming, JSON mode, and vision (image_url with base64 data URIs).
"""

from app.core.config import Settings
from app.gateway.openai_compat import OpenAICompatProvider, build_openai_client


class GeminiProvider(OpenAICompatProvider):
    name = "gemini"
    supports_tools = True

    def __init__(self, settings: Settings):
        super().__init__(
            build_openai_client(
                api_key=settings.gemini_api_key,
                base_url=settings.gemini_openai_base_url,
                timeout=settings.request_timeout,
            )
        )
        self._vision_model = settings.vision_model

    async def describe_image(
        self, image_b64: str, prompt: str, mime_type: str = "image/png"
    ) -> str:
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
        resp = await self._client.chat.completions.create(
            model=self._vision_model, messages=messages, max_tokens=512
        )
        return resp.choices[0].message.content or ""
