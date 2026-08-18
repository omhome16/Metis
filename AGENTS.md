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
uv run python -m scripts.run_matrix tech   # (or "Philosophy") config-matrix eval with thresholds (see Local gates)
```

Until a DB is up, pytest auto-skips Postgres/Redis/Neo4j tests (`require_db`/`require_redis`/`require_graph` fixtures). There is no typecheck step — `ruff check` is the only lint gate.

## Local gates (CI workflow was removed — `074ba77`)

The GitHub Actions workflow is gone (the corpus-dependent `run_matrix` gate kept failing on fresh CI DBs). Run these deliberately before pushing:

- `uv sync --frozen` → `ruff check` (violation count must stay ≤ 283) → `ruff format --check` (drift ≤ 54) → `pytest` → `run_matrix tech`.
- `run_matrix` enforces thresholds only when Postgres is reachable AND the dataset's corpus is ingested (skip-if-down/empty — a fresh DB has no chunks, so seed the corpus before relying on the gate): faithfulness ≥ 0.90, context_precision ≥ 0.80, citation_correctness == 1.0; exits 1 on any breach or when a judge score is unavailable. Only the default config (`hybrid+rerank+graph`) is gated; the other matrix rows are comparison configs that are intentionally worse by design.
- Keep the ruff/format baselines in sync with reality — bump them deliberately, not to mask regressions.

## Architecture

- `app/api/routes/` — one router file per endpoint group; wired in `app/main.py` under `/api/v1`. Includes `POST /ask/{message_id}/feedback` (negative feedback evicts matching semantic-cache entries), `GET /evals/feedback`, `GET/PUT /settings` (runtime settings, see Env gotchas) and `GET /library/reorganizations` (reorg audit log).
- `app/gateway/` — LLM provider abstraction (Groq, Gemini, ollama, mock). No API keys → MockProvider fallback; most tests run entirely on the mock. Task routing is per-task (`generation→groq`, `extraction/judge→gemini`) with per-task overrides `METIS_JUDGE_PROVIDER` / `METIS_EXTRACTION_PROVIDER`.
- `app/rag/` — chunking (parent-child: parents ~2k chars, children cut from parents) → embeddings → hybrid retrieval → rerank → context → `agent.py` (ReAct loop). `app/graph/` is the Neo4j store/extraction + community detection (`communities.py`); `app/evals/` has golden datasets + metrics; `app/workers/` has the arq jobs — `ingest.py` (document pipeline) and `reorg.py` (auto-reorg: community detection + delta-only summary refresh, debounced per runtime policy; `should_run` accepts a `now` kwarg for tests).
- Extraction is tiered (`app/graph/extraction.py`): `t1` local regex (default), `t2` t1 + LLM on sampled 8000-char windows, `t3` LLM per parent (60 max) — all fall back to t1 without API keys; mode + window count are runtime settings. Cache lookups (`app/cache.py`) take a `question=` kwarg — the near-duplicate guard (token Jaccard ≥ `cache_min_jaccard`, length ratio ≤ `cache_max_len_ratio`, capitalized-token agreement) runs only when it's provided.
- Migrations: alembic (async, `alembic/env.py` reads `settings.db_url`). Add a numbered migration for any schema change; models register via `import app.db.models` in env.py.

## Env gotchas

- LLM API keys use their **canonical unprefixed names** (`GROQ_API_KEY`, `GEMINI_API_KEY`); every other setting is `METIS_` prefixed (`app/core/config.py`). Setting `METIS_GROQ_API_KEY` silently does nothing.
- `.env`, `demo/`, `uploads/`, `learning doc/`, model caches are gitignored runtime data — don't commit them.
- Embeddings/rerank/CLIP run locally on CPU; `pyproject.toml` pins torch/torchvision to the cpu PyTorch index. Don't drop those overrides unless CUDA is intended.
- `transformers`/`sentence-transformers` are pinned `<5` (`4.57.6`/`4.1.0`): 5.x crashes fresh processes (0xC0000005) loading bge-m3. Don't "upgrade" them.
- The frontend QA script runs against the server on `:8011` by default (not :8000).
- Groq/Gemini free tiers exhaust fast (Groq ~100k tokens/day, Gemini flash ~20 req/day): when rate-limited, route `judge`/`extraction` to another provider via `METIS_JUDGE_PROVIDER`/`METIS_EXTRACTION_PROVIDER` (e.g. `groq` or a local `ollama` model). `OllamaProvider.structured` works only if the prompt mentions "json" — it injects a hint when absent; don't remove that.
- OCR (`METIS_OCR_ENGINE=pytesseract`) needs the **tesseract binary** on PATH (`apt-get install tesseract-ocr` / tesseract-ocr.win64), not just the Python wheel.
- Runtime settings (`app/core/runtime_settings.py`) live in the `app_settings` table with env defaults in `app/core/config.py` (`METIS_GRAPH_EXTRACTION_MODE`, `METIS_GRAPH_EXTRACT_WINDOWS`, `METIS_GRAPH_REORG_AUTO`, `METIS_GRAPH_REORG_POLICY`, `METIS_GRAPH_REORG_MIN_DOCS`, `METIS_CACHE_MIN_JACCARD`, `METIS_CACHE_MAX_LEN_RATIO`); `GET/PUT /api/v1/settings` merges overrides over defaults and validates values (mode ∈ t1/t2/t3, policy ∈ batch/debounced/nightly). DB reads never raise — a missing table or unreachable Postgres silently returns defaults.

## Testing

- `tests/conftest.py` forces `METIS_EMBED_MODEL=mock` etc. before importing the app — tests never download model weights; don't "fix" that.
- Tests use `httpx.AsyncClient` + `ASGITransport` against a single event loop (SQLAlchemy pool constraints); an autouse fixture disposes the engine after each test — don't add real network calls or new event loops in tests.
- `scripts/frontend_qa.py` fails non-zero on any browser console error — run it after touching frontend JS.