"""Turn calibrated judgments into Metis decisions, and never break a request.

Two rules this module exists to enforce:

1. **Policy is code, not the model.** The judgment model returns probabilities;
   thresholds live here, are evaluated against the user's own data, and can
   change without rerunning inference. A `Noul` near 0.5 means "similar
   probability either way", not "medium intensity", so an ambiguous judgment
   escalates to the pre-existing LLM path instead of acting.
2. **The judgment layer is never load-bearing on its own.** `METIS_JUDGMENT_BACKEND`
   defaults to `llm`, so the behavior that shipped before this package existed is
   the default, and `typesafe` can be rolled back with one environment variable.

Callers use `ask_quietly`: `None` means "no judgment available", and every
feature that uses it must still have its original path.
"""

from app.core.config import get_settings
from app.core.logging import get_logger
from app.judgment.base import JudgmentClient, JudgmentResult, Question
from app.judgment.mock import MockJudgmentClient
from app.judgment.typesafe import TypeSafeJudgmentClient

logger = get_logger(__name__)

#: Backends that mean "do not use judgments at all".
DISABLED_BACKENDS = frozenset({"", "off", "llm"})


def get_judgment_client() -> JudgmentClient | None:
    """The configured judgment backend, or None when judgments are not in use.

    None means *not configured*, never *failed* — that distinction is what lets
    callers fall back cleanly instead of branching on errors.
    """
    settings = get_settings()
    backend = (settings.judgment_backend or "llm").strip().lower()
    if backend in DISABLED_BACKENDS:
        return None
    if backend == "mock":
        return MockJudgmentClient()
    if backend == "typesafe":
        if not settings.typesafe_api_key:
            logger.warning(
                "METIS_JUDGMENT_BACKEND=typesafe but TYPESAFE_API_KEY is unset — judgment disabled"
            )
            return None
        return TypeSafeJudgmentClient(settings.typesafe_api_key, settings.typesafe_model)
    logger.warning("unknown METIS_JUDGMENT_BACKEND %r — judgment disabled", backend)
    return None


async def ask_quietly(
    client: JudgmentClient | None,
    state: dict | str,
    questions: dict[str, Question],
) -> JudgmentResult | None:
    """Ask the judgment backend, returning None on any failure.

    A judgment outage must degrade a feature, not fail a request — the same
    contract the gateway's provider fallback chain follows.
    """
    if client is None or not questions:
        return None
    try:
        return await client.ask(state, questions)
    except Exception as exc:  # noqa: BLE001 — judgment is best-effort by design
        logger.warning("judgment backend %s failed: %s", getattr(client, "name", "?"), exc)
        return None
