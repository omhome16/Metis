"""Simple in-process rate limiting (blueprint §14 security baseline)."""

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class RateLimiter:
    def __init__(self, max_requests: int = 120, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        recent = [t for t in self._hits.get(key, []) if now - t < self.window]
        if len(recent) >= self.max_requests:
            self._hits[key] = recent
            return False
        recent.append(now)
        self._hits[key] = recent
        if len(self._hits) > 10_000:  # bound memory: drop expired buckets
            cutoff = now - self.window
            self._hits = {
                k: [t for t in v if t > cutoff]
                for k, v in self._hits.items()
                if any(t > cutoff for t in v)
            }
        return True

    def reset(self) -> None:
        self._hits.clear()


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        limiter: RateLimiter,
        exempt_paths: tuple[str, ...] = ("/healthz",),
        trust_proxy_headers: bool = False,
    ):
        super().__init__(app)
        self.limiter = limiter
        self.exempt_paths = exempt_paths
        # Off by default: a client can forge X-Forwarded-For, so trusting it is
        # only correct behind a proxy that overwrites the header. Without this, a
        # hosted deployment buckets every visitor under the proxy's own address.
        self.trust_proxy_headers = trust_proxy_headers

    def _client_key(self, request: Request) -> str:
        if self.trust_proxy_headers:
            forwarded = request.headers.get("x-forwarded-for", "")
            if forwarded:
                return forwarded.split(",")[0].strip() or "unknown"
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self.exempt_paths:
            return await call_next(request)
        if not self.limiter.allow(self._client_key(request)):
            return JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded, slow down"},
                headers={"Retry-After": str(self.limiter.window)},
            )
        return await call_next(request)
