# JEV in Metis

A dedicated reference for the judgment layer: what it is, why Metis has it, exactly
where it is wired in, how to configure it, and what to do when it misbehaves.

- [What JEV is](#what-jev-is)
- [Why Metis has a judgment layer](#why-metis-has-a-judgment-layer)
- [Where JEV is integrated](#where-jev-is-integrated)
- [Where JEV is deliberately not used](#where-jev-is-deliberately-not-used)
- [Configuration](#configuration)
- [Examples](#examples)
- [Cost and limits](#cost-and-limits)
- [Troubleshooting](#troubleshooting)
- [Verifying the integration](#verifying-the-integration)

## What JEV is

**Jev** is TypeSafe's flagship **System One** model. A System One model does not
generate text: it ingests a `state` once, evaluates every question against it *in
parallel*, and returns **typed answers with calibrated probabilities** that code can
threshold directly.

| Primitive | Question it answers | Response fields |
|---|---|---|
| `Choice` | one of a defined set | `choice`, `confidence`, `probabilities` |
| `Noul` | probability a condition holds | `noul` **only** — no separate confidence |
| `Score` | degree along ordered levels | `score`, `confidence`, `legend`, `probabilities` |

Interfaces: `POST https://api.typesafe.ai/v1/systemone`, or the `typesafe-sdk` Python
package (`>=0.5.7`; Metis pins a resolved `0.7.0`). Metis uses the async client.
Docs: <https://docs.typesafe.ai>.

Two properties shape every design decision here:

1. **One state, many questions, one request.** Independent questions over the same
   state run in a single call — TypeSafe's own benchmark reports 12.2× cheaper and
   10× faster than issuing them separately. This is why the batched integrations
   below replace *per-item* LLM calls rather than mirroring them.
2. **It returns decisions, not explanations.** There is no prose to show a user, so
   where the UI previously displayed a model's rationale we now surface the
   probability itself.

## Why Metis has a judgment layer

Metis could already *generate* (`app/gateway/`), but it could not *decide*. Every
decision in the pipeline was really a prompt whose free-text reply was parsed and
hoped about:

- does this source contradict that one? → `{"contradicts": true|false}` parsed from JSON
- which tag does this query mean? → LLM *writes* a tag, which may not exist in the corpus
- is this claim supported by the context? → one LLM call **per claim**, per metric

Three concrete problems followed, all visible in the project's own history:

- **Invented values.** Commit `7eb4ea8` had to add a post-hoc check that discarded
  LLM-invented tags the corpus did not carry. The constraint was still fabricated.
- **Quota cliffs.** Per-item judge calls exhausted a free tier mid-matrix, which
  silently degraded to the mock provider and produced the "quota-artifact zeros"
  the README documents.
- **Fragile parsing.** A malformed reply on a user-visible path became a wrong
  verdict rather than an error.

A judgment model addresses all three: values are *selected* from options code
enumerates, judgments are *batched*, and the output is a number rather than a parse.

## Where JEV is integrated

| # | Location | What changed | Default |
|---|---|---|---|
| J1 | `app/rag/metadata.py` | Corpus tags/authors/years are enumerated and offered as `Choice` criteria, so a filter can only name values that exist. One request covers all three dimensions. `scoped_query_metadata` falls back to the original LLM extractor. | `llm` |
| J2 | `app/rag/contradiction.py` | The verdict is a `Noul` probability, thresholded in code. An ambiguous result defers to the LLM judge. `check_contradiction` keeps its `{contradicts, reason}` contract. | `llm` |
| J3 | `app/evals/metrics.py` | `faithfulness`, `context_precision`, `context_recall` batch every item into **one** request per metric instead of 1+N LLM calls. `JudgeSpec` defines each judgment once so both backends ask the same question. | `llm` |
| J4 | `app/rag/rerank.py` | `JudgmentReranker` scores all candidates in one request — a comparison backend to be measured, not a presumed upgrade. | `cross-encoder` |
| J5 | `app/rag/router.py` | Lane chosen by a single `Choice` (`METIS_ROUTER_BACKEND=judgment`). | `heuristic` |
| — | `app/graph/extraction.py` | **Not integrated.** See below. | — |

Supporting code:

```
app/judgment/
  base.py          Choice/Noul/Score + typed answers + JudgmentClient protocol
  typesafe.py      AsyncTypeSafeClient adapter (lazy SDK import)
  mock.py          deterministic, key-free client for tests
  calibration.py   get_judgment_client() + ask_quietly() + the policy doc
```

### Two rules enforced by design

**Policy lives in code.** Thresholds are settings, not prompt text, so they can be
changed without re-running inference and can be evaluated against your own data:

- `METIS_JUDGMENT_CONTRADICTION_THRESHOLD` — where a `Noul` becomes a contradiction.
- `METIS_JUDGMENT_NOUL_UNCERTAIN_BAND` — *the escalation rule.* A `Noul` within this
  distance of `0.5` means "similar probability either way" — **not** medium
  intensity. Those cases escalate to the LLM judge instead of acting. This is the
  main accuracy guard, and it is tested.

**Nothing is load-bearing alone.** `ask_quietly()` converts any backend failure into
`None`, and every caller treats `None` as "use the original path". A judgment outage
degrades a feature; it never fails a request.

## Where JEV is deliberately not used

| Rejected | Why |
|---|---|
| `app/rag/vision.py`, CLIP image path | **Jev accepts text only.** No image, audio, or video input. Vision stays on Gemini flash. |
| Embeddings, chunking, retrieval SQL, RRF fusion, HNSW tuning, cache cosine | There is no judgment to make. JEV would add latency and cost for a worse answer. |
| Answer generation, community summaries, "surprises" / journey narration | Jev **does not generate text or explanations.** Stays on Groq/Gemini. |
| `app/graph/extraction.py` (t2/t3) | The natural fit is an extraction cascade (regex proposes, JEV verifies/classifies) but it is a batch pipeline, not a hot path, and it is not implemented here rather than half-done. Listed as a follow-up in `CHANGELOG.md`. |

## Configuration

`METIS_JUDGMENT_BACKEND` selects the backend. **The default is `llm`, which means the
behavior that shipped before this layer existed** — so nothing changes until it is
switched on and measured.

| Variable | Default | Meaning |
|---|---|---|
| `METIS_JUDGMENT_BACKEND` | `llm` | `llm` (original paths) \| `typesafe` (Jev) \| `mock` (deterministic, no network) \| `off` |
| `TYPESAFE_API_KEY` | *(empty)* | Canonical unprefixed name, like `GROQ_API_KEY`. Empty disables the backend with a warning. |
| `METIS_TYPESAFE_MODEL` | `jev-latest` | Alias or pinned ID (`jev-1.13.0`). Pin the ID if you tune thresholds to a version. |
| `METIS_JUDGMENT_NOUL_UNCERTAIN_BAND` | `0.15` | Inside this distance of 0.5 → escalate to the LLM judge. |
| `METIS_JUDGMENT_CONTRADICTION_THRESHOLD` | `0.5` | `Noul` at or above this counts as a contradiction. |
| `METIS_RERANK_BACKEND` | `cross-encoder` | Or `typesafe` to compare. |
| `METIS_ROUTER_BACKEND` | `heuristic` | Or `llm` / `judgment`. |

```bash
# Enable it
export TYPESAFE_API_KEY="ts-..."        # https://console.typesafe.ai
export METIS_JUDGMENT_BACKEND=typesafe
uv run uvicorn app.main:app --reload
```

Because `MOCK` is a real backend, the whole layer can be exercised with no key:

```bash
export METIS_JUDGMENT_BACKEND=mock
```

## Examples

Build a question and ask it — this is the entire adapter surface:

```python
from app.judgment.base import Choice, Noul
from app.judgment.calibration import ask_quietly, get_judgment_client

client = get_judgment_client()  # None when the backend is `llm`/`off`
result = await ask_quietly(
    client,
    {"passage_a": a, "passage_b": b},
    {
        "contradicts": Noul(
            instructions="Do the passages contradict each other about the same subject?",
            true_description="They make opposing claims about the same subject.",
            false_description="They agree, or they are about different subjects.",
        ),
    },
)
noul = result.noul("contradicts")  # None if unavailable → callers fall back
```

Selecting from enumerated options (the shape that made invention impossible):

```python
from app.judgment.base import Choice

questions = {
    "tag": Choice(
        instructions="Which single tag does the question restrict to? Answer 'none' otherwise.",
        criteria={
            **{t: f"The question is about documents tagged {t!r}." for t in corpus_tags},
            "none": "The question does not restrict by tag.",
        },
    ),
}
```

Run it directly from the CLI:

```bash
# Contradiction judgment, mock backend, no key needed
METIS_JUDGMENT_BACKEND=mock uv run pytest -q tests/test_judgment.py
```

## Cost and limits

| | Jev 1.13 | |
|---|---|---|
| Price | **$0.042 / Mtok input** | Output tokens are **free** |
| Rate limits | 250,000 tokens/sec, 1,200 requests/min | vs a Groq free tier of ~100k tokens/day |
| Context | 64k per request | 32k for `state` + the longest single question |
| Input | text only | strings, JSON objects, arrays |

Batching is what makes this cheap: a 20-candidate rerank is **one** request, not 20.
Metis records `input_tokens` and exposes `JudgmentResult.cost_usd`, so the effect is
measurable rather than asserted.

> **Not yet measured on this corpus.** J4 and J5 are wired and tested but their
> accuracy versus the cross-encoder and the heuristic router has **not** been
> measured — that needs a live key and an ingested corpus. The commands to run are in
> `CHANGELOG.md`. Until then, treat J1–J3 as the integrations with evidence and
> J4–J5 as instrumented experiments.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Log: `METIS_JUDGMENT_BACKEND=typesafe but TYPESAFE_API_KEY is unset — judgment disabled` | Key missing. Note the **unprefixed** name: `METIS_TYPESAFE_API_KEY` silently does nothing. |
| Log: `typesafe-sdk is not installed` | `uv add typesafe-sdk` (the import is lazy on purpose so Metis still runs without it). |
| Log: `judgment backend typesafe failed: ...` | Any failure is swallowed by `ask_quietly` and the original path runs. Check the key, then network egress. |
| Log: `contradiction judgment ambiguous (p=0.52) — deferring to the LLM judge` | Working as designed. Lower `METIS_JUDGMENT_NOUL_UNCERTAIN_BAND` to act on more marginal cases (or raise it for more caution). |
| Log: `judgment response answered 2/4 questions` | A partial response is treated as unusable for rankings and metrics; the fallback runs. |
| Answers changed after enabling it | Expected — a different model with different calibration. Re-run `run_matrix` and update the README's numbers rather than leaving both sets claimable. |
| `401 missing or invalid API token` | That is `METIS_API_TOKEN`, unrelated to JEV — see `docs/deployment.md`. |

## Verifying the integration

```bash
uv run pytest -q tests/test_judgment.py tests/test_judgment_metrics.py tests/test_judgment_backends.py
```

These cover the primitives, backend selection, failure safety, and — importantly —
the adapter's parsing against a real response shape over a mocked HTTP transport.
That last part means CI exercises `POST /v1/systemone`'s wire format, bearer auth,
and the `Noul`/`Choice`/`Score` conversion **with no API key and no network**.
