from app.evals import metrics


class StubGateway:
    """Deterministic judge: whatever the caller asks, answer from a map."""

    def __init__(self, structured_map):
        self._map = structured_map

    async def structured(self, task, messages, json_schema):
        joined = "\n".join(str(m.get("content", "")) for m in messages)
        for key, value in self._map.items():
            if key in joined:
                return value
        return {}


async def test_faithfulness_all_supported():
    gw = StubGateway({"Extract the atomic": {"claims": ["c1", "c2"]}, "CLAIM:\n": {"supported": True}})
    score = await metrics.faithfulness(gw, "The sky is blue. Grass is green.", ["context"])
    assert score == 1.0


async def test_faithfulness_half_supported():
    class HalfGateway(StubGateway):
        def __init__(self):
            super().__init__({})
            self.count = 0

        async def structured(self, task, messages, json_schema):
            joined = "\n".join(str(m.get("content", "")) for m in messages)
            if "Extract the atomic" in joined:
                return {"claims": ["one", "two"]}
            self.count += 1
            return {"supported": self.count % 2 == 1}

    gw = HalfGateway()
    score = await metrics.faithfulness(gw, "Sentence one. Sentence two.", ["ctx"])
    assert score == 0.5


async def test_faithfulness_empty_answer():
    assert await metrics.faithfulness(StubGateway({}), "", ["ctx"]) == 0.0


async def test_answer_relevancy_with_mock_embedder():
    from app.rag.embeddings import MockEmbedder

    gw = StubGateway({"Generate 3": {"questions": ["q1", "q2"]}})
    embedder = MockEmbedder()
    score = await metrics.answer_relevancy(gw, "question", "an answer", embedder.embed_query)
    assert 0.0 <= score <= 1.0


async def test_context_precision_ideal():
    class UsefulGateway(StubGateway):
        def __init__(self):
            super().__init__({})
            self.calls = 0

        async def structured(self, task, messages, json_schema):
            self.calls += 1
            return {"useful": self.calls == 1}

    gw = UsefulGateway()
    # chunk 1 useful, chunk 2 not → precision at k=1 is 1, total = 1/1
    score = await metrics.context_precision(gw, "q", ["useful context", "noise"])
    assert score == 1.0


async def test_context_recall():
    class PresentGateway(StubGateway):
        def __init__(self):
            super().__init__({})
            self.calls = 0

        async def structured(self, task, messages, json_schema):
            self.calls += 1
            return {"present": self.calls == 1}

    gw = PresentGateway()
    assert await metrics.context_recall(gw, "First claim here. Second claim there.", ["ctx"]) == 0.5


def test_citation_correctness():
    valid, detail = metrics.citation_correctness("Answer [1] and [2].", ["a", "b", "c"])
    assert valid == 1.0 and detail == {"emitted": 2, "valid": 2}
    valid, _ = metrics.citation_correctness("Answer [1] and [9].", ["a"])
    assert valid == 0.5
    valid, detail = metrics.citation_correctness("No citations.", ["a"])
    assert valid == 0.0 and detail["emitted"] == 0
