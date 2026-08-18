"""HTTP middleware (M9.5): request context, correlation ids, timing, metrics, structured
logging, security headers, payload limits.

One ASGI middleware establishes the per-request `BillingRequestContext` (request id +
correlation id, honouring an inbound `X-Correlation-ID` so a trace survives the hop from the
Customer Platform), times the request, records an in-process metric, emits a structured access
log, and stamps security + correlation headers. Compression and CORS are Starlette's own,
wired in the app factory.

Transport concerns only. Note what the access log does **not** contain: no request body, no
token, no amount. Billing request paths carry account and invoice ids; the bodies carry money
and tax identifiers, and those do not belong in a log line.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .context import BillingRequestContext

_log = logging.getLogger("voxvoice.billing.api")

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "X-XSS-Protection": "1; mode=block",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Cache-Control": "no-store",
}


class BillingRequestMetrics:
    """In-process request counters (deterministic, per-app). A real metrics backend plugs in
    behind this later — the API owns the tap, not the sink.

    These are *transport* metrics (requests, statuses, latency). They are emphatically not
    `metric.*` business measurements: Billing consumes those, it never produces them
    (ADR-0023 M8 / ADR-0031 IR9)."""

    def __init__(self) -> None:
        self.count: dict[str, int] = defaultdict(int)
        self.status: dict[int, int] = defaultdict(int)
        self.total_ms: float = 0.0

    def record(self, method: str, path: str, status: int, ms: float) -> None:
        self.count[f"{method} {path}"] += 1
        self.status[status] += 1
        self.total_ms += ms

    def snapshot(self) -> dict:
        return {
            "requests": dict(self.count),
            "statuses": {str(k): v for k, v in self.status.items()},
            "total_ms": round(self.total_ms, 2),
        }


class BillingContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, metrics: BillingRequestMetrics) -> None:
        super().__init__(app)
        self._metrics = metrics

    async def dispatch(self, request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-ID") or uuid.uuid4().hex
        request_id = uuid.uuid4().hex
        request.state.context = BillingRequestContext(
            request_id=request_id, correlation_id=correlation_id
        )
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        self._metrics.record(request.method, request.url.path, response.status_code, elapsed_ms)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Response-Time-ms"] = f"{elapsed_ms:.2f}"
        for header, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        # HSTS is meaningless over plain HTTP — do not advertise it in dev.
        if request.url.scheme != "https" and "Strict-Transport-Security" in response.headers:
            del response.headers["Strict-Transport-Security"]
        _log.info(
            "billing.request",
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


class BillingPayloadLimitMiddleware(BaseHTTPMiddleware):
    """Reject over-large bodies with a clean 413. Uses the declared `Content-Length`.
    Transport concern only."""

    def __init__(self, app, *, max_bytes: int) -> None:
        super().__init__(app)
        self._max = max_bytes

    async def dispatch(self, request: Request, call_next):
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > self._max:
                    ctx = getattr(request.state, "context", None)
                    body: dict = {"type": "PayloadTooLarge", "message": "request body too large"}
                    if ctx is not None:
                        body["request_id"] = ctx.request_id
                    return JSONResponse(status_code=413, content={"error": body})
            except ValueError:
                pass
        return await call_next(request)
