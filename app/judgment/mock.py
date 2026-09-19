"""Deterministic, key-free judgment client for tests and no-key development.

`tests/conftest.py` forces mock providers so the suite never touches the
network; this client keeps that property true for the judgment layer too. Pass
`answers` to script exact values for a specific test.
"""

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

#: Fixed defaults so assertions are stable regardless of dict/hash ordering.
DEFAULT_NOUL = 0.5
DEFAULT_CONFIDENCE = 0.9


class MockJudgmentClient:
    """A `JudgmentClient` that answers from constants, never from a model."""

    name = "mock"

    def __init__(self, answers: dict[str, Answer] | None = None):
        self._answers = dict(answers or {})
        self.calls: list[tuple[dict | str, dict[str, Question]]] = []

    async def ask(self, state: dict | str, questions: dict[str, Question]) -> JudgmentResult:
        self.calls.append((state, questions))
        resolved: dict[str, Answer] = {}
        for name, question in questions.items():
            resolved[name] = self._answers.get(name) or self._default(question)
        return JudgmentResult(answers=resolved, model="mock", backend=self.name)

    @staticmethod
    def _default(question: Question) -> Answer:
        if isinstance(question, Noul):
            return NoulAnswer(noul=DEFAULT_NOUL)
        if isinstance(question, Choice):
            labels = list(question.criteria) or ["unknown"]
            share = 1 / len(labels)
            return ChoiceAnswer(
                choice=labels[0],
                confidence=DEFAULT_CONFIDENCE,
                probabilities=dict.fromkeys(labels, share),
            )
        if isinstance(question, Score):
            levels = question.criteria or ["unknown"]
            share = 1 / len(levels)
            return ScoreAnswer(
                score=0.0,
                confidence=DEFAULT_CONFIDENCE,
                probabilities=dict.fromkeys(levels, share),
            )
        raise TypeError(f"unsupported question type: {type(question).__name__}")
