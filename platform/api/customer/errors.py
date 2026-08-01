"""Centralized error → HTTP translation (M8.5).

The one place typed errors from the lower layers become HTTP responses. Controllers never
catch or translate — they let errors propagate to these handlers, which map each typed
error to a status code and a uniform error envelope carrying the request id. No business
logic; translation only.

Mapping:
  FoundationValidationError      → 422 (+ diagnostics)
  AuthorizationError             → 403
  OrganizationIsolationError     → 403
  NotFoundError                  → 404
  ConflictError                  → 409
  JWTError / auth failures       → 401
  NoHandlerError/ApplicationError→ 400
  PlatformError (typed root)     → 500
  Exception (unexpected)         → 500
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from api.application import ApplicationError, NoHandlerError
from api.foundation import (
    AuthorizationError,
    ConflictError,
    FoundationError,
    FoundationValidationError,
    NotFoundError,
    OrganizationIsolationError,
)
from core.errors import PlatformError

from .jwt import JWTError


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


def _status_for(exc: Exception) -> int:
    if isinstance(exc, FoundationValidationError):
        return 422
    if isinstance(exc, (AuthorizationError, OrganizationIsolationError)):
        return 403
    if isinstance(exc, NotFoundError):
        return 404
    if isinstance(exc, ConflictError):
        return 409
    if isinstance(exc, JWTError):
        return 401
    if isinstance(exc, (NoHandlerError, ApplicationError)):
        return 400
    return 500


def register_exception_handlers(app) -> None:
    """Install the centralized handlers on the FastAPI app."""

    @app.exception_handler(PlatformError)
    async def _platform(request: Request, exc: PlatformError):  # noqa: ANN202
        status = _status_for(exc)
        extra: dict | None = None
        if isinstance(exc, FoundationValidationError):
            extra = {
                "diagnostics": [
                    {"path": d.path, "code": d.code.value, "severity": d.severity.value,
                     "message": d.message}
                    for d in exc.diagnostics.findings
                ]
            }
        type_ = type(exc).__name__
        # 5xx must not leak internal detail; 4xx carries the typed message.
        message = str(exc) if status < 500 else "internal error"
        return _json(request, status, type_, message, extra)

    @app.exception_handler(JWTError)
    async def _jwt(request: Request, exc: JWTError):  # noqa: ANN202
        return _json(request, 401, "Unauthorized", str(exc))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):  # noqa: ANN202
        return _json(request, 500, "InternalError", "internal error")


__all__ = [
    "register_exception_handlers",
    # re-exported so callers/tests can reference the mapped types in one place
    "ApplicationError", "AuthorizationError", "ConflictError", "FoundationError",
    "FoundationValidationError", "NotFoundError", "OrganizationIsolationError", "JWTError",
]
