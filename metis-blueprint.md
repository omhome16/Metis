# METIS — The Self-Organizing Knowledge Library

> **Working name (was "Neural Library"):** Metis — Greek goddess of wisdom and craft.
> **One-liner:** The library that reads itself. Drop in documents and images; Metis builds a knowledge graph of everything inside, answers questions with citations, surfaces cross-document connections you didn't know existed, and flags contradictions between sources.
> **Repo concept:** `metis/` — a fully independent service (own repo, own DB, own stack). The only shared *concept* with the other two projects (Clio, Argus) is the LLM gateway pattern; there is no shared code.

---

## 1. Why this project (market signal)

- RAG appears in ~35–40% of AI/ML job descriptions — it is the single most-listed GenAI skill.
- Almost every candidate has "chat with my PDF." Metis goes three levels deeper: **hybrid retrieval + knowledge graph traversal + contradiction detection + a real eval harness**. That is a senior-level story.
- Multimodal ingestion (images/diagrams) maps to the 2025–2026 shift toward multimodal agents.
- Employer checklist this project hits: RAG, context engineering, graph engineering, evals/harness, memory (caching), vector DBs, LLM APIs, production FastAPI, observability, cost control.

## 2. Product goals & success criteria

| Goal | Success criterion |
|---|---|
| Grounded Q&A with citations | ≥ 90% of answers include a correct citation to the corpus |
| Cross-document connection discovery | "Find the path between A and B" works via graph traversal, with evidence |
| Contradiction detection | Flags real conflicts between chunks with both sources shown |
| Multimodal support | Images are ingested, described, searchable, and usable in queries |
| Rigorous evaluation | A golden-dataset harness reports RAGAS-style metrics on every config change |
| Production quality | Dockerized, async, observable, deployable to a free-tier cloud host |

## 3. Core concepts covered (learning map)

| Concept | Where in Metis |
|---|---|
| **RAG** | Full pipeline: chunking → embeddings → hybrid retrieval → rerank → generation |
| **Context engineering** | Chunk-size experiments, query rewriting, context assembly with token budgets, citation packing |
| **Graph engineering** | Entity/relationship extraction → Neo4j knowledge graph → graph-augmented retrieval, PageRank importance, connection paths |
| **Harness (evals)** | Golden datasets (arts + tech), RAGAS metrics, regression runs, config comparisons |
| **Memory** | Semantic caching of repeated queries (Redis + embedding similarity) |
| **Multimodal** | Image ingestion (Gemini vision descriptions + local CLIP embeddings) and image-in, image-out querying |
| **vLLM (light touch)** | Optional: self-hosted embedding/reranker models instead of local ones (expansion) |

## 4. High-level architecture

```
                        ┌────────────────────────────────────────────┐
                        │                FRONTEND (yours)            │
                        │   chat UI · graph explorer · ingest UI     │
                        └───────────────┬────────────────────────────┘
                                        │ REST + SSE / WebSocket
                                        ▼
                        ┌────────────────────────────────────────────┐
                        │              METIS API (FastAPI)           │
                        │  /ask /search /ingest /graph/* /evals/*    │
                        │  LLM Gateway (Groq + Gemini adapters)      │
                        └───┬──────────────┬──────────────┬──────────┘
                            │              │              │
                 ┌──────────▼─────┐ ┌──────▼───────┐ ┌────▼─────────┐
                 │  WORKER        │ │  GRAPH       │ │  VECTOR+     │
                 │  (ingestion,   │ │  ENGINE      │ │  DOCS DB     │
                 │  extraction,   │ │  Neo4j       │ │  Postgres    │
                 │  evals)        │ │              │ │  + pgvector  │
                 └────────────────┘ └──────────────┘ └──────────────┘
                                        ┌────────────┐
                                        │  REDIS     │  semantic cache,
                                        │            │  job queue
                                        └────────────┘
```

**Services (docker-compose):** `api` (FastAPI), `worker` (ingestion + eval background jobs, e.g. arq/Celery with Redis), `db` (Postgres + pgvector), `graph` (Neo4j), `cache` (Redis).

