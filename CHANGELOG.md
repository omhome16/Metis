# Changelog

All notable changes to Metis. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow the `pyproject.toml` `version` field.

## [Unreleased]

### Product build (multi-user + connectors + redesigned frontend)

**The demo became a product.** Multi-user accounts with per-user vault ownership,
EPUB ingestion, source connectors (URL / Readwise / Zotero), save-answer-as-note,
a vault-wide contradiction report, scoped answers, Obsidian export, the Wikipedia
agent tool removed (vault-only answers), and a complete frontend redesign in a warm
editorial style (auth screen, rebuilt chat with citations + selection picker,
interactive graph explorer, contradictions tab). See the `feat(product)` and
`feat(frontend)` commits for the full list.

- **Accounts**: `METIS_AUTH_MODE=users` (default) — scrypt + JWT, migration `0012`,
  first account adopts pre-accounts vaults. `token`/`none` modes preserved.
- **Frontend QA** (`scripts/frontend_qa.py`) rewritten for the new UI; registers its
  own `qa@metis.local` session automatically.

### Earlier: refactor + judgment layer

Audit → refactor → judgment-layer integration pass. Headline: **lint/format backlog
cleared to zero, access control added, and a TypeSafe Jev judgment layer integrated
at four points with a fifth and sixth instrumented.**

### Added

- **Judgment layer** (`app/judgment/`) — TypeSafe System One (Jev) as a sibling of the
  LLM gateway, because generating text and returning calibrated decisions are
  different contracts. `Choice`/`Noul`/`Score` primitives, typed answers, a real
  `typesafe-sdk` adapter, a deterministic mock, and `ask_quietly()` for
  degrade-not-fail semantics. See [`docs/jev.md`](docs/jev.md).
- **J1 — selection instead of generation for query filters** (`app/rag/metadata.py`).
  Corpus tags/authors/years are enumerated as `Choice` criteria, so a filter cannot
  reference a value the corpus lacks. One request covers all three dimensions.
  `scoped_query_metadata` falls back to the original LLM extractor.
- **J2 — calibrated contradiction verdicts** (`app/rag/contradiction.py`). The verdict
  is a `Noul` probability thresholded in code; ambiguous results escalate to the LLM
  judge. `check_contradiction` keeps its `{contradicts, reason}` contract.
- **J3 — batched eval judging** (`app/evals/metrics.py`). `faithfulness`,
  `context_precision`, and `context_recall` collapse from 1+N LLM calls to **one
  request per metric**. This is the direct fix for the quota-artifact zeros in the
  README's measured-results table.
- **J4 — `JudgmentReranker`** (`app/rag/rerank.py`) and **J5 — judgment lane routing**
  (`app/rag/router.py`), both opt-in comparison backends.
- **API token gate** (`app/core/security.py`): `METIS_API_TOKEN` protects routes when
  set (empty = previous behavior), accepted via Bearer / `X-API-Token` / `?token=`,
  compared with `compare_digest` on bytes. The SPA prompts once, stores it, and
  applies it to the SSE stream and download links too.
- **Security headers** — `nosniff`, frame-deny, referrer-policy, permissions-policy,
  and a CSP that keeps `script-src 'self'`; HSTS when `METIS_ENV=prod`.
- **Proxy-aware rate limiting** — `METIS_TRUST_PROXY_HEADERS` lets the limiter key on
  `X-Forwarded-For`; off by default because the header is client-forgeable. Also adds
  `Retry-After` to 429s.
- **GitHub Actions CI** (`.github/workflows/ci.yml`): lint, format check, and tests,
  with no thresholds. Deliberately no DB service containers.
- **Docs**: `docs/jev.md`, `docs/portfolio.md`, `CHANGELOG.md`, `MIGRATION.md`.

### Changed

- **Lint gate is absolute.** `ruff check` 231 → **0**; `ruff format` drift 42 files →
  **0**. E501 moved to the ignore list with reasoning inline, since `ruff format
  --check` is now a hard gate and owns line breaking.
- **Gateway deduplication** — Groq, Gemini, and ollama each re-implemented `chat`,
  `chat_stream`, `structured`, and `chat_tools_stream`. They now share
  `OpenAICompatProvider` (`app/gateway/openai_compat.py`): **−147 LOC**, one place to
  fix retries/usage/JSON-mode. No behavior change.
- `assemble_context` lost its dead `settings` parameter (no caller passed it).

### Fixed

- **`tests/test_feedback.py` called `client` without requesting the fixture** — a
  `NameError` that only fired when Postgres was reachable, so the DB-gated suite was
  broken while local runs skipped it happily.
- 4 unused imports, 1 dead local variable, 7 `E402` (mimetypes registration moved
  below the import block), 26 `B008` false positives (FastAPI `Depends` declared
  immutable), 3 `B905` `zip()` calls made explicit, 2 `B007` loop variables.

### Security

- No authentication existed on any endpoint. `METIS_API_TOKEN` is now available; the
  deployment doc's "put it behind your own gate" advice is satisfied by the app itself.
- Verified the markdown renderer escapes before transforming (`inline()` starts with
  `esc()`), so rendering model answer text through `innerHTML` is not an XSS path.
  Recorded here because it was checked, not assumed.
- **Dependency audit** (`pip-audit` over the exported runtime requirements). `pypdf`
  6.15.0 carried four advisories, fixed by bumping to **6.19.0**. `transformers
  4.57.6` carries eight advisories that are all fixed only in `transformers >= 5` —
  a hard pin here because 5.x cannot load `bge-m3`. That is an accepted risk with a
  stated reason and mitigations, documented in `docs/deployment.md`.
- **Secrets scan**: `.env` is untracked, and no key-shaped strings appear in any
  tracked file or anywhere in git history.

### Not done (honest gaps)

- **J4/J5 are unmeasured.** They need a live key and an ingested corpus. Run:
  ```bash
  METIS_JUDGMENT_BACKEND=typesafe METIS_RERANK_BACKEND=typesafe \
    uv run python -m scripts.run_matrix tech
  ```
  and compare `context_precision`, then keep the winner.
- **J6/cascade for graph extraction** (`app/graph/extraction.py` t2/t3) — designed,
  not implemented. It is a batch pipeline, not a hot path.
- **Public deployment** — deferred by request. The `render.yaml` blueprint as written
  cannot boot: the free tier is 512 MB / 0.1 CPU while the image loads `torch` +
  `bge-m3` (~2.3 GB). See `docs/deployment.md` for the measured options.
- **30 single-line `E501`s** were retired by policy rather than by hand-wrapping
  whitespace-sensitive Cypher strings in a file whose Neo4j tests are infra-gated.

## [0.1.0] — pre-audit baseline

FastAPI knowledge library: parent-child chunking, hybrid retrieval with RRF and graph
boost, cross-encoder rerank, semantic cache with a near-duplicate guard, ReAct agent,
Neo4j knowledge graph with GDS communities and delta-only auto-reorg, tiered graph
extraction, contradiction detection, OCR ingest, SSE streaming, a dependency-free
vanilla-JS SPA, and a RAGAS-style eval harness with enforced thresholds.
