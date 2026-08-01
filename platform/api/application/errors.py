"""Typed Application Layer errors (M8.3).

Rooted in `core.errors.PlatformError` (CODING_STANDARDS §3). The Application Layer
raises orchestration-level errors and otherwise lets the Foundation's typed errors
(`FoundationValidationError`/`NotFoundError`/`ConflictError`/`OrganizationIsolationError`/
`AuthorizationError`) propagate unchanged — it never re-wraps a domain outcome into a
vaguer type (no information loss).
"""

from __future__ import annotations

from core.errors import PlatformError


class ApplicationError(PlatformError):
    """Root of Application Layer orchestration errors."""


class NoHandlerError(ApplicationError):
    """A command/query was dispatched with no registered handler (a wiring bug)."""


class UnsupportedOperationError(ApplicationError):
    """A requested use case is not yet available because it would require an additive
    Foundation capability this milestone deliberately does not add (e.g. a hard member
    delete or an organization mutation — Foundation stays unchanged in M8.3)."""