## 5. Tech stack

| Layer | Choice | Why |
|---|---|---|
| API | FastAPI + Pydantic v2 + async (httpx, asyncpg, SQLAlchemy 2 async) | Standard, streaming-friendly, OpenAPI for your frontend |
| Streaming | Server-Sent Events (SSE) + optional WebSocket | Token streaming, job progress |
| Vector store | Postgres + pgvector | One DB for docs + vectors; simpler ops than a separate vector DB |
| Keyword search | Postgres full-text (`tsvector`) | Gives hybrid retrieval without extra infra (BM25-style ranking optional via `ts_rank`) |
| Graph | Neo4j (official `neo4j` Python driver) | Industry-standard property graph; Cypher; shortestPath() for connections; PageRank/Louvain via GDS |
| Embeddings | Local: `sentence-transformers` (`BAAI/bge-m3`, multilingual incl. Hindi; or `all-MiniLM-L6-v2`); image: `clip-ViT-B-32` | **Free, offline, no API cost.** Optional upgrade: Gemini `gemini-embedding-2` (multimodal) |
| Vision/description | Gemini Flash (vision) | Free-tier multimodal descriptions + structured entity extraction |
| LLM | Gateway: Groq (`llama-3.3-70b-versatile`, `llama-3.1-8b-instant`, `openai/gpt-oss-20b`) + Gemini (`gemini-2.5-flash` family) | Both free tiers; fallback chain |
| Reranker | Local cross-encoder (`BAAI/bge-reranker-base` via sentence-transformers) | Free, big retrieval-quality win |
| Task queue | arq or Celery + Redis | Ingestion + eval jobs run in the background |
| Caching | Redis | Semantic query cache |
| Observability | Langfuse (self-hosted OSS) + structured logs | Trace every LLM call, token cost, latency |
| Deploy | Docker Compose (local) → Render/Railway + Neo4j AuraDB Free | Free-tier cloud path |

## 6. Data model

### Postgres (+ pgvector)
```
documents(id, title, corpus, source_url, format, ingested_at)
chunks(id, doc_id FK, text, chunk_index, embedding vector(1024), tokens)
images(id, file_path, caption, tags text[], embedding vector(512))   -- CLIP dims
golden_questions(id, corpus, question, ground_truth, source_hint)
eval_runs(id, config jsonb, created_at, metrics jsonb)
```

### Neo4j schema (property graph)
```
(:Document {id, title, corpus})
(:Chunk    {id, text, index})
(:Image    {id, caption, tags})
(:Entity   {id, name, type})        -- Person | Place | Concept | Artwork | Work | Event | Organization | Technology
(:Topic    {id, name})

Relationships:
(Document)-[:CONTAINS]->(Chunk)
(Chunk)-[:MENTIONS {weight}]->(Entity)
(Image)-[:DEPICTS]->(Entity)
(Image)-[:BELONGS_TO]->(Document)          -- source image of a doc
(Entity)-[:RELATED_TO {weight, evidence_chunk}]->(Entity)   -- co-occurrence + extracted relations
(Entity)-[:AUTHORED_BY]->(Person)
(Entity)-[:BELONGS_TO]->(Topic)
(Chunk)-[:CONTRADICTS]->(Chunk)            -- written by the contradiction detector
```
Graph algorithms: **PageRank** (important entities) and **community detection** (topic clusters) via Neo4j GDS; **shortestPath()** (connection discovery between two entities) is a core Cypher function.

## 7. LLM gateway (pattern shared conceptually with Clio/Argus — implemented independently per repo)

```
LLMClient (interface)
 ├── chat(messages, model, temperature, max_tokens) -> ChatResult(text, usage)
 ├── chat_stream(...)  -> async generator of tokens
 ├── structured(...)   -> JSON via response_format/json_schema
 └── describe_image(image, prompt) -> text        (Gemini adapter only)
```

