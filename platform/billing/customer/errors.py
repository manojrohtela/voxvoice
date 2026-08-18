"""Centralized error → HTTP translation (M9.5).

The one place typed errors from the lower layers become HTTP responses. **Controllers never
catch and never translate** — they let errors propagate to these handlers, which map each
typed error to a status and a uniform envelope carrying the request id.

Having exactly one translation table is what keeps the mapping honest: a new domain error gets
a status here, once, rather than a different status in each of fifteen routers.

| Error | Status |
|---|---|
| `BillingValidationError` | 422 (+ diagnostics) |
| `InvalidMoney` / `CurrencyMismatch` | 422 |
| `BillingAuthorizationError` / `OrganizationIsolationError` | 403 |
| `BillingNotFound` | 404 |
| `BillingConflict` / `InvalidBillingTransition` / `ImmutabilityViolation` | 409 |
| `BillingJWTError` | 401 |
| `NoHandlerError` / `BillingApplicationError` | 400 |
| `EntitlementNotDecidable` | 409 |
| anything else | 500 (detail withheld) |

Two deliberate choices. **`ImmutabilityViolation` is 409, not 500**: refusing to alter an
issued invoice or a sealed period is the system working correctly, and a caller must be able to
tell that apart from a fault. **5xx never carries the exception message** — a stack detail on
an internal fault is an information leak, while every 4xx carries its typed message because the
caller can act on it.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from billing.application import BillingApplicationError, BillingAuthorizationError, NoHandlerError
from billing.foundation import (
    BillingConflict,
    BillingNotFound,
    BillingValidationError,
    EntitlementNotDecidable,
    ImmutabilityViolation,
    InvalidBillingTransition,
    InvalidMoney,
    OrganizationIsolationError,
)
from core.errors import PlatformError

from .jwt import BillingJWTError


def _envelope(request: Request, type_: str, message: str, extra: dict | None = None) -> dict:
    ctx = getattr(request.state, "context", None)
    body: dict[str, Any] = {"type": type_, "message": message}
    if ctx is not None:
        body["request_id"] = ctx.request_id
    if extra:
        body.update(extra)
    return {"error": body}


def _json(request: Request, status: int, type_: str, message: str, extra: dict | None = None):
    return JSONResponse(status_code=status, content=_envelope(request, type_, message, extra))


def status_for(exc: Exception) -> int:
    """The single translation table. Ordered most-specific first."""
    if isinstance(exc, BillingValidationError):
        return 422
    if isinstance(exc, InvalidMoney):  # includes CurrencyMismatch
        return 422
    if isinstance(exc, (BillingAuthorizationError, OrganizationIsolationError)):
        return 403
    if isinstance(exc, BillingNotFound):
        return 404
    if isinstance(exc, (BillingConflict, InvalidBillingTransition, ImmutabilityViolation)):
        return 409
    if isinstance(exc, EntitlementNotDecidable):
        return 409
    if isinstance(exc, BillingJWTError):
        return 401
    if isinstance(exc, (NoHandlerError, BillingApplicationError)):
        return 400
    return 500


def register_exception_handlers(app) -> None:
    """Install the centralized handlers on the FastAPI app."""

    @app.exception_handler(PlatformError)
    async def _platform(request: Request, exc: PlatformError):  # noqa: ANN202
        status = status_for(exc)
        extra: dict | None = None
        if isinstance(exc, BillingValidationError):
            # Every finding, not just the first — a caller fixing a request should need one
            # round trip, not five (§A2.6).
            extra = {
                "diagnostics": [
                    {
                        "path": d.path,
                        "code": d.code.value,
                        "severity": d.severity.value,
                        "message": d.message,
                    }
                    for d in exc.diagnostics.findings
                ]
            }
        message = str(exc) if status < 500 else "internal error"
        return _json(request, status, type(exc).__name__, message, extra)

    @app.exception_handler(BillingJWTError)
    async def _jwt(request: Request, exc: BillingJWTError):  # noqa: ANN202
        return _json(request, 401, "Unauthorized", str(exc))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):  # noqa: ANN202
        return _json(request, 500, "InternalError", "internal error")


__all__ = [
    "BillingApplicationError",
    "BillingAuthorizationError",
    "BillingConflict",
    "BillingJWTError",
    "BillingNotFound",
    "BillingValidationError",
    "ImmutabilityViolation",
    "register_exception_handlers",
    "status_for",
]
