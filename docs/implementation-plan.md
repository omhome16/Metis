# METIS — Implementation Plan

Derived from `metis-blueprint.md`. Each phase ends with tests passing, a docker-compose smoke
test, and a **git commit + push**. Phases are committed in sub-steps where sensible.

| Phase | Blueprint milestone | Deliverables | Exit criterion |
|---|---|---|---|
| 0 — Foundations | — | `pyproject.toml` (uv), `.env.example`, `docker-compose.yml` (pgvector/Redis/Neo4j+GDS), settings, logging, README, plan | `docker compose up -d db cache graph` healthy; `uv run` imports clean |
| 1 — Skeleton | M1 | FastAPI app, `/healthz`, async SQLAlchemy models + Alembic migration, `/ingest` stores raw docs, `/corpora` | pytest green; healthz returns 200 with DB/Redis/Neo4j readiness |
| 2 — Retrieval + Ask | M2 | Chunking, local embeddings (bge-m3), pgvector search, LLM Gateway (Groq+Gemini fallback), `/ask` SSE (`sources→tokens→citations→done`) | end-to-end ask against a demo doc returns a cited answer |
| 3 — Graph | M3 | Gemini structured extraction → Neo4j (Entity/Topic/Chunk/Document, MENTIONS/RELATED_TO/CONTAINS), graph-boosted retrieval, `/graph/explore` `/graph/connections` `/graph/stats` | ingest a doc → entities appear; `/graph/connections` resolves a path |
| 4 — Multimodal | M4 | Image ingestion: Gemini vision captions/tags + CLIP embeddings → Image node + DEPICTS; image-aware `/ask` | upload an image → caption stored; ask about image retrieves it |
| 5 — Context engineering | M5 | Query rewriting, hybrid search (vector+tsvector+RRF), bge-reranker, citation grounding, contradiction scan (CONTRADICTS edges) | hybrid beats single-retriever in unit eval; grounding drops unverifiable cites |
| 6 — Eval harness | M6 | Golden datasets (arts + tech), RAGAS metrics (faithfulness, answer relevancy, context precision/recall), config matrix, `/evals/*` | a config-matrix run produces a metrics report |
| 7 — Hardening | M7 | Redis semantic cache, Langfuse tracing, robust error handling + DLQ, deploy configs (Render/Railway + AuraDB) | cache hit served; traces captured; deploy docs complete |

## Status: ALL PHASES COMPLETE ✅

Every phase is implemented, tested (70 pytest tests), validated live against the local
Docker stack, and pushed to `origin/main` as its own commit. End-to-end demos verified:
real bge-m3 embeddings, hybrid retrieval, graph nodes, CLIP image retrieval, semantic
cache hits (`cached: true`), and a config-matrix eval run.

## Working notes (decisions & deviations from blueprint)

- **Skeletons are pushed early**: infra and data-model phases land before any ML weights are
  downloaded, so the repo is reviewable and shippable at every commit.
- **Local models are lazy-loaded** and overridable via env (`METIS_EMBED_MODEL`) so tests and
  CI never download 2GB+ weights; default runtime stays `BAAI/bge-m3` per blueprint.
- **Embedding dim is config-driven** (`METIS_EMBED_DIM`) so switching MiniLM↔bge-m3 doesn't
  require a migration.
- **LLM gateway is provider-agnostic** with a fallback chain and a deterministic mock provider
  used in tests / when no key is set.
- **Neo4j GDS plugin** is enabled via `NEO4J_PLUGINS` in compose for PageRank/Louvain;
  `shortestPath()` connectivity (Phase 3) only needs core Cypher.
