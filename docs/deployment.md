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
`docker compose up --build -d`. The `api` and `worker` services share the
`uploads` volume so the worker can read the files the API stores — keep that
volume mounted if you customize the compose file.

## Security (public deploys)

Metis has no accounts and no sessions. It now ships its own gate rather than
leaving this to you:

```bash
export METIS_API_TOKEN="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"
```

- **Empty (default) = no auth**, which keeps local single-user use unchanged.
- **Set = every protected route requires it**, via `Authorization: Bearer`,
  `X-API-Token`, or `?token=`. `/healthz` and `/static/*` stay open so monitoring
  and the SPA's token prompt still work. Comparison is constant-time on bytes.
- The SPA prompts once and stores the token in `localStorage`; scripted clients
  must send it themselves.
- **`?token=` puts the token in your access logs** — it exists only because a
  browser download cannot attach headers. If that is unacceptable, terminate auth
  at your reverse proxy instead and leave this unset.

The rate limiter (`METIS_RATE_LIMIT_MAX`) is abuse protection, not access control.
It keys on the client address, so behind a proxy set
`METIS_TRUST_PROXY_HEADERS=true` — but only if that proxy overwrites
`X-Forwarded-For`, since otherwise a client can forge its own bucket. Left false,
*every visitor shares one bucket*.

Security headers (`nosniff`, frame-deny, referrer-policy, permissions-policy, and a
CSP keeping `script-src 'self'`) are always on; HSTS is added when
`METIS_ENV=prod`.

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
| `METIS_ROUTER_LLM` | `false` | One extra LLM call to refine the semantic router's lane decision (heuristic-only when false) |
| `METIS_QUERY_REWRITE` / `METIS_METADATA_FILTER` / `METIS_RERANK_ENABLED` | `true` | Retrieval-pipeline toggles (also per-eval config overrides) |
| `METIS_EMBED_MODEL` / `METIS_RERANK_MODEL` / `METIS_CLIP_MODEL` | bge-m3 / bge-reranker-base / clip-ViT-B-32 | Local CPU models |
| `METIS_DB_URL` / `METIS_REDIS_URL` / `METIS_NEO4J_*` | — | Infra endpoints. A plain `postgresql://` DSN (as injected by Render) is rewritten to `postgresql+asyncpg://` automatically |
| `METIS_API_TOKEN` | (empty) | Shared-secret gate on protected routes; empty = no auth. See [Security](#security-public-deploys) |
| `METIS_TRUST_PROXY_HEADERS` | `false` | Key the rate limit on `X-Forwarded-For`; only behind a proxy that overwrites it |
| `METIS_CORS_ORIGINS` | `*` | Set explicit origins in prod (a wildcard cannot be used with credentials) |
| `METIS_JUDGMENT_BACKEND` | `llm` | `llm` \| `typesafe` \| `mock` \| `off` — see [jev.md](jev.md) |
| `TYPESAFE_API_KEY` | (empty) | **Unprefixed** name (like `GROQ_API_KEY`). Required when the backend is `typesafe` |
| `METIS_TYPESAFE_MODEL` | `jev-latest` | Alias or pinned ID (`jev-1.13.0`) |
| `METIS_JUDGMENT_CONTRADICTION_THRESHOLD` / `METIS_JUDGMENT_NOUL_UNCERTAIN_BAND` | `0.5` / `0.15` | Judgment policy; the band is when a verdict escalates instead of acting |
| `METIS_RERANK_BACKEND` | `cross-encoder` | `cross-encoder` \| `typesafe` (comparison) |
| `METIS_ROUTER_BACKEND` | `heuristic` | `heuristic` \| `llm` \| `judgment` |

## OCR

- Set `METIS_OCR_ENGINE=pytesseract` to OCR zero-text PDFs at ingest. Needs
  the **tesseract binary**, not just the wheel: `apt-get install tesseract-ocr`
  (Debian/Render) or tesseract-ocr.win64 on Windows.
- The Docker image installs `tesseract-ocr` for you.
- Documents that stay empty get `extraction_status=empty` + a UI badge + an
  ingest log warning — never silent.

## CI (GitHub Actions) and the local eval gate

`.github/workflows/ci.yml` runs **lint, format check, and pytest**, with no
thresholds — both lint gates are absolute (231 violations and 42 unformatted
files were cleared to 0). The test job uses no DB service containers on purpose:
the infra-gated tests auto-skip, which is what makes this suite CI-viable at all.

A previous workflow was removed (`074ba77`) because it *also* ran the
corpus-dependent `run_matrix` gate, which kept failing on fresh CI databases (no
chunks seeded → the matrix skipped, so the gate never really ran). That check
needs an ingested corpus and so is inherently stateful — it stays a **deliberate
local gate**:

- `uv sync --frozen` → `ruff check` → `ruff format --check` → `uv run pytest`
- `uv run python -m scripts.run_matrix tech` — thresholds: faithfulness ≥ 0.90,
  context_precision ≥ 0.80, citation_correctness == 1.0, enforced on the default
  config only. Needs Postgres reachable **and** the dataset's corpus ingested;
  it skips cleanly otherwise. If the free-tier quota is exhausted (judge/extraction
  calls count against the Gemini daily quota), set `METIS_JUDGE_PROVIDER` /
  `METIS_EXTRACTION_PROVIDER` to `groq` (or an ollama service) and re-run.

## Cloud: Render (free tier) + Neo4j AuraDB Free

> ⚠️ **The blueprint as written cannot boot.** Render's free web service is
> **512 MB RAM / 0.1 CPU**, while `Dockerfile` installs `torch` and the app loads
> `bge-m3` (~2.3 GB of weights) plus `bge-reranker-base` and CLIP. Expect an
> out-of-memory kill. Free Postgres is also reported to expire after 30 days,
> which would take the demo data with it.
>
> Options, in rough cost order: (a) a small VPS with 2–4 GB running the existing
> `docker compose` (~$4–6/mo, keeps local models and the shared `uploads` volume);
> (b) Render with a paid 2 GB instance (~$25–32/mo with a paid database);
> (c) make embeddings/rerank pluggable and call a hosted embedding API so 512 MB
> fits. **Measure the app's real resident memory before choosing** — that number,
> not this estimate, should make the decision.
>
> The free tier also separates `web` and `worker`, which have no shared disk, so
the worker cannot read what the API uploaded (see step 3).

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
   **Caveat:** Render `web` and `worker` are separate hosts with no shared disk, so
   the worker cannot read uploaded files — file ingest only works when the API and
   worker share an `uploads` volume (docker compose) or a single service.
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
- LLM calls go through Groq + Gemini free tiers; the semantic cache (Postgres,
  versioned + TTL'd) reduces repeat spend, and `/evals/run` reports `cost_total_usd`
  per config so you can watch it.
- Local ollama models cost CPU/VRAM only — useful as a fallback when free tiers
  are exhausted (see `METIS_JUDGE_PROVIDER`/`METIS_EXTRACTION_PROVIDER`).
- Langfuse tracing is optional; unset keys disable it with no behavior change.