- **GroqProvider** — OpenAI SDK with `base_url="https://api.groq.com/openai/v1"`. Models: `llama-3.3-70b-versatile` (default), `llama-3.1-8b-instant` (cheap/fast), `openai/gpt-oss-20b`. Env: `GROQ_API_KEY`.
- **GeminiProvider** — either the `google-genai`/`google-generativeai` SDK or the OpenAI-compatible endpoint `https://generativelanguage.googleapis.com/v1beta/openai/`. Models: `gemini-2.5-flash` (and newer flash variants — pin current names via the models endpoint). Env: `GEMINI_API_KEY`. Used for vision, structured extraction, and judging.
- **Fallback chain**: primary → secondary → error. Config per task (e.g., extraction → Gemini, generation → Groq 70B, rerank-check → Groq 8B).
- **Cost guardrails**: per-request `max_tokens`, per-day token budget meter, request timeouts, retries with exponential backoff (honor 429s/rate limits).

> Facts verified against official docs at blueprint time (2026): Groq API is OpenAI-compatible at `https://api.groq.com/openai/v1`; Gemini exposes an OpenAI-compatible layer at `https://generativelanguage.googleapis.com/v1beta/openai/`. Free tiers exist for both with daily rate limits — **always check the current limits in the consoles**; the worker scheduler must spread load.

## 8. Workflows

### 8.1 Ingestion pipeline (worker)

```
Client ──POST /ingest (files+corpus)──▶ API ──job──▶ Redis queue ──▶ Worker
                                                                      │
  1. Normalize: PDF → text (pypdf) · markdown/txt direct · image files
  2. Chunk: configurable strategy (fixed-size with overlap | semantic)
  3. Embed: local bge-m3 → vector; store chunk row (pgvector)
  4. Extract: Gemini structured output → entities + relations (JSON)
  5. Upsert: Neo4j — merge Entities, create MENTIONS/RELATED_TO,
     link Chunk→Document, Image→DEPICTS
  6. Images: Gemini vision → caption + tags → CLIP embedding → Image node
  7. Post-process: PageRank refresh on affected subgraph (optional job)
  8. Update job status (progress %, per-file errors) → SSE to frontend
```

### 8.2 Query pipeline (request path)

```
POST /ask {question, corpus?, stream}
 │
 ├─ 1. Semantic cache lookup (Redis: embedding-sim > 0.92 → cached answer)
 ├─ 2. Query rewrite/expansion (optional LLM step — Groq 8B)
 ├─ 3. Retrieve
 │      a. vector: pgvector top-k (bge-m3)
 │      b. keyword: Postgres tsvector top-k
 │      c. graph: extract entities (Gemini) → Neo4j 1-2 hop neighbors
 │         → collect chunks mentioning those entities
 ├─ 4. Fuse (Reciprocal Rank Fusion) + metadata filter (corpus)
 ├─ 5. Rerank top-20 → top-5 (local bge-reranker-base)
 ├─ 6. Context assembly (context engineering): token budget, dedupe,
 │      order by relevance, pack citation spans [doc:chunk]
 ├─ 7. Generate (Groq 70B) with citation-requiring system prompt
 ├─ 8. Grounding pass: verify each citation maps to an actually
 │      retrieved chunk; drop unverifiable claims (heuristic + optional judge)
 ├─ 9. Contradiction scan: for answer claims, check CONTRADICTS edges
 │      among used chunks → surface "Sources disagree" alert
 └─ 10. SSE stream: {sources} → {tokens...} → {citations} → {connections} → {done}
```

### 8.3 Connection discovery

```
GET /graph/connections?from=Gandhi&to=Nehru
 → resolve both names to Entity nodes
 → Cypher shortestPath (all-paths limit 3)
 → return path with edge evidence (chunk ids) + generated explanation
```

## 9. Context engineering design (the interview story)

