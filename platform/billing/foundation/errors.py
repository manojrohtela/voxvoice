"""Typed Billing Foundation errors (M9.2).

Rooted in `core.errors.PlatformError` per CODING_STANDARDS §3 — one typed hierarchy, no
bare or catch-all errors, and no vendor error ever crossing a boundary (there are no
vendors in the Foundation at all). Definitions only; no behaviour, no I/O.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.errors import ConfigError, PlatformError, RuntimeConflict

if TYPE_CHECKING:
    from .diagnostics import BillingDiagnostics


class BillingError(PlatformError):
    """Root of all Billing Platform errors."""


class BillingValidationError(BillingError, ConfigError):
    """An input failed structural validation. Carries the full diagnostics so the caller
    sees every finding, not just the first (fail fast, never run degraded — §A2.6)."""

    def __init__(self, diagnostics: BillingDiagnostics) -> None:
        self.diagnostics = diagnostics
        super().__init__(diagnostics.summary())


class InvalidMoney(BillingError, ConfigError):
    """A money value or operation violated the integer-minor-unit discipline."""


class CurrencyMismatch(InvalidMoney):
    """Arithmetic or comparison was attempted across two currencies (ADR-0023 RS5)."""


class BillingNotFound(BillingError):
    """A referenced billing aggregate does not exist within the caller's organization."""


class BillingConflict(BillingError):
    """A uniqueness or lifecycle invariant would be violated."""


class RefundSequenceConflict(BillingConflict):
    """Another worker took this capture's next refund slot (M9.16-R1, finding P-3).

    Not a replay and not a failure: it means a concurrent refund committed first, so the caller
    must re-read the refunds and re-check the ceiling against it. Declared here rather than in
    persistence because the *application* must react to it and may not import persistence — the
    layering rule that made this the right home."""


class OrganizationIsolationError(BillingError):
    """A cross-organization access was attempted — structurally forbidden (§A11.2)."""


class ImmutabilityViolation(BillingError):
    """An attempt to alter an immutable revenue fact (an issued invoice, a ledger entry,
    a sealed usage period, a published price version). ADR-0023 RS1/RS3, IN2."""


class InvalidBillingTransition(BillingError, RuntimeConflict):
    """An illegal lifecycle transition. Every billing lifecycle is a validated,
    table-driven state machine (§A2.4); illegal edges are rejected, never coerced."""


class EntitlementNotDecidable(BillingError):
    """An entitlement could not be computed from the supplied grants. Billing decides
    entitlement; it never *enforces* it and never evaluates a principal (ADR-0023 §11)."""
