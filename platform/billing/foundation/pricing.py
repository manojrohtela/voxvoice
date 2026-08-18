"""Pricing — price books, price versions, and every supported price shape (M9.7 domain).

ADR-0023 §5.1 / Blueprint §8. Prices are **immutable and effective-dated**: changing a
price creates a new version, and an existing subscription stays pinned to the version it
was rated against. That pinning is what lets an invoice be re-derived years later, and it
is how grandfathering is expressed — as a version pin, never as a special case.

All amounts are `Money` (integer minor units + currency). Nothing here computes a charge:
rating (M9.11) is a separate pure function. This module only *declares* what a price is.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .domain import BillingInterval
from .money import Currency, Money, Rounding


class PriceShape(StrEnum):
    """The supported commercial shapes (Blueprint §8.1). Any subscription may combine
    several — hybrid pricing is first-class, not an exception."""

    FLAT_RECURRING = "flat_recurring"
    PER_SEAT = "per_seat"
    PER_UNIT = "per_unit"
    ALLOWANCE_OVERAGE = "allowance_overage"
    TIERED_GRADUATED = "tiered_graduated"
    VOLUME = "volume"
    PACKAGE = "package"
    CREDIT = "credit"
    COMMITTED = "committed"
    ONE_OFF = "one_off"


class PriceBookAudience(StrEnum):
    """Who a price book is for. Scoped catalogs are what make reseller/OEM/white-label a
    configuration exercise rather than a re-architecture (Blueprint §17)."""

    PUBLIC = "public"
    PARTNER = "partner"
    CONTRACT = "contract"


@dataclass(frozen=True, slots=True, order=True)
class PriceTier:
    """One band of a tiered/volume price: `[up_to_previous, up_to)` units at `unit_price`.
    `up_to=None` is the open-ended final band."""

    up_to: int | None
    unit_price: Money
    flat_fee: Money | None = None


@dataclass(frozen=True, slots=True)
class EffectiveRange:
    """A half-open [from, until) validity window in wall-clock seconds. `until=None` is
    open-ended. Effective dating is what makes price history queryable rather than lost."""

    effective_from: float
    effective_until: float | None = None

    def covers(self, ts: float) -> bool:
        return self.effective_from <= ts and (
            self.effective_until is None or ts < self.effective_until
        )


@dataclass(frozen=True, slots=True)
class Price:
    """One immutable, versioned price. A price belongs to a price book and names either a
    meter (consumption) or a plan-level charge (access/seats/one-off)."""

    id: str
    price_book_id: str
    code: str
    shape: PriceShape
    currency: Currency
    version: int = 1
    effective: EffectiveRange = EffectiveRange(0.0)
    # plan-level recurring shapes
    interval: BillingInterval | None = None
    amount: Money | None = None  # FLAT_RECURRING / PER_SEAT / ONE_OFF / COMMITTED
    # consumption shapes
    meter_code: str = ""
    unit_amount: Money | None = None  # PER_UNIT / ALLOWANCE_OVERAGE overage rate
    included_units: int = 0  # ALLOWANCE_OVERAGE
    package_size: int = 0  # PACKAGE
    tiers: tuple[PriceTier, ...] = ()  # TIERED_GRADUATED / VOLUME
    credit_units_per_amount: int = 0  # CREDIT
    rounding: Rounding = Rounding.HALF_UP
    description: str = ""

    @property
    def is_recurring(self) -> bool:
        return self.shape in (PriceShape.FLAT_RECURRING, PriceShape.PER_SEAT, PriceShape.COMMITTED)

    @property
    def is_metered(self) -> bool:
        return self.shape in (
            PriceShape.PER_UNIT,
            PriceShape.ALLOWANCE_OVERAGE,
            PriceShape.TIERED_GRADUATED,
            PriceShape.VOLUME,
            PriceShape.PACKAGE,
        )

    def covers(self, ts: float) -> bool:
        return self.effective.covers(ts)


@dataclass(frozen=True, slots=True)
class PriceBook:
    """An immutable, audience-scoped, single-currency collection of prices.

    Single currency by construction: a subscription's currency is fixed at creation
    (ADR-0023 RS5), so a book that mixed currencies could silently produce a
    cross-currency charge line."""

    id: str
    org_id: str  # "" = public/platform book; set = partner- or contract-scoped
    code: str
    display_name: str
    currency: Currency
    audience: PriceBookAudience = PriceBookAudience.PUBLIC
    version: int = 1
    published: bool = False
    prices: tuple[Price, ...] = ()

    def price(self, code: str, *, at: float | None = None) -> Price | None:
        """Resolve a price by code, optionally at an instant. Deterministic: among
        covering versions the highest `version` wins, so resolution never depends on
        insertion order."""
        candidates = [p for p in self.prices if p.code == code]
        if at is not None:
            candidates = [p for p in candidates if p.covers(at)]
        if not candidates:
            return None
        return max(candidates, key=lambda p: (p.version, p.effective.effective_from))

    def for_meter(self, meter_code: str, *, at: float | None = None) -> tuple[Price, ...]:
        found = [p for p in self.prices if p.meter_code == meter_code]
        if at is not None:
            found = [p for p in found if p.covers(at)]
        return tuple(sorted(found, key=lambda p: (p.code, p.version)))
