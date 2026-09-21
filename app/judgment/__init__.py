"""Judgment layer: typed, calibrated decisions alongside the generating gateway.

See `app/judgment/base.py` for the primitives and `calibration.py` for the
policy that turns probabilities into decisions.
"""

from app.judgment.base import (
    Answer,
    Choice,
    ChoiceAnswer,
    JudgmentClient,
    JudgmentResult,
    Noul,
    NoulAnswer,
    Question,
    Score,
    ScoreAnswer,
)
from app.judgment.calibration import ask_quietly, get_judgment_client
from app.judgment.mock import MockJudgmentClient

__all__ = [
    "Answer",
    "Choice",
    "ChoiceAnswer",
    "JudgmentClient",
    "JudgmentResult",
    "MockJudgmentClient",
    "Noul",
    "NoulAnswer",
    "Question",
    "Score",
    "ScoreAnswer",
    "ask_quietly",
    "get_judgment_client",
]
