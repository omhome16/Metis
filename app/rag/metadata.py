"""P3.2: scoping hints extracted from a query, applied as retrieval filters.

Turns "the 2024 policy on privacy, by Adams" into
`{"tags": [...], "date_from": "2024-01-01", "date_to": "2024-12-31", "author": "Adams"}`
which then filters both retrieval arms. Any failure or empty result → `{}`
(no filtering, never breaks ask).

Two paths produce those hints:

* **Selection** (`scoped_query_metadata`, used when a judgment backend is
  configured) enumerates the tags/authors/years the corpus *actually holds* and
  asks the model to pick from them. A filter can then only reference values that
  exist, so an invented tag is impossible rather than merely discarded.
* **Generation** (`extract_query_metadata`, the original path) asks an LLM to
  write the hints as JSON. It can name values the corpus does not have — which
  is why `pipeline.retrieve_context` still validates tags before trusting them.
"""

import json
import re

# Aliased: this module also has a `text` parameter (raw JSON), and shadowing the
# SQL helper would make the queries below read as if they used that parameter.
from sqlalchemy import text as sql_text

from app.core.logging import get_logger
from app.gateway.gateway import LLMGateway
from app.judgment.base import Choice, Question
from app.judgment.calibration import ask_quietly, get_judgment_client

logger = get_logger(__name__)

METADATA_PROMPT = (
    "Extract retrieval scoping hints from the user's question.\n"
    "Return JSON with this exact shape (no markdown):\n"
    '{"tags": ["..."], "date_from": "YYYY-MM-DD or null",'
    ' "date_to": "YYYY-MM-DD or null", "author": "name or null"}\n'
    "tags: domain terms that would appear as document tags (max 5, lowercase).\n"
    "date_from/date_to: a date range implied by the question ('the 2024 policy' -> "
    'date_from "2024-01-01", date_to "2024-12-31"); null when no range is implied.\n'
    "author: a named author/creator if the question asks for one; null otherwise.\n"
    "Use null for anything not implied — do not invent constraints."
)

_FALLBACK_YEAR = re.compile(r"\b(1[89]\d\d|20\d\d)\b")


def _fallback_metadata(question: str) -> dict:
    """Regex-only hints when the LLM is unavailable: a bare year → that year."""
    match = _FALLBACK_YEAR.search(question or "")
    if not match:
        return {}
    year = match.group(1)
    return {"date_from": f"{year}-01-01", "date_to": f"{year}-12-31"}


def _clean(question: str, raw: dict) -> dict:
    out: dict = {}
    tags = [
        str(t).strip().lower()[:64]
        for t in (raw.get("tags") or [])
        if isinstance(t, str) and t.strip()
    ]
    if tags:
        out["tags"] = tags[:5]
    for key in ("date_from", "date_to"):
        value = raw.get(key)
        if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
            out[key] = value.strip()
    author = str(raw.get("author") or "").strip()
    if author:
        out["author"] = author[:128]
    return out


async def extract_query_metadata(gateway: LLMGateway, question: str) -> dict:
    """LLM (best-effort) → scoping dict; `{}` on any failure or empty result."""
    try:
        result = await gateway.structured(
            "query_metadata",
            [
                {"role": "system", "content": METADATA_PROMPT},
                {"role": "user", "content": question[:2000]},
            ],
            {},
        )
        if isinstance(result, dict) and result:
            cleaned = _clean(question, result)
            if cleaned:
                return cleaned
    except Exception as exc:  # noqa: BLE001
        logger.warning("query metadata extraction failed: %s", exc)
    return _fallback_metadata(question)


# ── selection path (judgment backend) ───────────────────────────────────────

# Option label meaning "the question does not restrict on this dimension".
NO_MATCH = "none"

MAX_TAG_CANDIDATES = 40
MAX_AUTHOR_CANDIDATES = 20
MAX_YEAR_CANDIDATES = 20

_TAGS_SQL = """
SELECT DISTINCT t.tag AS tag
FROM documents d, unnest(d.tags) AS t(tag)
WHERE d.corpus = :c AND t.tag <> ''
ORDER BY tag
LIMIT :lim
"""
_AUTHORS_SQL = """
SELECT DISTINCT d.author AS author
FROM documents d
WHERE d.corpus = :c AND d.author <> ''
ORDER BY author
LIMIT :lim
"""
_YEARS_SQL = """
SELECT DISTINCT EXTRACT(YEAR FROM d.doc_date)::int AS year
FROM documents d
WHERE d.corpus = :c AND d.doc_date IS NOT NULL
ORDER BY year
LIMIT :lim
"""


