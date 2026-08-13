# Metis 2026 Roadmap — Design Spec

- **Date:** 2026-08-13
- **Status:** Approved (user: "do all, full freedom; update the docs later properly")
- **Constraint set (non-negotiable):** CPU-only torch; `transformers<5`; mock-model hermetic test suite; pgvector/Redis/Neo4j(+GDS) only — no new services; numbered alembic migrations for every schema change; `ruff` clean; frontend QA script after UI changes; SSE ask contract stays backward-compatible.

## Why

Codebase audit + 2026 research (router-first architectures, LazyGraphRAG, parent-child chunking, pgvector HNSW/halfvec/iterative scans, grounded semantic caching) surfaced: exact-scan vector retrieval with no ANN index, O(n) Redis semantic-cache scans with no corpus invalidation, no query router (every query pays full pipeline cost), graph limited to local entity/neighbor retrieval (no global sensemaking), no metadata filtering, exact-name-only entity identity, scanned PDFs silently yielding zero chunks, no user feedback, no CI evals.

## Phase overview

| Phase | Theme | Components |
|---|---|---|
| P1 | Foundations | HNSW+halfvec index, doc metadata, corpus_version |
| P2 | Efficiency | NLP-default extraction, entity normalization, cache v2 |
| P3 | Quality | Parent-child chunking, metadata filtering |
| P4 | Flagship | Semantic router (Fast/Standard/Deep) |
| P5 | Flagship | Global graph sensemaking (Leiden + community summaries) |
| P6 | Productization | OCR fallback, feedback loop, CI evals, local LLM provider |
| P7 | Docs | README, docs/, learning vault (+ PNG re-render) |

Sequential order P1→P7; check in with user at each phase boundary with fresh eval numbers.

---

## Phase 1 — Vector & schema foundations

### 1.1 HNSW + halfvec

- Migration `0006_hnsw_halfvec.py`:
  - `chunks.embedding` → `HALFVEC(1024)` (config-aware: `METIS_EMBED_DIM`, default 1024; mock is 8 — dimension must match runtime config; migration is a no-op reshape via `USING embedding::halfvec(...)`).
  - `CREATE INDEX ix_chunks_embedding_hnsw ON chunks USING hnsw (embedding halfvec_cosine_ops) WITH (m = 16, ef_construction = 64)`.
  - Same for `images.embedding`.
- Query side (`app/rag/retrieval.py`): set `SET hnsw.ef_search = 120` and `SET hnsw.iterative_scan = relaxed_order` per session (engine event hook in `app/db/`); keep `cosine_distance` operator (pgvector auto-casts halfvec in `<=>`).
- Verification gate: existing eval matrix must not regress (context precision/recall within noise); `EXPLAIN` shows index scan, not seq scan.

### 1.2 Document metadata

- `documents` + columns: `tags ARRAY(String(128)) default []`, `doc_date DateTime(timezone=True) nullable`, `author String(256) nullable`.
- Admin endpoint `PATCH /api/v1/documents/{id}/metadata` (tags/date/author). Optional LLM auto-extraction at ingest behind `METIS_DOC_META_EXTRACT` (default off for P1; on later).
- Frontend: documents view shows tag chips; no edit UI in P1 (API only) — keeps UI diff minimal.

### 1.3 corpus_version

- Table `corpus_versions(corpus PK, version int, updated_at)`.
- Bump on any successful document ingest/delete (in the same transaction path as the doc write).
- Exposed: `GET /api/v1/healthz` payload + response headers on ask (e.g. `X-Metis-Corpus-Version`); consumed by cache v2 (P2).

### 1.4 Tests (P1)

- Migration smoke (models match migration), HNSW usage (EXPLAIN contains `hnsw`), metadata CRUD, version bump on ingest + delete, version untouched on no-op re-ingest.

---

## Phase 2 — Efficiency: lazy extraction + entity normalization + cache v2

### 2.1 NLP-default graph extraction (LazyGraphRAG move)

