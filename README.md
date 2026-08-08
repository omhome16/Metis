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

## Frontend

A hand-built, dependency-free single-page app served by FastAPI at `/` (no build step —
vanilla ES modules + CSS custom properties).

- **Vaults** — named libraries (`/vaults` API). Each vault holds its documents, its own
  knowledge graph, and a chat grounded in that vault's sources.
- **Documents** — library grid with per-file status, drag-and-drop upload with live job
  progress, and a detail view (raw content, chunk list, original file).
- **Graph** — a bespoke canvas force-directed renderer: drag nodes, scroll to zoom,
  click an entity to expand its neighborhood, search to focus. No graph library — the
  physics and rendering are ~300 lines of first-party code.
- **Ask** — SSE chat with token streaming, markdown answers, clickable citation chips,
  scored source cards, contradiction alerts, image attach, and a semantic-cache replay
  badge.
- **ReAct agent** — when the LLM provider supports function calling, the chat runs a
  tool-augmented reasoning loop: the model itself searches the vault, expands the
  knowledge graph, and checks background references before answering. The frontend
  shows a live "thinking" panel with each tool step as it happens.
- **Conversations** — every exchange is persisted server-side per vault (migration
  0005). Follow-ups carry full history back into the agent loop; the Ask view lists,
  switches, renames, and deletes past conversations.
- **Library** — the whole library as one graph (`/library` API): every vault rendered on
  a single canvas with vault-colored clusters and dashed cross-vault edges, live vault
  filters, and a **Surprises** tab that mines the graph for connections between vaults
  (shared concepts + cross-vault links) narrated in one LLM call.
- **Idea journeys** — pick any two entities (search-as-you-type pickers) and Metis finds
  the shortest path between them across all vaults, highlights it on the graph, and
  narrates the journey as a short story.
- **Resilience** — when the LLM providers are rate-limited, the agent falls back to
  direct hybrid retrieval → context assembly → generation, so answers stay grounded in
  real vault sources instead of degrading to an empty reply.
- **Themes** — "Reading Room" (light) and "Night Archive" (dark), system-aware,
  persisted, with a live-recolored graph. Typefaces (Spectral / Inter / IBM Plex Mono)
  are self-hosted.

Run the end-to-end UI check (headless Chromium, verifies home/vaults/graph/ask/themes
and fails on any console error):

```bash
uv run python scripts/frontend_qa.py
```

## API surface (`/api/v1`)

| Endpoint | Method | Purpose |
|---|---|---|
| `/healthz` | GET | Liveness + readiness |
| `/ingest` | POST (multipart) | Upload files + corpus → `job_id` |
| `/ingest/{job_id}` | GET | Job progress (+ SSE `/ingest/{job_id}/stream`) |
| `/corpora` | GET | Corpora + doc counts + graph stats |
| `/ask` | POST | Ask with SSE stream (`sources → thinking → tokens → citations → done`) |
| `/vaults/{name}/conversations` | GET/POST | List / create conversations for a vault |
| `/conversations/{id}` | GET/PATCH/DELETE | Conversation detail, rename, delete |
| `/conversations/{id}/messages` | GET | Message history for a conversation |
| `/search` | GET | Raw hybrid search |
| `/graph/explore` | GET | Subgraph around an entity |
| `/graph/connections` | GET | Path between two entities |
| `/graph/stats` | GET | Node/edge counts, top entities by PageRank |
| `/cache/stats` | GET | Semantic cache hit rate |
| `/library/graph` | GET | Cross-vault library graph (corpus-tagged nodes, bridge flags) |
| `/library/surprises` | GET | Mined cross-vault connections with LLM narratives |
| `/library/journey` | GET | Shortest entity path across vaults + narrated story |
| `/library/entities` | GET | Entity search for the journey pickers |
| `/evals/run` | POST | Run the eval harness |
| `/evals/reports` | GET | Past eval runs + metrics |

## Tests

```bash
uv run pytest
```
