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

## Cloud: Render (free tier) + Neo4j AuraDB Free

1. **Postgres/Redis** — Render managed: import `render.yaml` (Blueprint) or create
   manually. The blueprint wires `METIS_DB_URL` and `METIS_REDIS_URL` automatically.
2. **Neo4j** — [AuraDB Free](https://neo4j.com/cloud/aura/) (works free for demo
   corpora; the GDS plugin is *not* available on Aura Free — graph features that
   don't need GDS, like shortestPath and Cypher traversal, still work).
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
- Langfuse tracing is optional; unset keys disable it with no behavior change.
