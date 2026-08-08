from app.gateway.gateway import LLMGateway
from app.gateway.mock import MockProvider
from app.rag.rewrite import rewrite_query


async def test_rewrite_via_mock_gateway():
    gw = LLMGateway(clients={"mock": MockProvider()})
    result = await rewrite_query(gw, "tax deductions for freelancers")
    assert result  # mock returns the last user message


class BrokenGateway:
    async def structured(self, *args, **kwargs):
        raise RuntimeError("boom")


async def test_rewrite_falls_back_on_error():
    assert await rewrite_query(BrokenGateway(), "some question") is None
