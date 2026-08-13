"""P4: semantic router — lane decisions + per-lane ask behavior."""

import json
import uuid
from unittest.mock import AsyncMock, patch

from app.gateway.mock import MockProvider
from app.rag.router import route_question


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.replace("\r\n", "\n").strip().split("\n\n"):
        event, data = None, None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        if event is not None:
            events.append((event, data))
    return events


class RefiningGateway:
    """structured always says deep — used to prove LLM refinement can flip lanes."""

    async def structured(self, *args, **kwargs) -> dict:
        return {"lane": "deep"}


class BrokenGateway:
    """structured raises — refinement must degrade to the heuristic result."""

    async def structured(self, *args, **kwargs) -> dict:
        raise RuntimeError("llm down")


async def test_greetings_and_acknowledgements_route_fast():
    for q in [
        "hi",
        "Hello!",
        "hey",
        "thanks",
        "Thank you",
        "ty",
        "ok",
        "okay",
        "?",
        "bye",
        "sure",
        "hi there",
    ]:
        assert await route_question(q, None) == "fast"


async def test_trivial_single_token_routes_fast():
    for q in ["what?", "why?", "compare", "2+2?", "yes"]:
        assert await route_question(q, None) == "fast"


async def test_no_retrieval_phrasing_routes_fast():
    for q in [
        "Answer without searching the corpus",
        "Don't search, just answer",
        "no retrieval please",
        "skip the search and tell me",
    ]:
        assert await route_question(q, None) == "fast"


async def test_fact_question_routes_standard():
    for q in [
        "Who wrote The Art of War?",
        "What is the capital of France?",
        "When was Metis founded?",
    ]:
        assert await route_question(q, None) == "standard"


async def test_multi_hop_and_corpus_level_route_deep():
    for q in [
        "Compare the strategies of Sun Tzu and Machiavelli",
        "How does mitosis relate to meiosis?",
        "What is the relationship between GDP and inflation?",
        "What are the differences between the two documents?",
        "Summarize the library",
        "What does the corpus say about leadership?",
        "What themes run across the corpus?",
    ]:
        assert await route_question(q, None) == "deep", q


async def test_greeting_with_real_question_stays_standard():
    assert await route_question("Hello, what is the capital of France?", None) == "standard"


async def test_explicit_mode_wins_over_heuristic():
    assert await route_question("hello", None, mode="deep") == "deep"
    assert await route_question("Who wrote The Art of War?", None, mode="fast") == "fast"
    assert await route_question("hello", None, mode="standard") == "standard"


async def test_image_queries_never_deep():
    assert await route_question("Compare these images", None, image=True) == "standard"
    assert await route_question("hello", None, image=True, mode="deep") == "standard"


async def test_llm_refinement_can_flip_lane_when_enabled():
    gw = RefiningGateway()
    assert await route_question("Who wrote The Art of War?", gw, use_llm=True) == "deep"


async def test_llm_failure_falls_back_to_heuristic():
    gw = BrokenGateway()
    assert await route_question("Who wrote The Art of War?", gw, use_llm=True) == "standard"
    assert await route_question("hello", gw, use_llm=True) == "fast"


async def test_mock_provider_empty_refine_falls_back_to_heuristic():
    gw = MockProvider()  # structured returns {} for unknown tasks
    assert await route_question("Who wrote The Art of War?", gw, use_llm=True) == "standard"
    assert await route_question("hello", gw, use_llm=True) == "fast"


async def test_router_never_raises_on_empty_input():
    assert await route_question("", None) == "fast"
    assert await route_question("   ", None) == "fast"


def test_heuristic_sync_no_side_effects():
    from app.rag.router import _heuristic

    assert _heuristic("Compare A and B") == "deep"
    assert _heuristic("hello") == "fast"
    assert _heuristic("What is 2+2?") == "standard"


async def test_ask_fast_lane_zero_retrieval_zero_db(client, require_db):
    """P4: a greeting must skip retrieval, cache, and persistence entirely."""
    corpus = f"test-fast-{uuid.uuid4().hex[:8]}"
    with patch("app.api.routes.ask.get_gateway") as gw_mock:
        gw = gw_mock.return_value

        async def fake_stream(*args, **kwargs):
            for token in ["Hi ", "there! "]:
                yield token

        gw.chat_stream = fake_stream
        with (
            patch("app.rag.pipeline.retrieve_context", new=AsyncMock()) as rc,
            patch("app.api.routes.ask.cache_lookup", new=AsyncMock(return_value=None)) as cl,
            patch("app.api.routes.ask.cache_store", new=AsyncMock()) as cs,
        ):
            resp = await client.post("/api/v1/ask", json={"question": "Hello!", "corpus": corpus})
    assert resp.status_code == 200

    events = _parse_sse(resp.text)
    names = [e for e, _ in events]
    assert names[0] == "sources"
    assert names[-1] == "done"
    assert dict(events)["sources"]["chunks"] == []
    assert dict(events)["meta"]["lane"] == "fast"
    assert dict(events)["done"]["usage"]["lane"] == "fast"
    assert dict(events)["done"].get("conversation_id") is None
    assert rc.await_count == 0
    assert cl.await_count == 0
    assert cs.await_count == 0


async def test_ask_deep_lane_without_tools_falls_back_to_direct(client, require_db):
    """P4: multi-hop question without tool support still serves via direct path."""
    from tests.test_ask import FakeGateway, _cleanup, _seed

    corpus = f"test-deep-{uuid.uuid4().hex[:8]}"
    await _seed(corpus)
    try:
        with patch("app.api.routes.ask.get_gateway", return_value=FakeGateway()):
            resp = await client.post(
                "/api/v1/ask",
                json={"question": "Compare Sun Tzu and Machiavelli", "corpus": corpus},
            )
        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        names = [e for e, _ in events]
        assert "meta" in names
        assert dict(events)["meta"]["lane"] == "deep"
        assert dict(events)["done"]["usage"]["lane"] == "deep"
        assert names[0] == "sources"
        assert names[-1] == "done"
    finally:
        await _cleanup(corpus)