- Ingest path uses `extract_entities_fallback` (regex Capitalized Phrases) as **default**; LLM typed-relations pass behind `METIS_GRAPH_LLM_EXTRACT` (default `true` for now — quality; document toggle). Graph build becomes offline-free when toggled.
- Keep LLM used for per-query graph boost extraction (query-time deferral — already LazyGraphRAG-style).

### 2.2 Entity normalization / aliasing

- Normalize at write: case-fold + whitespace-collapse + strip surrounding punctuation before `MERGE` (both LLM and regex paths).
- `Entity.alias_of` property + merge pass: on ingest, if normalized name already exists (or matches an alias row), re-point edges to canonical node instead of creating a duplicate. Keep `name` unique constraint.
- Idempotent: re-ingest never creates dupes.

### 2.3 Cache v2 (grounded, indexed, versioned)

- New table `cache_entries`: `id`, `corpus`, `question`, `question_embedding HALFVEC` (HNSW-indexed, same dim), `answer`, `sources JSON`, `citations JSON`, `done JSON`, `model VARCHAR`, `embed_model VARCHAR`, `corpus_version INT`, `created_at`, TTL enforced by expiry filter (no Redis TTL; use `expires_at`).
- Lookup: 1 SQL query — `ORDER BY question_embedding <=> :q LIMIT 1` where cosine ≥ threshold and `corpus_version` matches current and `expires_at > now()`. No scan_iter, no Python cosine.
- Key concerns: cache entries must never break ask (try/except), hit replays same SSE contract (`cached: true`), staleness bounded by corpus_version + 7-day expiry.
- Backfill/migration: keep Redis read path as fallback for one release? No — single write path to Postgres; Redis remains queue-only (document in docs).
- Negative-feedback eviction hook reserved (P6).

### 2.4 Tests (P2)

- Cache hit/miss, semantic hit (paraphrase ≥ threshold), version-mismatch → miss, expired → miss, alias merge idempotency, regex-default extraction parity with LLM on tech corpus.

---

## Phase 3 — Quality: parent-child chunking + metadata filtering

### 3.1 Parent-child (small-to-big)

- New table `parent_chunks(id, doc_id FK, text, start_chunk_idx)` + `chunks.parent_id FK nullable`.
- Ingest: paragraph grouping → parents (target ~2000 chars, structure-preserving) → children (target ~400 chars, embedded + HNSW-indexed). Children carry `parent_id`; parents not embedded.
- Retrieval: search children → rerank children (top 20 → 5) → fetch parents → dedupe by parent_id → parents become the `[n]` context blocks (kept/dropped citation accounting unchanged). Parent → chunk mapping kept for source display.
- Kill switch `METIS_PARENT_CHILD` (default on) with flat fallback (old behavior) — A/B-able via eval matrix.
- Re-ingest of existing corpora required for the new structure (documented; demo corpora re-ingest script).

### 3.2 Metadata filtering

- LLM (best-effort, JSON) extracts `{tags: [...], date_from, date_to, author}` from the query; empty on failure.
- Applied as SQL filters on both vector and keyword arms (uses P1 iterative scans); corpus filter still first.
- Filters surfaced in the SSE `meta` event and eval harness.

### 3.3 Eval extension

- New golden questions: metadata-scoped ("the 2024 policy on X"), cross-section ("how does section A relate to section B"). Metrics unchanged (faithfulness/context precision/recall/citations); compare flat vs parent-child configs.

---

## Phase 4 — Semantic router (Fast / Standard / Deep)

- `app/rag/router.py`: `route(question, ...) -> Lane` where Lane ∈ {fast, standard, deep}.
  - **fast**: greetings, thanks, single-token/whitespace-trimmed trivial, explicit "no retrieval" phrasing → direct LLM chat, no retrieval, no cache write. Zero DB queries.
  - **standard**: default — hybrid + rerank (+ metadata filters), no agent, no graph.
  - **deep**: agent + graph tools + (P5) global search; triggered by heuristic signals (multi-hop markers: "compare", "how does X relate to Y", "across", "summarize the corpus") or explicit `mode=deep` param.
  - Heuristic-first (keyword/length/punctuation rules), optional LLM refinement behind `METIS_ROUTER_LLM`; any failure → standard.
