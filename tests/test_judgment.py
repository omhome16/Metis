"""Judgment layer: primitives, mock client, backend selection, failure safety.

No network and no API key: the TypeSafe adapter imports its SDK lazily, so these
tests exercise the contract and the mock backend only.
"""

import httpx2
import pytest

from app.judgment import (
    Choice,
    ChoiceAnswer,
    JudgmentResult,
    MockJudgmentClient,
    Noul,
    NoulAnswer,
    Score,
    ScoreAnswer,
    ask_quietly,
    calibration,
    get_judgment_client,
)
from app.judgment.base import TYPESAFE_INPUT_USD_PER_MTOK
from app.judgment.typesafe import TypeSafeJudgmentClient


class _StubSettings:
    """Stand-in for Settings — avoids the alias/env plumbing in unit tests."""

    def __init__(self, backend: str, key: str = "", model: str = "jev-latest"):
        self.judgment_backend = backend
        self.typesafe_api_key = key
        self.typesafe_model = model


class _FailingClient:
    name = "explodes"

    async def ask(self, state, questions):
        raise RuntimeError("judgment backend is down")


# ── primitives & typed accessors ────────────────────────────────────────────


async def test_mock_answers_every_primitive():
    client = MockJudgmentClient()
    result = await client.ask(
        {"text": "x"},
        {
            "is_it": Noul(instructions="Does it hold?"),
            "which": Choice(instructions="Which one?", criteria={"a": None, "b": None}),
            "how_much": Score(instructions="How much?", criteria=["low", "high"]),
        },
    )
    assert isinstance(result.answers["is_it"], NoulAnswer)
    assert isinstance(result.answers["which"], ChoiceAnswer)
    assert isinstance(result.answers["how_much"], ScoreAnswer)
    assert result.backend == "mock"


async def test_mock_defaults_are_deterministic():
    client = MockJudgmentClient()
    questions = {"n": Noul(instructions="?"), "c": Choice(instructions="?", criteria={"x": None})}
    first = await client.ask({}, questions)
    second = await client.ask({}, questions)
    assert first.answers == second.answers


async def test_mock_honors_scripted_answers_and_records_calls():
    client = MockJudgmentClient({"contradicts": NoulAnswer(noul=0.93)})
    result = await client.ask(
        {"a": "1", "b": "2"}, {"contradicts": Noul(instructions="Contradict?")}
    )
    assert result.noul("contradicts") == 0.93
    assert len(client.calls) == 1
    assert client.calls[0][1]["contradicts"].instructions == "Contradict?"


def test_result_accessors_are_type_safe():
    result = JudgmentResult(
        answers={
            "n": NoulAnswer(noul=0.8),
            "c": ChoiceAnswer(choice="yes", confidence=0.7),
            "s": ScoreAnswer(score=1.5, confidence=0.6),
        }
    )
    assert result.noul("n") == 0.8
    assert result.noul("c") is None  # wrong primitive → None, never a wrong type
    assert result.choice("c") == "yes"
    assert result.choice("n") is None
    assert result.confidence("c") == 0.7
    assert result.confidence("n") is None  # Noul carries no separate confidence
    assert result.score("s") == 1.5
    assert result.noul("missing") is None


def test_noul_uncertainty_band_marks_coin_flips():
    assert NoulAnswer(noul=0.5).is_uncertain()
    assert NoulAnswer(noul=0.62).is_uncertain()
    assert not NoulAnswer(noul=0.9).is_uncertain()
    assert not NoulAnswer(noul=0.05).is_uncertain()
    assert NoulAnswer(noul=0.62).is_uncertain(band=0.01) is False


def test_cost_is_input_tokens_only():
    result = JudgmentResult(answers={}, input_tokens=1_000_000)
    assert result.cost_usd == pytest.approx(TYPESAFE_INPUT_USD_PER_MTOK)
    assert JudgmentResult(answers={}, input_tokens=0).cost_usd == 0.0


# ── backend selection ───────────────────────────────────────────────────────


@pytest.mark.parametrize("backend", ["llm", "off", "", "LLM", "nonsense"])
def test_disabled_or_unknown_backends_select_nothing(monkeypatch, backend):
    monkeypatch.setattr(calibration, "get_settings", lambda: _StubSettings(backend))
    assert get_judgment_client() is None


def test_mock_backend_selects_the_mock_client(monkeypatch):
    monkeypatch.setattr(calibration, "get_settings", lambda: _StubSettings("mock"))
    assert isinstance(get_judgment_client(), MockJudgmentClient)


def test_typesafe_without_a_key_is_disabled_not_broken(monkeypatch):
    monkeypatch.setattr(calibration, "get_settings", lambda: _StubSettings("typesafe", key=""))
    assert get_judgment_client() is None


