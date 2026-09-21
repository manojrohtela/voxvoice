"""The console API error model (M12.1, ADR-0055 DP7).

Every console error leaves as

    {"error": {"type": "<snake_case>", "message": "<safe text>", "request_id": "<id>"}}

— the envelope shape Epic 8 established — with the same id in the `x-request-id` header.

The handler is registered for `ConsoleError` **only**. It deliberately does not claim
`HTTPException` or `RequestValidationError`: the console app also serves the M11 developer API,
whose `{"detail": …}` bodies are a public contract that must not change under it.
"""

from __future__ import annotations

import re
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

REQUEST_ID_HEADER = "x-request-id"

#: An inbound request id is echoed only when it is a short, inert token; anything else is
#: replaced, so a caller cannot inject arbitrary text into our responses or logs.
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")


class ConsoleError(Exception):
    """A failure the console reports to its user. `message` must be safe to display."""

    type = "internal"
    status_code = 500

    def __init__(self, message: str = "Something went wrong") -> None:
        super().__init__(message)
        self.message = message


class InvalidRequest(ConsoleError):
    type, status_code = "invalid_request", 400


class Unauthenticated(ConsoleError):
    type, status_code = "unauthenticated", 401


class Forbidden(ConsoleError):
    type, status_code = "forbidden", 403


class NotFound(ConsoleError):
    """Missing — or belonging to another organization, which must look identical (S4)."""

    type, status_code = "not_found", 404


class Conflict(ConsoleError):
    type, status_code = "conflict", 409


class RateLimited(ConsoleError):
    type, status_code = "rate_limited", 429


class RuntimeUnavailable(ConsoleError):
    type, status_code = "runtime_unavailable", 503


def request_id_for(request: Request) -> str:
    """The caller's `x-request-id` when it is a safe token, otherwise a fresh one."""
    inbound = request.headers.get(REQUEST_ID_HEADER, "")
    if _REQUEST_ID_RE.fullmatch(inbound):
        return inbound
    return uuid.uuid4().hex


def _envelope(request: Request, exc: ConsoleError) -> JSONResponse:
    request_id = request_id_for(request)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"type": exc.type, "message": exc.message, "request_id": request_id}},
        headers={REQUEST_ID_HEADER: request_id},
    )


async def _handle_console_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ConsoleError)  # registered for ConsoleError only
    return _envelope(request, exc)


def register_console_error_handler(app: FastAPI) -> None:
    """Scope the envelope to console errors; every other handler is left exactly as it was."""
    app.add_exception_handler(ConsoleError, _handle_console_error)


__all__ = [
    "REQUEST_ID_HEADER",
    "Conflict",
    "ConsoleError",
    "Forbidden",
    "InvalidRequest",
    "NotFound",
    "RateLimited",
    "RuntimeUnavailable",
    "Unauthenticated",
    "register_console_error_handler",
    "request_id_for",
]
