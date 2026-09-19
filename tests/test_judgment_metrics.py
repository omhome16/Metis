"""Eval metrics on the judgment backend: one batched call, per-item escalation."""

import pytest

from app.evals import metrics
from app.judgment import MockJudgmentClient, NoulAnswer, calibration


class CountingGateway:
    """LLM judge that records how many calls the fallback actually needed."""

    def __init__(self, verdict: bool = True):
        self.verdict = verdict
        self.calls: list[str] = []

    async def structured(self, task, messages, json_schema):
        self.calls.append(messages[-1]["content"])
        if "claims" in messages[0]["content"]:
            return {"claims": ["a claim", "another claim"]}
        return {k: self.verdict for k in ("supported", "useful", "present")}


def _client(monkeypatch, noul: float, **overrides):
    answers = {f"item_{i}": NoulAnswer(noul=noul) for i in range(6)}
    answers.update(overrides)
    client = MockJudgmentClient(answers)
    monkeypatch.setattr(calibration, "get_judgment_client", lambda: client)
    monkeypatch.setattr(metrics, "get_judgment_client", lambda: client)
    return client


async def test_faithfulness_judges_every_claim_in_one_request(monkeypatch):
    gateway = CountingGateway()
    client = _client(monkeypatch, 0.95)
    score = await metrics.faithfulness(gateway, "One. Two.", ["context"])
    assert score == 1.0
    # one request carried both claim judgments
    assert len(client.calls) == 1
    assert set(client.calls[0][1]) == {"item_0", "item_1"}
    # and the LLM judge was not needed for the support step at all
    assert all("CLAIM" not in call for call in gateway.calls)


async def test_faithfulness_scores_unsupported_claims(monkeypatch):
    gateway = CountingGateway()
    _client(monkeypatch, 0.02)
    assert await metrics.faithfulness(gateway, "One. Two.", ["context"]) == 0.0


async def test_ambiguous_items_escalate_to_the_llm_individually(monkeypatch):
    """A coin-flip judgment must not be treated as a verdict."""
    gateway = CountingGateway(verdict=False)
    _client(monkeypatch, 0.5)
    score = await metrics.faithfulness(gateway, "One. Two.", ["context"])
    assert score == 0.0  # the LLM said not supported
    assert any("CLAIM" in call for call in gateway.calls)


async def test_without_a_judgment_backend_metrics_use_the_llm_path(monkeypatch):
    gateway = CountingGateway(verdict=True)
    monkeypatch.setattr(metrics, "get_judgment_client", lambda: None)
    assert await metrics.faithfulness(gateway, "One. Two.", ["context"]) == 1.0
    assert any("CLAIM" in call for call in gateway.calls)


async def test_context_precision_batches_over_chunks(monkeypatch):
    gateway = CountingGateway(verdict=True)
    client = _client(monkeypatch, 0.9)
    score = await metrics.context_precision(gateway, "q", ["c1", "c2", "c3"])
    assert score == 1.0
    assert len(client.calls) == 1
    assert set(client.calls[0][1]) == {"item_0", "item_1", "item_2"}


async def test_context_recall_batches_over_claims(monkeypatch):
    gateway = CountingGateway(verdict=True)
    client = _client(monkeypatch, 0.88)
    score = await metrics.context_recall(gateway, "One. Two.", ["context"])
    assert score == 1.0
    assert len(client.calls) == 1


async def test_empty_inputs_never_reach_a_backend(monkeypatch):
    gateway = CountingGateway()
    client = _client(monkeypatch, 0.9)
    assert await metrics.context_precision(gateway, "q", []) == 0.0
    assert await metrics.faithfulness(gateway, "   ", ["context"]) == 0.0
    assert client.calls == []


def test_judge_specs_pin_both_backends_to_the_same_question():
    """The two paths must ask the same thing or the numbers are not comparable."""
    for spec in (metrics.SUPPORT, metrics.USEFULNESS, metrics.PRESENCE):
        assert spec.instructions and spec.true_description and spec.false_description
        assert spec.prompt and spec.key


@pytest.mark.parametrize("noul,expected", [(0.99, 1.0), (0.01, 0.0)])
async def test_faithfulness_extremes(monkeypatch, noul, expected):
    _client(monkeypatch, noul)
    assert await metrics.faithfulness(CountingGateway(), "One. Two.", ["ctx"]) == expected
