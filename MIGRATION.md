# Migration guide

Changes that affect how you run, configure, or extend Metis. Nothing here breaks an
existing local setup by default — every behavior change is opt-in — but two items
(new environment variables, and a lint policy change) are worth reading.

## 1. No behavior change unless you opt in

`METIS_JUDGMENT_BACKEND` defaults to `llm`, `METIS_RERANK_BACKEND` to `cross-encoder`,
and `METIS_ROUTER_BACKEND` to `heuristic`. That means the retrieval, contradiction, and
eval paths behave exactly as they did before the judgment layer landed. If you do
nothing else, your answers and your eval numbers stay comparable.

To enable the judgment layer:

```bash
export TYPESAFE_API_KEY="ts-..."          # unprefixed, like GROQ_API_KEY
export METIS_JUDGMENT_BACKEND=typesafe
```

### Then re-measure, don't assume

Enabling it **changes** contradiction verdicts and eval scores, because a different
model with different calibration is answering. The README's measured-results table was
produced on the `llm` backend. After switching, re-run the matrix and update the table
rather than leaving two claimable sets of numbers:

```bash
uv run python -m scripts.run_matrix tech      # and: philosophy / Philosophy
```

If a specific integration underperforms, turn off just that one by leaving the global
backend on and switching the specific comparison backends off (they are separate
variables).

## 2. New environment variables

All optional; defaults preserve current behavior.

| Variable | Default | Notes |
|---|---|---|
| `METIS_JUDGMENT_BACKEND` | `llm` | `llm` \| `typesafe` \| `mock` \| `off` |
| `TYPESAFE_API_KEY` | *(empty)* | **Unprefixed.** `METIS_TYPESAFE_API_KEY` silently does nothing. |
| `METIS_TYPESAFE_MODEL` | `jev-latest` | Pin `jev-1.13.0` if you tune thresholds to a version. |
| `METIS_JUDGMENT_NOUL_UNCERTAIN_BAND` | `0.15` | Escalation band around 0.5. |
| `METIS_JUDGMENT_CONTRADICTION_THRESHOLD` | `0.5` | Where a `Noul` becomes a contradiction. |
| `METIS_RERANK_BACKEND` | `cross-encoder` | Or `typesafe` to compare. |
| `METIS_ROUTER_BACKEND` | `heuristic` | Or `llm` / `judgment`. `METIS_ROUTER_LLM` still works. |
| `METIS_API_TOKEN` | *(empty)* | **Empty = no auth.** See §3. |
| `METIS_TRUST_PROXY_HEADERS` | `false` | Enable only behind a proxy that overwrites `X-Forwarded-For`. |
| `METIS_CORS_ORIGINS` | `*` | Set explicit origins in prod (already in `.env.example`). |

Copy the new blocks from `.env.example`; they are grouped and commented.

## 3. Access control is now available — and it changes the client contract

`METIS_API_TOKEN` is **empty by default**, so local single-user use is unchanged. When
you set it, every protected route requires the token, and:

- `/healthz` and `/static/*` stay open (monitoring and the prompt itself).
- The SPA prompts once and stores the token in `localStorage`; no code change needed.
- **Scripted clients must now send it**: `Authorization: Bearer <token>` or
  `X-API-Token: <token>`. Browser downloads use `?token=` because an `<a href>` cannot
  set headers — so **a token in a URL is a token in your access logs.** If that matters,
  put the token behind your own reverse-proxy auth instead and leave this unset.

If you have an existing script or CI job hitting the API, it keeps working with
`METIS_API_TOKEN` unset. Setting the token is the step that requires updating clients.

## 4. Lint policy changed (for contributors)

`uv run ruff check .` is now expected to be **clean**: 231 violations → 0, and
formatting drift 42 files → 0. Two things changed to get there:

- **`E501` is ignored** (`pyproject.toml`), with the reasoning inline. `ruff format`
  is the structural gate and owns line breaking; `E501` only still fired on
  unbreakable tokens (long Cypher statements, one-line prompts). If you disagree,
  remove the ignore and wrap those 30 strings — nothing else depends on it.
- **FastAPI's `Depends`/`Query` are declared immutable** for `flake8-bugbear`, which
  silenced 26 `B008` false positives.

The thresholds previously documented in `AGENTS.md` ("≤ 283 violations, ≤ 54 files")
are gone; CI fails on any violation or any drift. `AGENTS.md` and
`docs/deployment.md` were updated to match.

## 5. CI is back, without the flaky gate

`.github/workflows/ci.yml` runs lint + format check + tests. The previous workflow was
removed (`074ba77`) because its `run_matrix` eval gate kept failing on fresh CI
databases — that gate needs an ingested corpus and is inherently stateful, so it
remains a **deliberate local gate**:

```bash
uv run python -m scripts.run_matrix tech    # thresholds: faithfulness ≥ 0.90,
                                            # context_precision ≥ 0.80,
                                            # citation_correctness == 1.0
```

## 6. Internal API changes (if you import Metis as a library)

- `app.rag.context.assemble_context(question, hits, image_captions=None)` — the dead
  `settings` parameter was removed. It had no callers; the static-analysis signal said
  so and a repository-wide search confirmed it.
- Gateway providers (`GroqProvider`, `GeminiProvider`, `OllamaProvider`) now subclass
  `OpenAICompatProvider` and take an already-built client. Constructors are unchanged:
  `GroqProvider(settings)`, `OllamaProvider(settings, transport=...)`. Only
  `GeminiProvider` keeps a provider-specific method (`describe_image`).
- `app.rag.metadata.extract_query_metadata` is unchanged and still available. The
  pipeline now calls `scoped_query_metadata(...)`, which prefers selection and falls
  back to `extract_query_metadata`.
- `check_contradiction(gateway, text_a, text_b) -> {"contradicts": bool, "reason": str}`
  is unchanged. Note that on the judgment backend `reason` is now a probability
  (`Noul p(contradiction)=0.93`) rather than prose, because Jev does not explain itself.
