# Deploying METIS

## Local (docker compose)

```bash
docker compose up -d db cache graph     # Postgres+pgvector, Redis, Neo4j+GDS
uv sync
cp .env.example .env                    # add GROQ_API_KEY / GEMINI_API_KEY
uv run alembic upgrade head
uv run uvicorn app.main:app --reload    # API on :8000
uv run arq app.workers.settings.WorkerSettings   # ingestion/evals worker
```

All 5 services (api/worker/db/cache/graph) can also run fully containerized:
`docker compose up --build -d`.

## Environment variables (all `METIS_*` except LLM keys)

| Variable | Default | Purpose |
|---|---|---|
| `GROQ_API_KEY` / `GEMINI_API_KEY` | — | Canonical unprefixed names (see AGENTS.md "Env gotchas") |
| `METIS_PRIMARY_PROVIDER` | `groq` | `groq` \| `gemini` \| `ollama` |
| `METIS_GENERATION_MODEL` / `METIS_FAST_MODEL` | llama-3.3-70b / 3.1-8b | Groq models |
| `METIS_VISION_MODEL` / `METIS_EXTRACTION_MODEL` | `gemini-flash-latest` | Gemini flash family |
| `METIS_JUDGE_PROVIDER` / `METIS_EXTRACTION_PROVIDER` | `gemini` | Per-task provider override (P7) — e.g. `groq` or `ollama` when the Gemini free tier is rate-limited |
| `METIS_OLLAMA_MODEL` | (empty) | Local LLM via ollama (OpenAI-compatible `http://localhost:11434/v1`); empty disables |
| `METIS_OLLAMA_BASE_URL` / `METIS_OLLAMA_TOOLS` | `http://localhost:11434/v1` / `false` | ollama endpoint; `true` only for tool-capable tags |
| `METIS_OCR_ENGINE` | (empty) | `pytesseract` enables OCR of zero-text PDFs; requires the **tesseract binary** on PATH |
| `METIS_PARENT_CHILD` / `METIS_PARENT_SIZE` | `true` / `2000` | Parent-child chunking kill switch |
| `METIS_ROUTER_ENABLED` | — | Semantic router lanes kill switch |
| `METIS_EMBED_MODEL` / `METIS_RERANK_MODEL` / `METIS_CLIP_MODEL` | bge-m3 / bge-reranker-base / clip-ViT-B-32 | Local CPU models |
| `METIS_DB_URL` / `METIS_REDIS_URL` / `METIS_NEO4J_*` | — | Infra endpoints |

## OCR

- Set `METIS_OCR_ENGINE=pytesseract` to OCR zero-text PDFs at ingest. Needs
  the **tesseract binary**, not just the wheel: `apt-get install tesseract-ocr`
  (Debian/Render) or tesseract-ocr.win64 on Windows.
- The Docker image installs `tesseract-ocr` for you.
- Documents that stay empty get `extraction_status=empty` + a UI badge + an
  ingest log warning — never silent.

## CI (GitHub Actions — removed)

`.github/workflows/ci.yml` was removed (`074ba77`): the corpus-dependent
`run_matrix` gate kept failing on fresh CI databases (no chunks seeded → the
matrix skipped, so the gate never ran). The checks it ran are now **local gates,
run deliberately before pushing**:

- `uv sync --frozen` → `ruff check` (violation count ≤ 283) → `ruff format --check` (drift ≤ 54)
- `uv run pytest`
- `uv run python -m scripts.run_matrix tech` — thresholds: faithfulness ≥ 0.90,
  context_precision ≥ 0.80, citation_correctness == 1.0, enforced on the default
  config only. Needs Postgres reachable **and** the dataset's corpus ingested;
  it skips cleanly otherwise. If the free-tier quota is exhausted (judge/extraction
  calls count against the Gemini daily quota), set `METIS_JUDGE_PROVIDER` /
  `METIS_EXTRACTION_PROVIDER` to `groq` (or an ollama service) and re-run.

## Cloud: Render (free tier) + Neo4j AuraDB Free

1. **Postgres/Redis** — Render managed: import `render.yaml` (Blueprint) or create
   manually. The blueprint wires `METIS_DB_URL` and `METIS_REDIS_URL` automatically.
2. **Neo4j** — [AuraDB Free](https://neo4j.com/cloud/aura/) (works free for demo
   corpora; the GDS plugin is *not* available on Aura Free — graph features that
   don't need GDS, like shortestPath and Cypher traversal, still work, but
   **community detection (`app/graph/communities.py`) is GDS-only and no-ops on
   Aura Free**).
   Set `METIS_NEO4J_URI`, `METIS_NEO4J_USER`, `METIS_NEO4J_PASSWORD`.
3. **API + worker** — the Dockerfile runs both; Render starts `web` with uvicorn and
   `worker` with arq. `GROQ_API_KEY` / `GEMINI_API_KEY` are set in the dashboard.
4. **Migrations** — run once against the managed DB:
   `uv run alembic upgrade head` (locally with `METIS_DB_URL` pointed at Render).
5. **First boot** — `POST /api/v1/ingest` a document; the worker embeds it with the
   local models (the CPU wheel Docker image) and builds the Neo4j graph. Then
   `POST /api/v1/ask` streams a cited answer.

## Railway (alternative)

- Services: `api` (Dockerfile, `uvicorn app.main:app`), `worker` (Dockerfile,
  `arq app.workers.settings.WorkerSettings`), plus Railway Postgres + Redis plugins.
- Neo4j: same AuraDB Free approach. Set env vars per service.

## Cost notes

- Embeddings / reranker / CLIP run **locally (free)** in the container.
- LLM calls go through Groq + Gemini free tiers; the semantic cache (Redis) reduces
  repeat spend, and `/evals/run` reports `cost_total_usd` per config so you can watch it.
- Local ollama models cost CPU/VRAM only — useful as a fallback when free tiers
  are exhausted (see `METIS_JUDGE_PROVIDER`/`METIS_EXTRACTION_PROVIDER`).
- Langfuse tracing is optional; unset keys disable it with no behavior change.