| Technique | Detail |
|---|---|
| Chunking | Configurable: fixed 256/512/1024 with overlap vs semantic (embedding-similarity splits). **Experiments are recorded**, not guessed (harness measures which wins per corpus). |
| Query rewriting | LLM expands vague queries ("tax deductions for freelancers" → domain terms) before retrieval. |
| Context budget | Max tokens per prompt (e.g., 6k) — allocate: retrieved chunks (weighted), instructions, few-shot. Truncate lowest-relevance chunk last. |
| Citation packing | Each chunk carries `[n]`; answer must emit `[n]` inline; grounding pass validates. |
| Corpus isolation | Metadata filtering keeps arts and tech corpora separated. |
| Dedup + ordering | RRF order preserved; near-duplicate chunks removed before assembly. |

## 10. Multimodal (images)

- **Ingestion:** images → Gemini vision caption + tags → CLIP embedding (`clip-ViT-B-32`, local/free) → `Image` node + `DEPICTS` edges.
- **Arts corpus:** paintings (public-domain images, e.g., Wikimedia Commons) get artist/movement/period/technique tags — queryable ("show all Impressionist works referencing water").
- **Query:** `POST /ask` accepts an optional image → embed with CLIP → retrieve similar images → ask Gemini to answer *about* the image with the retrieved context.
- **Note:** real-time image-gen (cover art etc.) is a frontend concern (your call); Metis focuses on ingestion, retrieval, and understanding of images.

## 11. Demo corpora (two built-in cases)

| Corpus | Sources | Notes |
|---|---|---|
| **Arts** | Public-domain literature (Project Gutenberg), public-domain art-history texts, public-domain painting images | Fully shareable/citable — great for the demo and for evals |
| **Tech** | Official docs (e.g., FastAPI, PyTorch guides), arXiv papers, code samples | Licenses permit redistribution; cite sources |

Add your own corpus via the ingest API at any time (e.g., your course notes).

## 12. Eval harness (built-in, Argus-compatible output)

- **Golden datasets:** ~30–50 hand-written questions per corpus with `ground_truth` + expected source hints (curate alongside ingestion).
- **Metrics** (RAGAS — open-source library; metrics `Faithfulness`, `AnswerRelevancy`, `ContextPrecision`, `ContextRecall`; plug your own LLM/embedding wrappers pointed at Groq/Gemini OpenAI-compatible endpoints; dataset rows as `{question, answer, contexts, ground_truth}`):
  - Faithfulness (claims grounded in context)
  - Answer Relevancy
  - Context Precision / Context Recall
- **Custom metrics:** citation correctness (does each `[n]` map to a retrieved chunk), contradiction-detection accuracy, latency p50/p95, cost-per-answer (metered from gateway).
- **Config matrix:** chunk_size × embedding × reranker on/off × graph-boost on/off × model → run harness → comparison report.
- **Regression:** re-run on any prompt/ingestion change; store runs in Postgres; export JSON in the Argus format so Argus can consume the same datasets.

## 13. API contract (what your frontend calls)

| Endpoint | Method | Purpose |
|---|---|---|
| `/healthz` | GET | Liveness |
| `/api/v1/ingest` | POST (multipart) | Upload files + corpus → `job_id` |
| `/api/v1/ingest/{job_id}` | GET | Job progress (also SSE `/api/v1/ingest/{job_id}/stream`) |
| `/api/v1/corpora` | GET | List corpora + doc counts + graph stats |
| `/api/v1/ask` | POST | Ask (returns SSE stream; body: `{question, corpus?, image?, options}`) |
| `/api/v1/search` | GET | Raw hybrid search results `{q, top_k}` |
| `/api/v1/graph/explore` | GET | Subgraph around an entity `{entity, depth}` |
| `/api/v1/graph/connections` | GET | Path between two entities `{from, to}` |
| `/api/v1/graph/stats` | GET | Node/edge counts, top entities by PageRank |
| `/api/v1/cache/stats` | GET | Semantic cache hit rate |
| `/api/v1/evals/run` | POST | Run harness `{dataset_id, config}` |
| `/api/v1/evals/reports` | GET | Past eval runs + metrics |
| `/ws/ask` | WebSocket | (Optional) interactive streaming alternative to SSE for ask — add only if the frontend prefers WS |

