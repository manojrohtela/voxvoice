"""Typed Customer Platform Foundation errors (M8.2).

Rooted in `core.errors.PlatformError` per CODING_STANDARDS §3 (one typed hierarchy,
no bare/catch-all). Every boundary raises typed errors carrying enough context to see
*why*, not just *that*, an operation failed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.errors import ConfigError, PlatformError

if TYPE_CHECKING:
    from .diagnostics import FoundationDiagnostics


class FoundationError(PlatformError):
    """Root of Customer Platform Foundation errors."""


class FoundationValidationError(FoundationError, ConfigError):
    """An input failed structural validation. Carries the full diagnostics so the
    caller sees every finding, not just the first (fail fast, do not run degraded)."""

    def __init__(self, diagnostics: FoundationDiagnostics) -> None:
        self.diagnostics = diagnostics
        super().__init__(diagnostics.summary())


class NotFoundError(FoundationError):
    """A referenced aggregate does not exist within the caller's organization."""


class ConflictError(FoundationError):
    """A uniqueness or lifecycle invariant would be violated (e.g. duplicate slug,
    re-inviting an active member)."""


class OrganizationIsolationError(FoundationError):
    """A cross-organization access was attempted — structurally forbidden
    (Constitution §A11.2; org-scoping is enforced on every query, never optional)."""


class AuthorizationError(FoundationError):
    """The actor lacks the permission required for this action (RBAC deny)."""
