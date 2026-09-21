"""Access control: the optional API token, security headers, rate-limit keying.

A purpose-built app is used rather than the real one so each guard can be
configured independently — the real app reads its token from the environment,
which is exactly what these tests need to vary.
"""

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.limits import RateLimiter, RateLimitMiddleware
from app.core.security import (
    SECURITY_HEADERS,
    ApiTokenMiddleware,
    SecurityHeadersMiddleware,
    presented_token,
)

TOKEN = "s3cret-token"


def _client(token: str = "", hsts: bool = False, auth_mode: str = "token") -> TestClient:
    app = FastAPI()
    app.add_middleware(ApiTokenMiddleware, token=token, auth_mode=auth_mode)
    app.add_middleware(SecurityHeadersMiddleware, hsts=hsts)

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/api/v1/thing")
    async def thing():
        return {"ok": True}

    @app.get("/static/js/main.js")
    async def asset():
        return {"asset": True}

    @app.get("/documents/{doc_id}/file")
    async def document_file(doc_id: str):
        return {"doc": doc_id}

    return TestClient(app)


# ── no token configured → unchanged, unauthenticated behavior ───────────────


def test_without_a_token_everything_stays_open():
    client = _client()
    assert client.get("/api/v1/thing").status_code == 200
    assert client.get("/healthz").status_code == 200


def test_token_gate_is_dormant_outside_token_mode():
    """users/none modes never engage the shared-secret middleware."""
    client = _client(TOKEN, auth_mode="users")
    assert client.get("/api/v1/thing").status_code == 200
    client = _client(TOKEN, auth_mode="none")
    assert client.get("/api/v1/thing").status_code == 200


# ── token configured → protected routes require it ─────────────────────────


def test_configured_token_rejects_anonymous_requests():
    client = _client(TOKEN)
    res = client.get("/api/v1/thing")
    assert res.status_code == 401
    assert res.headers["www-authenticate"] == "Bearer"
    assert res.json()["detail"] == "missing or invalid API token"


def test_bearer_header_authorizes():
    client = _client(TOKEN)
    assert (
        client.get("/api/v1/thing", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    )


def test_custom_header_authorizes():
    client = _client(TOKEN)
    assert client.get("/api/v1/thing", headers={"X-API-Token": TOKEN}).status_code == 200


def test_query_parameter_authorizes_browser_navigations():
    """A download link cannot set headers, so the query form has to work."""
    client = _client(TOKEN)
    assert client.get(f"/api/v1/thing?token={TOKEN}").status_code == 200
    assert client.get(f"/documents/abc/file?token={TOKEN}").status_code == 200


def test_wrong_token_is_rejected():
    client = _client(TOKEN)
    assert client.get("/api/v1/thing", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/v1/thing?token=wrong").status_code == 401


def test_bearer_prefix_is_case_insensitive_and_tolerates_padding():
    client = _client(TOKEN)
    assert (
        client.get("/api/v1/thing", headers={"Authorization": f"bearer {TOKEN} "}).status_code
        == 200
    )


def test_health_and_static_assets_stay_reachable():
    """A monitor and the token prompt itself must keep working."""
    client = _client(TOKEN)
    assert client.get("/healthz").status_code == 200
    assert client.get("/static/js/main.js").status_code == 200


def test_non_ascii_token_matches_without_raising():
    """`compare_digest` on a raw str raises for non-ASCII, so the gate compares
    bytes. A non-ASCII token reaches the server percent-encoded in a query
    string (HTTP headers cannot carry those bytes at all).
    """
    client = _client("tökén")
    assert client.get("/api/v1/thing?token=t%C3%B6k%C3%A9n").status_code == 200
    assert client.get("/api/v1/thing?token=nope").status_code == 401


def test_presented_token_reads_every_accepted_location():
    from starlette.requests import Request

    def req(**headers):
        raw = [(k.lower().replace("_", "-").encode(), v.encode()) for k, v in headers.items()]
        return Request(
            {"type": "http", "headers": raw, "query_string": b"", "method": "GET", "path": "/"}
        )

    assert presented_token(req(authorization="Bearer abc")) == "abc"
    assert presented_token(req(x_api_token="abc")) == "abc"
    assert presented_token(req(authorization="Basic abc")) == ""
    assert presented_token(req()) == ""


# ── security headers ───────────────────────────────────────────────────────


def test_security_headers_are_attached():
    res = _client().get("/api/v1/thing")
    for name, value in SECURITY_HEADERS.items():
        assert res.headers[name] == value
    assert res.headers["x-frame-options"] == "DENY"


def test_csp_keeps_scripts_first_party_only():
    csp = _client().get("/api/v1/thing").headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "'unsafe-eval'" not in csp


def test_headers_cover_rejections_too():
    res = _client(TOKEN).get("/api/v1/thing")
    assert res.status_code == 401
    assert res.headers["x-content-type-options"] == "nosniff"


def test_hsts_only_when_enabled_for_prod():
    assert "strict-transport-security" not in _client().get("/api/v1/thing").headers
    assert "strict-transport-security" in _client(hsts=True).get("/api/v1/thing").headers


# ── rate limiting behind a proxy ───────────────────────────────────────────


def _limited(trust_proxy_headers: bool) -> TestClient:
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        limiter=RateLimiter(max_requests=1, window_seconds=60),
        trust_proxy_headers=trust_proxy_headers,
    )

    @app.get("/api/v1/thing")
    async def thing():
        return {"ok": True}

    return TestClient(app)


def test_rate_limit_keys_on_the_forwarded_client_when_trusted():
    client = _limited(trust_proxy_headers=True)
    assert client.get("/api/v1/thing", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    # a different visitor behind the same proxy gets their own bucket
    assert client.get("/api/v1/thing", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 200
    assert client.get("/api/v1/thing", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 429


def test_rate_limit_ignores_a_forged_forwarded_header_by_default():
    client = _limited(trust_proxy_headers=False)
    assert client.get("/api/v1/thing", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    res = client.get("/api/v1/thing", headers={"X-Forwarded-For": "9.9.9.9"})
    assert res.status_code == 429  # one shared bucket: the header did not help
    assert res.headers["retry-after"] == "60"