def test_typesafe_with_a_key_selects_the_adapter(monkeypatch):
    monkeypatch.setattr(
        calibration, "get_settings", lambda: _StubSettings("typesafe", key="ts-test-key")
    )
    client = get_judgment_client()
    assert isinstance(client, TypeSafeJudgmentClient)
    assert client.name == "typesafe"


# ── failure safety ──────────────────────────────────────────────────────────


async def test_ask_quietly_without_a_client_returns_none():
    assert await ask_quietly(None, {}, {"n": Noul(instructions="?")}) is None


async def test_ask_quietly_with_no_questions_skips_the_call():
    client = MockJudgmentClient()
    assert await ask_quietly(client, {}, {}) is None
    assert client.calls == []


async def test_ask_quietly_swallows_backend_failures():
    """A judgment outage degrades a feature — it must never fail a request."""
    assert await ask_quietly(_FailingClient(), {}, {"n": Noul(instructions="?")}) is None


async def test_ask_quietly_passes_results_through():
    client = MockJudgmentClient({"n": NoulAnswer(noul=0.91)})
    result = await ask_quietly(client, {"text": "x"}, {"n": Noul(instructions="?")})
    assert result is not None and result.noul("n") == 0.91


# ── TypeSafe adapter: a real response shape over a mock transport ───────────
# These exercise the adapter's parsing against the documented wire format with
# no API key and no network, so the integration is covered in ordinary CI.


def _transport(body: dict, seen: dict) -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx2.Response(200, json=body)

    return httpx2.MockTransport(handler)


async def test_typesafe_adapter_calls_systemone_with_bearer_auth():
    seen: dict = {}
    client = TypeSafeJudgmentClient(
        "ts-key",
        transport=_transport(
            {
                "model": "jev-1.13.0",
                "usage": {"input_tokens": 42, "output_tokens": 0},
                "answers": {"is_it": {"type": "noul", "noul": 0.88}},
            },
            seen,
        ),
    )
    result = await client.ask({"text": "x"}, {"is_it": Noul(instructions="Does it hold?")})
    assert seen["url"] == "https://api.typesafe.ai/v1/systemone"
    assert seen["auth"] == "Bearer ts-key"
    assert result.backend == "typesafe"
    assert result.model == "jev-1.13.0"
    assert result.noul("is_it") == 0.88
    assert result.input_tokens == 42
    assert result.cost_usd == pytest.approx(42 / 1e6 * TYPESAFE_INPUT_USD_PER_MTOK)


async def test_typesafe_adapter_converts_every_primitive():
    client = TypeSafeJudgmentClient(
        "k",
        transport=_transport(
            {
                "model": "jev-1.13.0",
                "usage": {"input_tokens": 120, "output_tokens": 5},
                "answers": {
                    "which": {
                        "type": "choice",
                        "choice": "supports",
                        "confidence": 0.93,
                        "probabilities": {"supports": 0.93, "says_nothing": 0.07},
                    },
                    "how_much": {
                        "type": "score",
                        "score": 1.4,
                        "confidence": 0.8,
                        "legend": {"0": "low", "2": "high"},
                        "probabilities": {"0": 0.3, "2": 0.7},
                    },
                },
            },
            {},
        ),
    )
    result = await client.ask(
        {"claim": "c"},
        {
            "which": Choice(
                instructions="How does it relate?",
                criteria={"supports": "s", "says_nothing": "n"},
            ),
            "how_much": Score(instructions="How well?", criteria=["low", "high"]),
        },
    )
    assert result.choice("which") == "supports"
    assert result.confidence("which") == 0.93
    assert result.score("how_much") == 1.4
    assert result.confidence("how_much") == 0.8


async def test_typesafe_adapter_tolerates_a_partial_response():
    """One missing answer must not lose the others or raise."""
    client = TypeSafeJudgmentClient(
        "k",
        transport=_transport(
            {
                "model": "jev-1.13.0",
                "usage": {"input_tokens": 10, "output_tokens": 0},
                "answers": {"kept": {"type": "noul", "noul": 0.7}},
            },
            {},
        ),
    )
    result = await client.ask(
        {"text": "x"},
        {"kept": Noul(instructions="?"), "dropped": Noul(instructions="?")},
    )
    assert result.noul("kept") == 0.7
    assert result.noul("dropped") is None


async def test_typesafe_adapter_skips_the_call_when_there_are_no_questions():
    def handler(request: httpx2.Request) -> httpx2.Response:  # pragma: no cover
        raise AssertionError("no question should have reached the network")

    client = TypeSafeJudgmentClient("k", transport=httpx2.MockTransport(handler))
    result = await client.ask({"text": "x"}, {})
    assert result.answers == {}
