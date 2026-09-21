"""TypeSafe System One (Jev) adapter.

Jev ingests `state` once and evaluates every question against it in parallel,
returning typed answers and calibrated probabilities instead of generated text.
One request therefore carries many independent questions — far cheaper and
faster than one model call per question — and output tokens are free.

The SDK is imported lazily on purpose: the judgment layer is optional, and
importing Metis must never fail because `typesafe-sdk` is absent or no key is
configured. Docs: https://docs.typesafe.ai (HTTP: `POST /v1/systemone`).
"""

import time

from app.core.logging import get_logger
from app.judgment.base import (
    Answer,
    Choice,
    ChoiceAnswer,
    JudgmentResult,
    Noul,
    NoulAnswer,
    Question,
    Score,
    ScoreAnswer,
)

logger = get_logger(__name__)

_MISSING_SDK = "typesafe-sdk is not installed — run `uv add typesafe-sdk`"


class TypeSafeJudgmentClient:
    """`JudgmentClient` backed by the System One API."""

    name = "typesafe"

    def __init__(self, api_key: str, model: str = "jev-latest", transport=None):
        # `transport` is test-only (an httpx2 MockTransport), which lets the
        # suite exercise this adapter against a real response shape with no key.
        self._api_key = api_key
        self._model = model
        self._transport = transport

    @staticmethod
    def _sdk():
        try:
            import typesafe_sdk
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise RuntimeError(_MISSING_SDK) from exc
        return typesafe_sdk

    def _to_sdk_question(self, question: Question):
        sdk = self._sdk()
        if isinstance(question, Noul):
            if question.true_description or question.false_description:
                criteria = sdk.NoulCriteria(
                    true=question.true_description, false=question.false_description
                )
                return sdk.Noul(instructions=question.instructions, criteria=criteria)
            return sdk.Noul(instructions=question.instructions)
        if isinstance(question, Choice):
            return sdk.Choice(instructions=question.instructions, criteria=dict(question.criteria))
        if isinstance(question, Score):
            return sdk.Score(instructions=question.instructions, criteria=list(question.criteria))
        raise TypeError(f"unsupported question type: {type(question).__name__}")

    @staticmethod
    def _from_sdk_answer(answer, question: Question) -> Answer:
        if isinstance(question, Noul):
            return NoulAnswer(noul=float(answer.noul))
        if isinstance(question, Choice):
            return ChoiceAnswer(
                choice=str(answer.choice),
                confidence=float(answer.confidence),
                probabilities=dict(answer.probabilities or {}),
            )
        return ScoreAnswer(
            score=float(answer.score),
            confidence=float(answer.confidence),
            probabilities=dict(answer.probabilities or {}),
        )

    async def ask(self, state: dict | str, questions: dict[str, Question]) -> JudgmentResult:
        if not questions:
            return JudgmentResult(answers={}, model=self._model, backend=self.name)
        sdk = self._sdk()
        started = time.perf_counter()
        async with sdk.AsyncTypeSafeClient(
            api_key=self._api_key, transport=self._transport
        ) as client:
            response = await client.system_one(
                state=state,
                questions={name: self._to_sdk_question(q) for name, q in questions.items()},
                model=self._model,
            )
        answers = self._collect(response, questions)
        usage = getattr(response, "usage", None)
        return JudgmentResult(
            answers=answers,
            model=str(getattr(response, "model", "") or self._model),
            backend=self.name,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    def _collect(self, response, questions: dict[str, Question]) -> dict[str, Answer]:
        """Convert `response.answers` (keyed by question name) to typed answers.

        The question's own primitive tells us how to read its answer, so the
        response's type discriminator never has to be inspected.
        """
        raw_answers = getattr(response, "answers", None) or {}
        answers: dict[str, Answer] = {}
        for name, question in questions.items():
            raw = raw_answers.get(name)
            if raw is not None:
                answers[name] = self._from_sdk_answer(raw, question)
        if len(answers) != len(questions):
            logger.warning(
                "judgment response answered %d/%d questions", len(answers), len(questions)
            )
        return answers