- Ask route: lane recorded in SSE `meta` event + message row (`route` column? keep in `usage` JSON — no migration needed) + Langfuse span attribute.
- Contract unchanged: same event sequence for all lanes.

### Tests (P4)

- Routing matrix: greeting→fast (assert zero retrieval queries via counting), fact→standard, multi-hop→deep, LLM-down→standard, image queries never deep (unchanged), per-lane cost assertions.

---

## Phase 5 — Global graph sensemaking

- **Communities:** at graph build (or on-demand job `POST /api/v1/graph/communities`), run GDS Leiden/Louvain over `RELATED_TO` (weighted, undirected): `Entity.community_id` + `Entity.community_rank`.
- **Summaries:** per community, LLM summary (one call per community; deterministic, stored as `CommunitySummary` nodes `(:Community {id, summary, entity_count})`). Cheap: ~dozens of communities.
- **Global queries:** lane `deep` + intent keywords ("themes", "summarize the library", "what does the corpus say about") → gather community summaries (top-k by size/rank) → map-reduce generation with citations mapped to member entities/chunks. Honesty discipline: cite communities; fallback to standard if GDS unavailable (AuraDB caveat) or summaries missing.
- Optional `METIS_GLOBAL_RELEVANCE_BUDGET` knob.

### Tests (P5)

- Community assignment deterministic on fixture graph; global answer cites communities; degraded mode (no GDS) falls back cleanly; summaries idempotent per community.

---

## Phase 6 — Productization

- **OCR:** optional `METIS_OCR_ENGINE=pytesseract` (tesseract binary required) for PDFs with zero extracted text; otherwise explicit `extraction_status=empty` on the document + UI badge + ingest log warning. Never silent.
- **Feedback:** `POST /api/v1/ask/{message_id}/feedback {rating: -1|1, note?}` → `feedback` table (message_id FK, rating, note, created_at). UI: thumbs in ask view. **Negative feedback evicts matching cache entries** (by message.sources question embedding) and is surfaced in eval tooling.
- **CI evals:** GitHub Actions workflow `.github/workflows/ci.yml`: `uv sync --frozen` → `ruff check` → `pytest` → `run_matrix tech` with thresholds (faithfulness ≥ 0.90, context precision ≥ 0.80, citation correctness == 1.0). Neo4j/Postgres/Redis via compose services in the job (or skip-if-down semantics: thresholds enforced only when DBs up). Fail PR on regression.
- **Local LLM provider:** optional `ollama` gateway client (OpenAI-compatible `http://localhost:11434/v1`), task-routed like others, `supports_tools` per configured model; documented CPU cost; mock remains default without keys.

### Tests (P6)

- Feedback endpoint CRUD + cache eviction on negative; OCR path unit test with tiny scanned fixture (skip if tesseract absent); ollama client test with MockTransport (no real server).

---

## Phase 7 — Docs (explicit deliverable)

- `README.md`: new features, measured results tables, env/config additions.
- `docs/architecture.md`: router lanes, cache v2, parent-child, communities, feedback, updated request flows.
- `docs/deployment.md`: new env vars (METIS_*), OCR, CI, GDS caveats.
- `AGENTS.md`: command additions (CI, run_matrix thresholds), new env vars, architecture map updates.
- Learning vault (`learning doc/`, gitignored by design — still updated):
  - Part III: pipeline diagrams + router/parent-child additions; Part VI: new tradeoff entries; Part VII: 2026 alignment update (router-first, LazyGraphRAG, small-to-big); Part VIII: new micro-labs; Part IX: new code walkthroughs.
  - Re-render all new/changed Mermaid diagrams to PNG via existing mermaid-cli pipeline (`assets/` + `diagram-sources.md` contract).

---

## Cross-cutting rules

- Every phase: alembic migration (when schema changes), hermetic tests, `uv run ruff check .` + `ruff format --check`, `uv run pytest` green, frontend QA after UI changes, eval re-run with numbers reported at phase boundary.
- API/SSE backward compatibility; new behavior behind env kill-switches where it changes retrieval semantics (parent-child, router, LLM extraction).
- No new external services; no GPU; no model upgrades that violate pins.
