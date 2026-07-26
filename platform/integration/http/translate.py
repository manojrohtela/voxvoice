"""Framework-neutral HTTP error translation (M6.7B-2)."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..errors.failure import classify_error

# Deterministic category -> HTTP status. Categories come from the error's own
# typed hierarchy (`classify_error`); no capability-specific branching here.
_STATUS_BY_CATEGORY: dict[str, int] = {
    "integration_boundary": 500,   # a composition-root wiring failure
    "configuration": 400,          # invalid/inconsistent configuration
    "provider": 502,               # an upstream provider failed
    "conflict": 409,               # illegal state-machine / lifecycle transition
    "capability_result": 422,      # a capability reported a non-exception failure
    "platform": 500,               # any other typed platform error
    "unknown": 500,                # a non-platform error (should not cross the boundary)
}


@dataclass(frozen=True, slots=True)
class HttpErrorResponse:
    """A framework-neutral HTTP error the `api/` layer maps onto its own
    `HTTPException`. `trace_id` is preserved so a client can correlate."""

    status_code: int
    error_type: str
    category: str
    detail: str
    trace_id: str = ""
    body: dict[str, str] = field(default_factory=dict)


def translate_error(exc: BaseException, *, trace_id: str = "") -> HttpErrorResponse:
    """Deterministically map a typed error to an `HttpErrorResponse`, preserving
    error ownership (the original type name is reported, never rewrapped)."""
    category = classify_error(exc)
    status = _STATUS_BY_CATEGORY.get(category, 500)
    error_type = type(exc).__name__
    detail = str(exc) or error_type
    return HttpErrorResponse(
        status_code=status,
        error_type=error_type,
        category=category,
        detail=detail,
        trace_id=trace_id,
        body={"error": error_type, "category": category, "detail": detail, "trace_id": trace_id},
    )
