from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.errors import register_exception_handlers
from app.core.limits import RateLimiter


def test_rate_limiter_allows_then_denies():
    limiter = RateLimiter(max_requests=3, window_seconds=60)
    assert all(limiter.allow("ip-1") for _ in range(3))
    assert not limiter.allow("ip-1")  # over the window limit
    assert limiter.allow("ip-2")  # different key unaffected


def test_rate_limiter_window_expiry():
    limiter = RateLimiter(max_requests=1, window_seconds=0)  # zero window → all expire
    assert limiter.allow("ip")
    assert limiter.allow("ip")


def test_rate_limiter_reset():
    limiter = RateLimiter(max_requests=1, window_seconds=60)
    assert limiter.allow("ip")
    assert not limiter.allow("ip")
    limiter.reset()
    assert limiter.allow("ip")


def test_unhandled_exception_handler_returns_500():
    mini = FastAPI()
    register_exception_handlers(mini)

    @mini.get("/boom")
    async def boom():
        raise RuntimeError("kaboom")

    client = TestClient(mini, raise_server_exceptions=False)
    resp = client.get("/boom")
    assert resp.status_code == 500
    assert resp.json() == {"detail": "internal server error"}
