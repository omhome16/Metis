"""Typed judgments: how Metis asks a *decision* model for an answer.

`app/gateway` **generates** text. This package **decides** things. The two
contracts are different — an `LLMClient` streams generated tokens, a
`JudgmentClient` returns constrained answers with calibrated probabilities that
code thresholds — so they are separate packages rather than one overloaded
interface.

The primitives mirror TypeSafe's System One API (https://docs.typesafe.ai):
`Choice` selects one of a defined set, `Noul` gives the probability that a
condition holds, and `Score` places a value on ordered levels. Every question is
evaluated against one shared `state`, in parallel, in a single request.
"""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# Jev bills input tokens only ($42/Btok); output tokens are free.
# https://docs.typesafe.ai/models
TYPESAFE_INPUT_USD_PER_MTOK = 0.042

#: A Noul this close to 0.5 means "similar probability either way" — not medium
#: intensity — so callers escalate instead of acting on it.
DEFAULT_UNCERTAIN_BAND = 0.15


@dataclass(frozen=True)
class Choice:
    """Pick one of a defined set. `criteria` maps each label to what it means."""

    instructions: str
    criteria: dict[str, str | None]


@dataclass(frozen=True)
class Noul:
    """Whether a condition holds, as a probability.

    Note there is deliberately *no* confidence field here: a Noul is itself a
    probability, so `|noul - 0.5|` is the uncertainty signal. Contrast with
    `Choice` and `Score`, which carry probability *and* confidence.
    """

    instructions: str
    true_description: str = ""
    false_description: str = ""


@dataclass(frozen=True)
class Score:
    """Degree along a described dimension. `criteria` is ordered, lowest first."""

    instructions: str
    criteria: list[str]


Question = Choice | Noul | Score


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str
    confidence: float
    probabilities: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class NoulAnswer:
    noul: float

    def is_uncertain(self, band: float = DEFAULT_UNCERTAIN_BAND) -> bool:
        """True when the answer is closer to a coin flip than to a decision."""
        return abs(self.noul - 0.5) < band


@dataclass(frozen=True)
class ScoreAnswer:
    score: float
    confidence: float
    probabilities: dict[str, float] = field(default_factory=dict)


Answer = ChoiceAnswer | NoulAnswer | ScoreAnswer


@dataclass
class JudgmentResult:
    """Answers keyed by question name, plus what it cost to get them."""

    answers: dict[str, Answer]
    model: str = ""
    backend: str = ""
    input_tokens: int = 0
    latency_ms: int = 0

    @property
    def cost_usd(self) -> float:
        return self.input_tokens / 1e6 * TYPESAFE_INPUT_USD_PER_MTOK

    def noul(self, name: str) -> float | None:
        """Probability for a `Noul` question, or None if it was not answered."""
        answer = self.answers.get(name)
        return answer.noul if isinstance(answer, NoulAnswer) else None

    def choice(self, name: str) -> str | None:
        answer = self.answers.get(name)
        return answer.choice if isinstance(answer, ChoiceAnswer) else None

    def confidence(self, name: str) -> float | None:
        answer = self.answers.get(name)
        if isinstance(answer, (ChoiceAnswer, ScoreAnswer)):
            return answer.confidence
        return None

    def score(self, name: str) -> float | None:
        answer = self.answers.get(name)
        return answer.score if isinstance(answer, ScoreAnswer) else None


@runtime_checkable
class JudgmentClient(Protocol):
    """Anything that can answer typed questions about a state."""

    name: str

    async def ask(self, state: dict | str, questions: dict[str, Question]) -> JudgmentResult:
        """Evaluate every question against `state` — they run in parallel."""
        ...
