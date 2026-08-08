# METIS — The Self-Organizing Knowledge Library

> **One-liner:** The library that reads itself. Drop in documents and images; Metis builds a
> knowledge graph of everything inside, answers questions with citations, surfaces
> cross-document connections you didn't know existed, and flags contradictions between sources.

See **[metis-blueprint.md](metis-blueprint.md)** for the full product design and
**[docs/implementation-plan.md](docs/implementation-plan.md)** for the phased build plan.

## Stack

FastAPI (async) · Postgres + pgvector · Neo4j · Redis · sentence-transformers (bge-m3, CLIP,
bge-reranker) · LLM gateway (Groq + Gemini, free tiers) · arq worker · Langfuse.

## Quickstart

```bash
# 1. Infrastructure (Postgres+pgvector, Redis, Neo4j)
docker compose up -d db cache graph

# 2. Dependencies + env
uv sync
cp .env.example .env   # add GROQ_API_KEY / GEMINI_API_KEY

# 3. Migrations + dev server
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

## API surface (`/api/v1`)

| Endpoint | Method | Purpose |
|---|---|---|
| `/healthz` | GET | Liveness + readiness |
| `/ingest` | POST (multipart) | Upload files + corpus → `job_id` |
| `/ingest/{job_id}` | GET | Job progress (+ SSE `/ingest/{job_id}/stream`) |
| `/corpora` | GET | Corpora + doc counts + graph stats |
| `/ask` | POST | Ask with SSE stream (`sources → tokens → citations → connections → done`) |
| `/search` | GET | Raw hybrid search |
| `/graph/explore` | GET | Subgraph around an entity |
| `/graph/connections` | GET | Path between two entities |
| `/graph/stats` | GET | Node/edge counts, top entities by PageRank |
| `/cache/stats` | GET | Semantic cache hit rate |
| `/evals/run` | POST | Run the eval harness |
| `/evals/reports` | GET | Past eval runs + metrics |

## Tests

```bash
uv run pytest
```
