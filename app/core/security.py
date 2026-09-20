"""Deployment-facing request guards: auth-mode gating and security headers.

Auth is configurable via `METIS_AUTH_MODE`:

* **users** (default) — real accounts (app/core/auth.py): per-user JWTs, owned
  vaults. This middleware steps aside and does nothing.
* **token** — the original shared-secret gate. `METIS_API_TOKEN` set → every
  protected request must present it (`Authorization: Bearer`, `X-API-Token`, or
  `?token=` — the query form exists for `<a href>` downloads). A shared secret
  answers "may this client use the library", never "who is this".
* **none** — localhost dev, no gate.

`/healthz`, `/auth/*`, and the static shell stay open in token mode so the
uptime monitor, the login screen, and the token prompt itself still work.
"""

import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

TOKEN_HEADER = "x-api-token"

#: Reachable without a token: monitoring, plus the shell that renders the prompt.
OPEN_PATHS = frozenset({"/healthz", "/", "/index.html"})

#: `script-src 'self'` is the load-bearing line — index.html has no inline
#: script. `style-src` has to allow inline styles because a view sets a `style=`
#: attribute (static/js/views/documents.js); script injection is what matters.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    ),
}

HSTS_VALUE = "max-age=31536000; includeSubDomains"


OPEN_PREFIXES = ("/auth",)


def presented_token(request: Request) -> str:
    """The token a request carries, from any accepted location ('' if none)."""
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    custom = request.headers.get(TOKEN_HEADER, "")
    if custom.strip():
        return custom.strip()
    return (request.query_params.get("token") or "").strip()


class ApiTokenMiddleware(BaseHTTPMiddleware):
    """Require `METIS_API_TOKEN` on protected routes (auth_mode='token' only)."""

    def __init__(self, app, token: str, auth_mode: str = "users"):
        super().__init__(app)
        self.token = token or ""
        self.active = auth_mode == "token" and bool(self.token)

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self.active or self._is_open(request.url.path):
            return await call_next(request)
        # compare_digest on bytes: constant-time, and immune to non-ASCII input
        # raising where a plain == would not.
        if secrets.compare_digest(presented_token(request).encode(), self.token.encode()):
            return await call_next(request)
        return JSONResponse(
            status_code=401,
            content={"detail": "missing or invalid API token"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    @staticmethod
    def _is_open(path: str) -> bool:
        return path in OPEN_PATHS or path.startswith(OPEN_PREFIXES) or path.startswith("/static/")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach the deployment baseline to every response."""

    def __init__(self, app, hsts: bool = False):
        super().__init__(app)
        self.hsts = hsts

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if self.hsts:
            # Only meaningful over HTTPS, so it is tied to prod and to nothing else.
            response.headers.setdefault("Strict-Transport-Security", HSTS_VALUE)
        return response
