# AGENTS.md

Metis: FastAPI knowledge-library (RAG + Neo4j knowledge graph + contradiction detection + eval harness). Backend is a single `app/` package; frontend is a dependency-free vanilla-JS SPA served by FastAPI at `/` (no npm, no build step — edit `app/static/js/**` directly).

## Commands

```bash
docker compose up -d db cache graph   # Postgres+pgvector, Redis, Neo4j — required for full dev/test
uv sync
cp .env.example .env                  # add GROQ_API_KEY / GEMINI_API_KEY (see Env traps)
uv run alembic upgrade head           # async migrations, driven by METIS_DB_URL
uv run uvicorn app.main:app --reload  # API + SPA on :8000
uv run arq app.workers.settings.WorkerSettings   # ingest worker — required or /ingest jobs never run
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run python scripts/frontend_qa.py   # Playwright UI check; expects server on :8011 (default) — start `uv run uvicorn app.main:app --port 8011` or pass a URL; needs `uv run playwright install chromium` once
uv run python -m scripts.run_matrix tech   # (or "arts") config-matrix eval
```

Until a DB is up, pytest auto-skips Postgres/Redis/Neo4j tests (`require_db`/`require_redis`/`require_graph` fixtures). There is no typecheck step — `ruff check` is the only lint gate.

## Architecture

- `app/api/routes/` — one router file per endpoint group; wired in `app/main.py` under `/api/v1`.
- `app/gateway/` — LLM provider abstraction (Groq, Gemini, mock). No API keys → MockProvider fallback; most tests run entirely on the mock.
- `app/rag/` — chunking → embeddings → hybrid retrieval → rerank → context → `agent.py` (ReAct loop). `app/graph/` is the Neo4j store/extraction; `app/evals/` has golden datasets + metrics; `app/workers/ingest.py` is the arq job.
- Migrations: alembic (async, `alembic/env.py` reads `settings.db_url`). Add a numbered migration for any schema change; models register via `import app.db.models` in env.py.

## Env gotchas

- LLM API keys use their **canonical unprefixed names** (`GROQ_API_KEY`, `GEMINI_API_KEY`); every other setting is `METIS_` prefixed (`app/core/config.py`). Setting `METIS_GROQ_API_KEY` silently does nothing.
- `.env`, `demo/`, `uploads/`, model caches are gitignored runtime data — don't commit them.
- Embeddings/rerank/CLIP run locally on CPU; `pyproject.toml` pins torch/torchvision to the cpu PyTorch index. Don't drop those overrides unless CUDA is intended.
- `transformers`/`sentence-transformers` are pinned `<5` (`4.57.6`/`4.1.0`): 5.x crashes fresh processes (0xC0000005) loading bge-m3. Don't "upgrade" them.
- The frontend QA script runs against the server on `:8011` by default (not :8000).

## Testing

- `tests/conftest.py` forces `METIS_EMBED_MODEL=mock` etc. before importing the app — tests never download model weights; don't "fix" that.
- Tests use `httpx.AsyncClient` + `ASGITransport` against a single event loop (SQLAlchemy pool constraints); an autouse fixture disposes the engine after each test — don't add real network calls or new event loops in tests.
- `scripts/frontend_qa.py` fails non-zero on any browser console error — run it after touching frontend JS.