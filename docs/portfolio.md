# Portfolio material

Resume bullets, a summary, a spoken pitch, interview Q&A, and an honest
challenges/learnings section.

**Rule for editing this file:** every number here was measured on this repository, and
the source of each is named. If a claim has not been measured, it is labelled as
unmeasured. Do not add optimistic numbers — a reviewer may run the command.

---

## 2-sentence project summary

Metis is a self-organizing knowledge library: you drop in documents and it builds a
per-vault knowledge graph alongside vector embeddings, then answers questions with
citations, surfaces cross-document connections, and flags when sources contradict each
other. It is a single FastAPI service — async Postgres+pgvector, Neo4j with GDS
communities, Redis-backed workers, a hand-built vanilla-JS SPA, and a RAGAS-style eval
harness whose thresholds actually gate the default configuration.

---

## Resume bullets

Each bullet is action → technology → result, using only measured figures.

1. **Engineered a TypeSafe Jev judgment layer** alongside an existing LLM gateway,
   turning four prompt-and-parse decision points into typed, calibrated judgments with
   code-side thresholds — collapsing eval-metric judging from `1 + N` LLM calls to **one
   request per metric** and eliminating a documented free-tier quota cliff that had been
   producing invalid benchmark rows.

2. **Cleared a code-quality backlog to zero** (231 → **0** ruff violations, 42 → **0**
   unformatted files) while fixing a genuinely broken DB-gated test that local runs were
   silently skipping, and rebuilt CI as a hard gate (no thresholds) after the prior
   workflow had been deleted for a flaky corpus-dependent check.

3. **Closed a total absence of access control** by adding an optional constant-time API
   token gate, a security-header baseline with `script-src 'self'` CSP, and
   proxy-aware rate limiting — then removed a **−147 LOC** three-way provider
   duplication in the model layer with behavior-preserving tests.

4. **Built a RAGAS-style evaluation harness with enforced thresholds**
   (faithfulness ≥ 0.90, context precision ≥ 0.80, citation correctness == 1.0) over a
   config matrix comparing hybrid retrieval, reranking, graph boost, and parent-child
   chunking — and published the results including a low-scoring run with its
   explanation, rather than only the good ones.

---

## 30-second pitch

> "Metis is a knowledge library that reads itself. Most 'chat with your documents'
> projects stop at retrieval — Metis also builds a knowledge graph of what's inside, so
> it can answer with citations *and* tell you how your sources relate, and when two of
> them flatly contradict each other.
>
> The engineering decision I'd point at is the one I made about the model layer. It had
> a generation gateway, but every decision — does this source contradict that one,
> which tag does this query mean — was really a prompt whose free-text reply got parsed
> and hoped about. So I added a judgment layer: a System One model that returns typed,
> calibrated probabilities, with thresholds living in code instead of in prose. That
> made an entire class of bug structurally impossible — the corpus enumerates its real
> tags and the model *selects* from them, so it can no longer invent one. It also cut
> eval judging from one call per claim to one call per metric.
>
> The honest part: I made it opt-in and measured, not assumed. Two of the six places
> where it *could* go replace something that's already free and local, so those are
> instrumented but explicitly unmeasured — I'd rather ship a comparison harness than a
> claim I can't back up."

---

## Interview questions and suggested answers

**"Why is the judgment layer a separate package instead of another gateway provider?"**
Because the contracts differ. `LLMClient` is a *generation* interface — chat, stream,
structured JSON, vision. A judgment model returns constrained answers with probability
distributions and never emits text or reasoning. Forcing it into that ABC would have
corrupted what the ABC means, and every caller would have had to branch on which kind of
model it was talking to. Two interfaces that say what they actually are beat one
interface that lies.

