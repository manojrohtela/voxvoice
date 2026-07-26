"""Failure propagation + aggregation (M6.7B-2).

Per the M6.7A design §12: every capability already raises typed errors under
`core.errors.PlatformError`; they **propagate verbatim** through Integration —
never caught-and-rewrapped. Integration adds exactly one boundary rule
(`IntegrationError`, Bundle 1) and, here in Bundle 2, *reference aggregation*
over failures for observability/HTTP translation. This module implements no
retry, no recovery, and no new error hierarchy (Bundle 2 constraints).

- `PlatformFailureSummary` — a reference-only projection of one failure (the
  original exception is *kept*, never rewrapped) so a boundary can report it.
- `FailureTranslator` — builds a summary from an exception or from a failed
  capability result (`AgentGoalResult.partial_success=False`), preserving the
  capability's own error ownership.
- `IntegrationFailurePolicy` — the deterministic classification of *where* a
  failure originated and whether it is the one integration-boundary error;
  used by the HTTP layer to pick a status code without any capability-specific
  logic. It decides nothing about retries (those remain the owning
  capability's, per §9/§12).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.errors import ConfigError, PlatformError, ProviderError, RuntimeConflict

from .base import IntegrationError

# Deterministic error-class -> category slug. Categories are references for the
# HTTP layer only; they never alter a capability's own error handling.
_CATEGORY: tuple[tuple[type[BaseException], str], ...] = (
    (IntegrationError, "integration_boundary"),
    (ConfigError, "configuration"),
    (ProviderError, "provider"),
    (RuntimeConflict, "conflict"),
    (PlatformError, "platform"),
)


def classify_error(exc: BaseException) -> str:
    """The deterministic category of a typed error (references only)."""
    for cls, slug in _CATEGORY:
        if isinstance(exc, cls):
            return slug
    return "unknown"


@dataclass(frozen=True, slots=True)
class PlatformFailureSummary:
    """A reference-only projection of one failure. `original` holds the actual
    typed error object (or `None` for a non-exception result failure) — never a
    rewrapped copy (design §12)."""

    trace_id: str
    category: str
    error_type: str
    message: str
    origin: str = ""          # capability the failure originated in, when known
    is_boundary_error: bool = False  # True only for IntegrationError (design §12)
    original: BaseException | None = None


class FailureTranslator:
    """Builds `PlatformFailureSummary` references at the integration boundary,
    preserving the capability's own typed error."""

    def from_exception(
        self, exc: BaseException, *, trace_id: str, origin: str = ""
    ) -> PlatformFailureSummary:
        return PlatformFailureSummary(
            trace_id=trace_id,
            category=classify_error(exc),
            error_type=type(exc).__name__,
            message=str(exc),
            origin=origin,
            is_boundary_error=isinstance(exc, IntegrationError),
            original=exc,
        )

    def from_result(
        self, result: Any, *, trace_id: str, origin: str = "agent"
    ) -> PlatformFailureSummary | None:
        """A failed capability result (e.g. `AgentGoalResult.partial_success=
        False`) is a failure without an exception — summarized by reference,
        never synthesized into a raised error."""
        if getattr(result, "partial_success", True):
            return None
        return PlatformFailureSummary(
            trace_id=trace_id,
            category="capability_result",
            error_type=type(result).__name__,
            message=str(getattr(result, "error", "") or "capability reported failure"),
            origin=origin,
            is_boundary_error=False,
            original=None,
        )


class IntegrationFailurePolicy:
    """Deterministic failure classification for the composition-root boundary.
    Owns *no* retry/recovery decision (design §9/§12) — only which failures are
    the one integration-boundary error and how they map to a category."""

    def is_boundary_error(self, exc: BaseException) -> bool:
        return isinstance(exc, IntegrationError)

    def propagate_verbatim(self, exc: BaseException) -> bool:
        """Any typed platform error that is *not* the integration-boundary
        error propagates verbatim — Integration never catches-and-rewraps a
        capability's own error (design §12)."""
        return isinstance(exc, PlatformError) and not isinstance(exc, IntegrationError)

    def category(self, exc: BaseException) -> str:
        return classify_error(exc)