**SSE event shapes** (`/ask`):
```json
{"event":"sources","data":{"chunks":[{"id":"c1","doc":"...","text":"...","score":0.93}]}}
{"event":"tokens","data":{"text":"The answer begins..."}}
{"event":"citations","data":{"citations":[{"n":1,"chunk_id":"c1","doc":"..."}]}}
{"event":"connections","data":{"paths":[...]}}
{"event":"contradiction","data":{"alert":"sources disagree","chunks":["c1","c7"]}}
{"event":"done","data":{"answer_id":"...","usage":{"in":1234,"out":210},"cost_usd":0.0004}}
```

## 14. Production concerns

- **Observability:** Langfuse traces per query (retrieval steps, prompt, tokens, cost); structured logs; `/healthz` + readiness checks on Postgres/Neo4j/Redis.
- **Cost control:** local embeddings/rerankers = $0; LLM calls metered; semantic cache reduces repeat spend; free-tier rate limits respected by the scheduler (spread + backoff).
- **Reliability:** idempotent ingestion (upsert by content hash), per-file error isolation, retries, dead-letter queue.
- **Testing strategy:** unit tests for chunking/embedding/rerank logic and metric math; contract tests for SSE payload shapes (sources → tokens → citations → done); a small CI smoke eval (one golden question through the whole pipeline) so regressions are caught before deploy.
- **Security (baseline):** no auth for now (single-user local); keep API keys in env vars / secrets; PII caution for uploaded docs; rate-limit the API. JWT multi-user = future work.
- **Deploy:** Docker Compose locally (all 5 services). Cloud: Render/Railway (api + worker + managed Postgres/Redis) + **Neo4j AuraDB Free** for the graph (small limits — fine for demo corpora). Images optional (local volume or object storage).

## 15. Milestones (no time estimates — sequence only)

- **M1 — Skeleton:** FastAPI + docker-compose + Postgres/pgvector + Redis; `/ingest` stores raw docs.
- **M2 — Retrieval + ask:** chunking, local embeddings, vector search, generation via gateway, SSE streaming.
- **M3 — Graph:** entity/relation extraction → Neo4j; graph-boosted retrieval; `/graph/*` endpoints.
- **M4 — Multimodal:** image ingestion + CLIP + vision descriptions; image-aware ask.
- **M5 — Context engineering:** query rewriting, hybrid search (vector + tsvector + RRF), reranker, citation grounding.
- **M6 — Harness:** golden datasets (arts + tech), RAGAS metrics, config matrix, regression.
- **M7 — Production hardening:** semantic cache, Langfuse, error handling, deploy to Render/Railway + AuraDB.

## 16. Deliverables / portfolio artifacts

- README case study (problem → architecture diagram → eval results table → cost analysis → failure modes).
- Live demo corpora (arts + tech) anyone can query.
- The eval report comparing chunking/embedding/rerank/graph-boost configs (numbers, not vibes).
- Optional blog: "How I built a library that reads itself" (ingestion → graph → contradiction detection).

## 17. Stretch goals

- Contradiction resolution agent (which source is more credible — PageRank + recency).
- Self-hosted embeddings/reranker behind **vLLM** (see Argus expansion pack) instead of local sentence-transformers — for throughput at scale.
- Multi-user + workspaces (JWT auth, per-user corpora).

## 18. References (sources of truth — verify before coding)

- Groq API: https://console.groq.com/docs · https://api.groq.com/openai/v1
- Gemini API: https://ai.google.dev/gemini-api/docs · OpenAI-compat: https://ai.google.dev/gemini-api/docs/openai
- pgvector: https://github.com/pgvector/pgvector
- Neo4j: https://neo4j.com/docs/ · GDS: https://neo4j.com/docs/graph-data-science/
- sentence-transformers: https://www.sbert.net/
- RAGAS: https://docs.ragas.io
- FastAPI: https://fastapi.tiangolo.com/
- Langfuse: https://langfuse.com/docs
- Project Gutenberg: https://www.gutenberg.org · arXiv: https://arxiv.org
- Neo4j AuraDB Free: https://neo4j.com/cloud/aura/
