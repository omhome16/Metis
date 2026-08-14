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
    db_url: str = "postgresql+asyncpg://metis:metis@localhost:6433/metis"
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
    # NOTE: gemini-2.5-flash was retired for new API accounts — use the live flash alias.
    vision_model: str = "gemini-flash-latest"
    extraction_model: str = "gemini-flash-latest"
    # Task providers are overridable per task — e.g. route judge/extraction to
    # groq when the Gemini free tier is rate-limited (METIS_JUDGE_PROVIDER /
    # METIS_EXTRACTION_PROVIDER). Values: groq | gemini | ollama.
    judge_provider: str = "gemini"
    extraction_provider: str = "gemini"
    max_tokens: int = 1024
    request_timeout: float = 60.0

    # ── Local LLM (P6): ollama, OpenAI-compatible endpoint ─────────────────
    # `ollama_model` empty → provider disabled (mock remains the default
    # fallback). Set METIS_OLLAMA_TOOLS=true only for models that support
    # tool calls (llama3.x / qwen3 tool-capable tags).
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_api_key: str = "ollama"
    ollama_model: str = ""
    ollama_tools: bool = False

    # ── Local models (free, offline) ───────────────────────────────────────
    embed_model: str = "BAAI/bge-m3"
    embed_dim: int = 1024
    rerank_model: str = "BAAI/bge-reranker-base"
    clip_model: str = "clip-ViT-B-32"
    clip_dim: int = 512

    # ── Retrieval / chunking / context engineering (M5) ───────────────────
    chunk_size: int = 700
    chunk_overlap: int = 100
    top_k_vector: int = 10
    top_k_keyword: int = 10
    top_k_graph: int = 10
    rerank_candidates: int = 20
    top_k_rerank: int = 5
    cache_similarity_threshold: float = 0.92
    cache_ttl_days: int = 7
    query_rewrite: bool = True
    rerank_enabled: bool = True

    # ── Parent-child chunking (P3.1) ───────────────────────────────────────
    # true: children (~child_size) are embedded and searched; context blocks
    # come from their parents (~parent_size). false: flat chunks (old behavior).
    parent_child: bool = True
    parent_size: int = 2000
    child_size: int = 400
    child_overlap: int = 60

    # ── Metadata filtering (P3.2) ──────────────────────────────────────────
    # LLM (best-effort) extracts {tags, date_from, date_to, author} from the
    # query; applied as SQL filters on both retrieval arms. Empty on failure.
    metadata_filter: bool = True

    # ── Semantic router (P4) ────────────────────────────────────────────────
    # true: heuristic lane decision is optionally refined by one LLM call
    # (task "router"); any failure keeps the heuristic result. false: heuristic only.
    router_llm: bool = False

    # ── Global sensemaking (P5) ─────────────────────────────────────────────
    # top-k communities whose summaries feed a deep-lane "global" answer.
    global_relevance_budget: int = 8

    # ── OCR (P6) ────────────────────────────────────────────────────────────
    # "pytesseract": OCR PDFs with zero extracted text (tesseract binary
    # required, rasterized locally). "" (default): zero-text PDFs are marked
    # `extraction_status=empty` + ingest warning — never silent.
    ocr_engine: str = ""

    # ── Graph extraction (P2 lazy move) ────────────────────────────────────
    # true: LLM typed-relations at ingest; false: regex fallback only (offline build).
    graph_llm_extract: bool = True

    # ── HNSW tuning (pgvector 0.8+; per-connection GUCs) ──────────────────
    hnsw_ef_search: int = 120
    hnsw_iterative_scan: str = "relaxed_order"  # off | relaxed_order | strict_order

    # ── Observability / limits ─────────────────────────────────────────────
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    rate_limit_max: int = 120
    rate_limit_window: int = 60

    # ── CORS ───────────────────────────────────────────────────────────────
    # Set METIS_CORS_ORIGINS (comma-separated) in prod; dev default allows all.
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
