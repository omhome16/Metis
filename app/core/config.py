"""Application settings, loaded from environment / .env file."""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="METIS_",
        extra="ignore",
        case_sensitive=False,
    )

    # ── App ────────────────────────────────────────────────────────────────
    app_name: str = "Metis"
    env: str = "dev"  # dev | test | prod
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"

    # ── Databases (docker-compose.yml) ─────────────────────────────────────
    db_url: str = "postgresql+asyncpg://metis:metis@localhost:5433/metis"
    redis_url: str = "redis://localhost:6379/0"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "metis-dev-password"

    # ── LLM gateway ────────────────────────────────────────────────────────
    # NOTE: GROQ_API_KEY / GEMINI_API_KEY use their canonical unprefixed names.
    groq_api_key: str = Field(default="", validation_alias="GROQ_API_KEY")
    gemini_api_key: str = Field(default="", validation_alias="GEMINI_API_KEY")
    groq_base_url: str = "https://api.groq.com/openai/v1"
    gemini_openai_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    primary_provider: str = "groq"  # groq | gemini
    generation_model: str = "llama-3.3-70b-versatile"
    fast_model: str = "llama-3.1-8b-instant"
    vision_model: str = "gemini-2.5-flash"
    extraction_model: str = "gemini-2.5-flash"
    max_tokens: int = 1024
    request_timeout: float = 60.0

    # ── Local models (free, offline) ───────────────────────────────────────
    embed_model: str = "BAAI/bge-m3"
    embed_dim: int = 1024
    rerank_model: str = "BAAI/bge-reranker-base"
    clip_model: str = "clip-ViT-B-32"
    clip_dim: int = 512

    # ── Retrieval / chunking ───────────────────────────────────────────────
    chunk_size: int = 512
    chunk_overlap: int = 64
    top_k_vector: int = 10
    top_k_keyword: int = 10
    top_k_graph: int = 10
    rerank_candidates: int = 20
    top_k_rerank: int = 5
    cache_similarity_threshold: float = 0.92

    # ── CORS ───────────────────────────────────────────────────────────────
    cors_origins: list[str] = ["*"]

    @field_validator("groq_api_key", "gemini_api_key", mode="before")
    @classmethod
    def _trim_api_keys(cls, v):
        """Treat whitespace-only values (e.g. `KEY=   # comment`) as unset."""
        return v.strip() if isinstance(v, str) else v

    @property
    def is_test(self) -> bool:
        return self.env == "test"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
