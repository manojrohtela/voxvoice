"""Shared billing domain vocabulary (M9.2).

The value objects and enumerations every bounded context reuses: tiers, billing
intervals, periods, quantities, and the scope keys Billing attributes usage to. Pure
data — immutable, slotted, no I/O, no behaviour beyond derived read-only properties.

Ownership (ADR-0023 §5): everything here is a *commercial* fact. Organization,
workspace, project, and agent identifiers appear only as **references** used for scoping
and attribution — Billing stores no copy of them as truth and writes none of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .errors import BillingValidationError
from .money import Currency


class Tier(StrEnum):
    """The five standard commercial tiers (Blueprint §6.1). ENTERPRISE is contract-driven
    — same machinery, catalog/prices sourced from a Contract instead of the public
    catalog; there is no separate enterprise code path."""

    FREE = "free"
    STARTER = "starter"
    PRO = "pro"
    BUSINESS = "business"
    ENTERPRISE = "enterprise"


class BillingInterval(StrEnum):
    """Recurring term length. Interval switches restart the term (Blueprint §6.3)."""

    MONTHLY = "monthly"
    YEARLY = "yearly"


class AccountKind(StrEnum):
    """A billing account's commercial role. The account hierarchy is deliberately
    **separate from organization tenancy** (Blueprint §17) so reseller / OEM /
    white-label need no re-architecture."""

    DIRECT = "direct"
    PARTNER = "partner"  # reseller / OEM parent
    CHILD = "child"  # billed through a parent


class ScopeKind(StrEnum):
    """The attribution granularity of a usage record or an entitlement limit."""

    ORGANIZATION = "organization"
    WORKSPACE = "workspace"
    PROJECT = "project"
    AGENT = "agent"


@dataclass(frozen=True, slots=True, order=True)
class Scope:
    """An attribution key. `org_id` is always present (§A11.2); narrower ids are optional
    and reference facts the Customer Platform owns — Billing never writes them."""

    org_id: str
    kind: ScopeKind = ScopeKind.ORGANIZATION
    workspace_id: str = ""
    project_id: str = ""
    agent_id: str = ""

    def __post_init__(self) -> None:
        if not self.org_id:
            raise BillingValidationError(_empty("scope.org_id", "an org_id is required"))

    @property
    def narrowest_id(self) -> str:
        """The most specific reference this scope names (for reporting/breakdowns)."""
        return self.agent_id or self.project_id or self.workspace_id or self.org_id


@dataclass(frozen=True, slots=True, order=True)
class BillingPeriod:
    """A half-open [start, end) window of wall-clock seconds. Charges attach to a period,
    never to "now" (Blueprint §6.2) — this is what makes a billing run reproducible."""

    start: float
    end: float

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise BillingValidationError(_empty("period", "period end must be after start"))

    @property
    def duration(self) -> float:
        return self.end - self.start

    def contains(self, ts: float) -> bool:
        return self.start <= ts < self.end

    def overlaps(self, other: BillingPeriod) -> bool:
        return self.start < other.end and other.start < self.end

    def elapsed_at(self, ts: float) -> float:
        """Seconds elapsed within the period at `ts`, clamped to [0, duration]."""
        return min(max(ts - self.start, 0.0), self.duration)

    def remaining_at(self, ts: float) -> float:
        return self.duration - self.elapsed_at(ts)


@dataclass(frozen=True, slots=True)
class Quantity:
    """A metered quantity in a meter's declared unit. Integer by construction: fractional
    consumption is normalized to the smallest billable unit at the meter definition, so
    no float ever reaches rating or the ledger."""

    units: int
    unit: str

    def __post_init__(self) -> None:
        if self.units < 0:
            raise BillingValidationError(_empty("quantity.units", "units must be >= 0"))
        if not self.unit:
            raise BillingValidationError(_empty("quantity.unit", "a unit is required"))

    def plus(self, other: Quantity) -> Quantity:
        if self.unit != other.unit:
            raise BillingValidationError(
                _empty("quantity.unit", f"cannot add {self.unit} to {other.unit}")
            )
        return Quantity(self.units + other.units, self.unit)

    @classmethod
    def zero(cls, unit: str) -> Quantity:
        return cls(0, unit)


@dataclass(frozen=True, slots=True)
class CurrencyScope:
    """A currency chosen once and fixed for the life of a subscription/account
    (ADR-0023 RS5). Conversion is presentation, never a ledger concern."""

    currency: Currency


def _empty(path: str, message: str):
    """Build a single-error diagnostics set for invariant violations in value objects.

    Value objects guard their own construction invariants (a `Money` with a float, a
    period that ends before it starts). Structural *input* validation — the kind that
    reports many findings at once — lives in `validation.py` and never raises.
    """
    from .diagnostics import BillingDiagnosticCode, BillingDiagnostics, Diagnostic, Severity

    return BillingDiagnostics.of(
        [Diagnostic(path, BillingDiagnosticCode.MISSING_REQUIRED_FIELD, Severity.ERROR, message)]
    )
