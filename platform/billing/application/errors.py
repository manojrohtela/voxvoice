"""Typed Application-layer errors (M9.3).

Rooted in `core.errors.PlatformError` (CODING_STANDARDS §3). These are **orchestration**
errors — routing, transaction, and authorization-coordination failures. Domain errors keep
coming from the Foundation unchanged (`BillingValidationError`, `InvalidBillingTransition`,
`ImmutabilityViolation`, …); the Application never re-wraps or re-interprets them, because
re-wrapping would move business meaning up a layer.
"""

from __future__ import annotations

from core.errors import PlatformError


class BillingApplicationError(PlatformError):
    """Root of Billing Application-layer errors."""


class NoHandlerError(BillingApplicationError):
    """No handler is registered for a dispatched command or query."""


class DuplicateHandlerError(BillingApplicationError):
    """Two handlers registered for one message type — a wiring defect, not a runtime
    condition; the bus refuses it rather than silently picking one."""


class UnitOfWorkClosedError(BillingApplicationError):
    """A closed transaction was staged into, committed, or re-entered."""


class BillingAuthorizationError(BillingApplicationError):
    """The injected authorizer denied the action.

    Billing performs **no permission evaluation** (ADR-0023 §11.3 / §7): this error only
    reports a decision made by the authorization seam the Control Plane supplies."""