**"How do you know the judgment version is better than the LLM version?"**
For three of them I know *structurally*, not empirically. The metadata case is the
clearest: the old path let an LLM write a tag, and an earlier commit had to add a
post-hoc check that threw away invented tags. The new path enumerates the corpus's
actual tags as the option set, so an invented tag is unreachable — and there's a test
asserting the offered options equal the corpus's tags exactly. For the other two (a
cross-encoder reranker and a heuristic router) I deliberately did **not** claim
superiority: the things they'd replace are free, local, or synchronous, so I wired them
as opt-in backends and documented the command to measure them. That's the honest
answer, and being able to say "I don't know yet, here's how I'd find out" is worth more
than a guess.

**"You made it opt-in and defaulted to the old behavior. Isn't that just not shipping?"**
No, and it's a deliberate call about credibility. The README publishes measured eval
numbers. If I silently swapped the judge, those numbers would stop being reproducible
from the code on `main` — a reviewer re-running the harness would get different figures
with no explanation. Opt-in lets both paths exist and produces a comparison table
instead of quietly invalidating the old one. It also means the change can be rolled back
with one environment variable rather than a revert, which matches how the rest of the
system already degrades.

**"What's the single hardest bug you found?"**
A test that called a `client` fixture it never requested. It raised `NameError` — but
only when Postgres was reachable, and the suite auto-skips DB-gated tests on a machine
without infrastructure. So it was broken and invisible: green locally, red in any real
environment. What makes it interesting is the second-order lesson — the codebase has ~91
deliberately broad `except Exception` blocks so features degrade instead of failing, and
that resilience is genuinely good, but it also means nothing shouts when something's
wrong. That's a real trade-off, and it's why I now treat "the guard is intentional" and
"the guard is hiding something" as two separate questions.

**"How would this scale?"**
I'd be careful about claiming it does. The honest version: retrieval is already indexed
(HNSW with tuned `ef_search` and iterative scan), the cache is versioned and TTL'd per
corpus, graph reorg is delta-only so LLM spend doesn't grow with corpus size, and
judgment/LLM calls are batched rather than per-item. The parts I'd actually watch are
the local CPU models — embeddings and the cross-encoder run in-process, which caps
throughput per replica and is exactly why the free hosting tier can't run it.

**"What would you do differently?"**
Two things. First, I'd have introduced the judgment layer before the post-hoc tag
validation patch, because fixing it by *validating the output* was a symptom-level fix —
the real fix was changing the operation from generate to select. Second, I'd have added
the access-control token before writing the first public-facing route instead of
documenting "put it behind your own gate" as a deployment caveat, which is really an
admission that the app isn't deployment-ready.

---

## Challenges & what I learned

**The resilience trap.** Metis is deliberately built to degrade rather than fail: broad
exception guards on every optional feature mean a missing graph, a dead cache, or a
rate-limited provider never takes down an answer. That design is why the ask path is
reliable. It's also why a broken test hid for as long as it did — nothing shouts when
something quietly stops working. I came away thinking the guard isn't the problem; the
missing piece is observability *at* the guard. Logging a swallowed exception is cheap and
turns a silent degradation into a signal.

**Measured beats asserted.** The temptation with a "better model" integration is to ship
it and describe the improvement. Instead I made it a backend behind an environment
variable, so the only way to claim it works is to run both and compare. That reframing —
turning an upgrade into an experiment — is probably the single most useful habit I took
from this work, and it's the reason two of the six integrations are honestly labelled as
unmeasured rather than quietly presented as wins.

**Reading a doc is not knowing an API.** I designed the adapter from TypeSafe's
documentation, then checked it against the installed SDK and found the details that
mattered were not the ones I'd guessed at: constructors are keyword-only, `NoulCriteria`
is `TypedDict` rather than a class, `Noul` genuinely has no confidence field (so
`|noul − 0.5|` is the uncertainty signal, not a separate number), and the SDK takes an
HTTP transport — which is what let me test the real wire format in CI with no API key.
Verifying an integration against the artefact rather than the prose took ten minutes and
would have taken far longer to debug in production.

**The deployment reality check.** The project had a Render blueprint that looked
finished and could never have booted: the free tier is 512 MB while the image pulls
`torch` and a 2.3 GB embedding model. Writing infrastructure that has never run is a way
of mistaking configuration for deployment, and the fix is mostly arithmetic done early.