async def corpus_scoping_candidates(session, corpus: str | None) -> dict:
    """The tags, authors, and years that exist in a corpus — the option sets.

    Enumerating these is what makes selection possible: a judgment can only pick
    a value that was offered, so no filter can name something absent from the
    corpus.
    """
    params = {"c": corpus or "default"}
    tags = (
        (await session.execute(sql_text(_TAGS_SQL), {**params, "lim": MAX_TAG_CANDIDATES}))
        .scalars()
        .all()
    )
    authors = (
        (await session.execute(sql_text(_AUTHORS_SQL), {**params, "lim": MAX_AUTHOR_CANDIDATES}))
        .scalars()
        .all()
    )
    years = (
        (await session.execute(sql_text(_YEARS_SQL), {**params, "lim": MAX_YEAR_CANDIDATES}))
        .scalars()
        .all()
    )
    return {
        "tags": [t for t in tags if t],
        "authors": [a for a in authors if a],
        "years": list(years),
    }


def _scoping_questions(candidates: dict) -> dict[str, Question]:
    """One Choice per dimension, every option a value the corpus really holds."""
    questions: dict[str, Question] = {}
    if candidates.get("tags"):
        questions["tag"] = Choice(
            instructions=(
                "Which single tag does the question restrict documents to? Every option is a "
                f"tag that exists in this corpus. Answer {NO_MATCH!r} unless the question "
                "clearly narrows to one of these topics."
            ),
            criteria={
                **{t: f"The question is about documents tagged {t!r}." for t in candidates["tags"]},
                NO_MATCH: "The question does not restrict by tag.",
            },
        )
    if candidates.get("authors"):
        questions["author"] = Choice(
            instructions=(
                "Which author does the question ask about? Every option is an author with"
                f" documents in this corpus. Answer {NO_MATCH!r} unless a specific author is "
                "asked for."
            ),
            criteria={
                **{
                    a: f"The question asks for documents written by {a!r}."
                    for a in candidates["authors"]
                },
                NO_MATCH: "No specific author is asked for.",
            },
        )
    if candidates.get("years"):
        questions["year"] = Choice(
            instructions=(
                "Which year does the question restrict documents to? Every option is a year"
                f" present in this corpus. Answer {NO_MATCH!r} unless the question names a year."
            ),
            criteria={
                **{
                    str(y): f"The question is about documents dated {y}."
                    for y in candidates["years"]
                },
                NO_MATCH: "No year is named by the question.",
            },
        )
    return questions


def _selection_to_meta(result) -> dict:
    """Map selected options onto the filter shape both retrieval arms expect."""
    meta: dict = {}
    tag = result.choice("tag")
    if tag and tag != NO_MATCH:
        meta["tags"] = [tag]
    author = result.choice("author")
    if author and author != NO_MATCH:
        meta["author"] = author
    year = result.choice("year")
    if year and year != NO_MATCH and year.isdigit():
        meta["date_from"] = f"{year}-01-01"
        meta["date_to"] = f"{year}-12-31"
    return meta


async def _scope_by_selection(question: str, candidates: dict) -> dict | None:
    """Select scoping hints from the corpus's own values.

    Returns None when no judgment was available (so the caller can fall back to
    generation). Returns `{}` when the judgment ran and decided the question
    restricts nothing — a real decision that must not be second-guessed.
    """
    client = get_judgment_client()
    if client is None:
        return None
    questions = _scoping_questions(candidates)
    if not questions:
        return None
    result = await ask_quietly(client, {"question": (question or "")[:2000]}, questions)
    if result is None:
        return None
    return _selection_to_meta(result)


async def scoped_query_metadata(
    gateway: LLMGateway,
    question: str,
    *,
    session=None,
    corpus: str | None = None,
) -> dict:
    """Scoping hints for a query, preferring selection over generation.

    With a judgment backend configured and a session available, the corpus's real
    tags/authors/years are offered as choices, so the filter cannot reference a
    value the corpus lacks. Otherwise the pre-existing LLM extractor runs
    unchanged, which is the default configuration.
    """
    if session is not None:
        try:
            candidates = await corpus_scoping_candidates(session, corpus)
        except Exception as exc:  # noqa: BLE001 — scoping must never break ask
            logger.warning("corpus scoping candidates failed: %s", exc)
            candidates = {}
        if any(candidates.values()):
            selected = await _scope_by_selection(question, candidates)
            if selected is not None:
                return selected
    return await extract_query_metadata(gateway, question)


def parse_metadata_json(text: str) -> dict:
    """Parse the LLM's raw JSON text (lenient: strips code fences)."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        stripped = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return {}
