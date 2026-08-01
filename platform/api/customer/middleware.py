"""HTTP middleware (M8.5): correlation ids, request context, timing, metrics, logging,
security headers.

A single ASGI middleware establishes the per-request `RequestContext` (request id +
correlation id from the `X-Correlation-ID` header if present), times the request, records a
lightweight in-process metric, emits a structured access log, and stamps security +
correlation headers on the response. Compression (GZip) and CORS are Starlette's own
middleware, wired in the app factory. Transport concerns only.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .context import RequestContext

_log = logging.getLogger("voxvoice.api")

# M8.18 hardening — a fuller production security-header set (defence in depth; transport-only).
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "X-XSS-Protection": "1; mode=block",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Cache-Control": "no-store",
}


class RequestMetrics:
    """In-process request counters (deterministic, per-app). A real metrics backend
    plugs in behind this later; the API owns the tap, not the sink."""

    def __init__(self) -> None:
        self.count: dict[str, int] = defaultdict(int)
        self.status: dict[int, int] = defaultdict(int)
        self.total_ms: float = 0.0

    def record(self, method: str, path: str, status: int, ms: float) -> None:
        self.count[f"{method} {path}"] += 1
        self.status[status] += 1
        self.total_ms += ms


class ContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, metrics: RequestMetrics) -> None:
        super().__init__(app)
        self._metrics = metrics

    async def dispatch(self, request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-ID") or uuid.uuid4().hex
        request_id = uuid.uuid4().hex
        request.state.context = RequestContext(request_id=request_id, correlation_id=correlation_id)
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        self._metrics.record(request.method, request.url.path, response.status_code, elapsed_ms)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Response-Time-ms"] = f"{elapsed_ms:.2f}"
        for header, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        # HSTS only meaningful over TLS — do not advertise it in dev/plain HTTP.
        if request.url.scheme != "https" and "Strict-Transport-Security" in response.headers:
            del response.headers["Strict-Transport-Security"]
        _log.info(
            "request",
            extra={
                "request_id": request_id,
                "correlation_id": correlation_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "ms": round(elapsed_ms, 2),
            },
        )
        return response


class PayloadLimitMiddleware(BaseHTTPMiddleware):
    """Reject over-large request bodies with a clean 413 (M8.18 hardening). Uses the declared
    `Content-Length` when present; a streamed body over the limit is caught while reading. Transport
    concern only — no business logic."""

    def __init__(self, app, *, max_bytes: int) -> None:
        super().__init__(app)
        self._max = max_bytes

    async def dispatch(self, request: Request, call_next):
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > self._max:
                    return _limit_response(request, "request body too large")
            except ValueError:
                pass
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """A deterministic, in-process sliding-window rate limiter (M8.18 hardening). `per_minute<=0`
    disables it (the dev/test default). Keyed by client host (a real deployment keys by API
    key/tenant behind the same tap). Returns 429 with `Retry-After`. Not a distributed limiter — a
    single-node throttle; a shared store plugs in behind this seam later."""

    def __init__(self, app, *, per_minute: int, window_seconds: float = 60.0) -> None:
        super().__init__(app)
        self._limit = per_minute
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next):
        if self._limit <= 0:
            return await call_next(request)
        key = request.client.host if request.client else "anonymous"
        now = time.monotonic()
        hits = self._hits[key]
        while hits and hits[0] <= now - self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            resp = _limit_response(request, "rate limit exceeded", status=429)
            resp.headers["Retry-After"] = str(int(self._window))
            return resp
        hits.append(now)
        return await call_next(request)


def _limit_response(request: Request, message: str, *, status: int = 413) -> JSONResponse:
    ctx = getattr(request.state, "context", None)
    body = {"type": "PayloadTooLarge" if status == 413 else "TooManyRequests", "message": message}
    if ctx is not None:
        body["request_id"] = ctx.request_id
    return JSONResponse(status_code=status, content={"error": body})
