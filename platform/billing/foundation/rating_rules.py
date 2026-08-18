"""Rating rules (M9.12) — the pure arithmetic that turns immutable inputs into charges.

Every rule Billing applies when calculating money lives here. Nothing in this module reads a
clock, touches persistence, resolves a price, folds a meter, or performs any I/O: rating is a
**pure function of its inputs**, and that is not a stylistic preference — it is the only way
"identical inputs always produce identical charges" can be asserted by a test rather than hoped
for in production.

Two design decisions carry the milestone.

## 1. All money arithmetic is integer arithmetic

Not one float participates. Extension is `Money.times` (exact), proportional reduction is
`Money.scaled` with a declared rounding policy, and splitting a total across several claimants is
`Money.allocate` (largest-remainder, so the parts always sum to the whole). A rounding artefact in
the eighth decimal place of a rate is a revenue incident nobody can reproduce; there are no
decimal places here to have an artefact in.

## 2. Discount re-basing — a defect in a frozen milestone, fixed additively

M9.7's `PriceResolver` folds **every** eligible discount into the *unit* rate, including
`FIXED_AMOUNT`, `BUNDLE`, and `COMMITMENT` reductions, and reports the per-unit figure. For a
flat or one-off price that is exactly right. For a per-seat, per-unit, tiered, volume, or package
price it is not: rating multiplies the rate by a quantity, so a one-time "₹500 off this line"
silently becomes "₹500 off *each* minute" — a reduction multiplied by consumption.

M9.7 is frozen and correct at its own altitude: it answers "what does one unit cost?", and a
per-unit answer is the only answer it can give without a quantity. The quantity is rating's, and
so is the fix. `DiscountRebasingPolicy` therefore:

* **re-bases** the rate for extended shapes — adding the fixed sums back to the net rate, so the
  rate rating multiplies is the list rate less only the *proportional* reductions, which scale
  correctly;
* subtracts each fixed sum **once**, at charge level, where a fixed sum belongs;
* records both moves as visible `REBASE` and `DISCOUNT` components, so the breakdown shows the
  correction rather than hiding it;
* raises a `FIXED_DISCOUNT_ON_METERED_RATE` **warning** through `RatingValidator`, because a
  fixed-sum discount authored against a metered price is usually an authoring mistake worth
  seeing even once rating has handled it safely.

Nothing in M9.7 changes. For every shape rating does *not* multiply — flat, one-off, committed —
the re-based result is arithmetically identical to M9.7's, so the correction is invisible exactly
where M9.7 was already right.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from core.fsm import StateMachineDefinition

from .commercial_rating import (
    EXTENDED_CHARGE_KINDS,
    FIXED_SUM_DISCOUNT_KINDS,
    RATING_LIFECYCLE,
    SHAPE_TO_CHARGE_KIND,
    ChargeComponent,
    ChargeExplanation,
    ChargeInspection,
    ChargeKind,
    ChargeProvenance,
    ComponentKind,
    DifferenceKind,
    IncludedUnitsSource,
    MinimumCommitment,
    RatedCharge,
    RatingBasis,
    RatingDelta,
    RatingDifference,
    RatingExplanation,
    RatingInspection,
    RatingMode,
    RatingResult,
    RatingRun,
    RatingSearchQuery,
    RatingSearchResult,
    RatingState,
    RatingTrigger,
    RerateReason,
    RerateVerdict,
    derive_charge_id,
)
from .diagnostics import BillingDiagnosticCode, BillingDiagnostics, Diagnostic, Severity
from .domain import BillingPeriod
from .errors import BillingConflict, InvalidBillingTransition
from .money import Currency, Money
from .price_books import AppliedDiscount, ResolvedPrice
from .pricing import PriceShape, PriceTier

# ── service inputs ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ChargeRequest:
    """One thing to charge for: a subscribed item, the price already resolved for it, and the
    quantity it consumed.

    The `ResolvedPrice` is an **input**, not something this module computes. That is the whole
    boundary between M9.7 and M9.12 in one field: pricing answers "what does one unit cost for
    this customer, at this instant?" and rating multiplies. A test asserts this module never
    constructs a `PriceResolutionRequest` or calls a resolver."""

    item_id: str
    price: ResolvedPrice
    quantity: int = 0
    unit: str = ""
    label: str = ""
    meter_code: str = ""
    meter_pin: str = ""
    usage_ids: tuple[str, ...] = ()
    usage_digest: str = ""
    entitlement_allowance: int | None = None
    entitlement_version: int = 0
    subscription_version: int = 1
    catalog_version: int | None = None
    pricing_version: int | None = None
    price_version: int = 1

    @property
    def is_metered(self) -> bool:
        return bool(self.meter_code)


@dataclass(frozen=True, slots=True)
class RebasedRate:
    """A rate rating may safely multiply, plus the fixed sums held back for charge level.

    `was_rebased` is what the breakdown and the diagnostics both read — the correction is never
    silent."""

    rate: Money | None = None
    tiers: tuple[PriceTier, ...] = ()
    fixed_reduction: Money | None = None
    fixed_discounts: tuple[AppliedDiscount, ...] = ()
    proportional_discounts: tuple[AppliedDiscount, ...] = ()
    was_rebased: bool = False


# ── included quantities ─────────────────────────────────────────────────────────


class IncludedQuantityResolver:
    """How many units are free before anything is charged, and on whose authority.

    Two authorities can declare an allowance: the price (`Price.included_units`, an
    `ALLOWANCE_OVERAGE` shape) and the published entitlement snapshot (M9.10, an allowance on the
    quota gate for the meter). **Declared precedence: the entitlement snapshot wins when it
    declares one at all.**

    Declared rather than inferred, and in one place, because the alternatives are worse. Taking
    the larger would let a promotional entitlement permanently raise a plan's allowance; taking
    the smaller would let a stale snapshot quietly bill a customer for units their plan includes.
    A contract grant that raises an allowance is the *whole point* of M9.10's grant precedence, so
    the entitlement is the more specific statement and it wins — and which one won is recorded on
    every charge."""

    def resolve(
        self, *, price_included: int, entitlement_allowance: int | None
    ) -> tuple[int, IncludedUnitsSource]:
        if entitlement_allowance is not None:
            return max(entitlement_allowance, 0), IncludedUnitsSource.ENTITLEMENT
        if price_included > 0:
            return price_included, IncludedUnitsSource.PRICE
        return 0, IncludedUnitsSource.NONE

    def overage(self, units: int, included: int) -> int:
        """Units beyond the allowance. Never negative — unused allowance is not a credit."""
        return max(units - included, 0)


# ── discount re-basing ──────────────────────────────────────────────────────────


class DiscountRebasingPolicy:
    """Separates reductions that scale with quantity from reductions that do not.

    See the module docstring for why this exists. In one sentence: M9.7 folds fixed sums into a
    unit rate because it has no quantity, and rating has the quantity, so rating must undo the
    fold before multiplying or a one-time reduction becomes a per-unit one."""

    def rebase(self, price: ResolvedPrice, kind: ChargeKind) -> RebasedRate:
        fixed = tuple(d for d in price.discounts if d.kind in FIXED_SUM_DISCOUNT_KINDS)
        proportional = tuple(d for d in price.discounts if d.kind not in FIXED_SUM_DISCOUNT_KINDS)
        net_rate = price.net_amount if price.net_amount is not None else price.list_amount
        net_tiers = price.net_tiers or price.tiers

        # Nothing to undo: either there is no fixed-sum reduction, or the shape is one rating
        # does not multiply, in which case M9.7's per-unit fold *is* the charge-level answer.
        if not fixed or kind not in EXTENDED_CHARGE_KINDS:
            return RebasedRate(
                rate=net_rate,
                tiers=net_tiers,
                fixed_reduction=None,
                fixed_discounts=(),
                proportional_discounts=price.discounts,
                was_rebased=False,
            )

        held_back = Money.zero(price.currency)
        for discount in fixed:
            held_back = held_back + discount.reduction

        rebased_rate = None if net_rate is None else net_rate + held_back
        rebased_tiers = tuple(
            replace(tier, unit_price=tier.unit_price + held_back) for tier in net_tiers
        )
        return RebasedRate(
            rate=rebased_rate,
            tiers=rebased_tiers,
            fixed_reduction=held_back,
            fixed_discounts=fixed,
            proportional_discounts=proportional,
            was_rebased=True,
        )


# ── the charge calculator ───────────────────────────────────────────────────────


class ChargeCalculator:
    """The arithmetic of every supported commercial shape.

    Each method returns the components at **list** rates. Reductions are appended afterwards as
    negative components, so a breakdown reads the way an invoice does — gross lines, then what
    came off — and so `sum(components) == net_amount` holds by construction rather than by a
    second calculation that could disagree."""

    def __init__(self, included: IncludedQuantityResolver | None = None) -> None:
        self._included = included or IncludedQuantityResolver()

    # ── plan-level shapes ───────────────────────────────────────────────────────

    def flat(self, rate: Money, *, label: str = "Subscription") -> tuple[ChargeComponent, ...]:
        return (ChargeComponent(kind=ComponentKind.BASE, label=label, amount=rate),)

    def one_off(self, rate: Money, *, label: str = "One-off charge") -> tuple[ChargeComponent, ...]:
        return (ChargeComponent(kind=ComponentKind.BASE, label=label, amount=rate),)

    def seats(
        self, rate: Money, seats: int, *, label: str = "Seats"
    ) -> tuple[ChargeComponent, ...]:
        count = max(seats, 0)
        return (
            ChargeComponent(
                kind=ComponentKind.SEATS,
                label=label,
                amount=rate.times(count),
                units=count,
                unit_amount=rate,
                detail=f"{count} × {rate}",
            ),
        )

    # ── consumption shapes ──────────────────────────────────────────────────────

    def per_unit(
        self, rate: Money, units: int, *, unit: str = "unit", label: str = "Usage"
    ) -> tuple[ChargeComponent, ...]:
        count = max(units, 0)
        return (
            ChargeComponent(
                kind=ComponentKind.UNITS,
                label=label,
                amount=rate.times(count),
                units=count,
                unit_amount=rate,
                detail=f"{count} {unit} × {rate}",
            ),
        )

    def allowance_overage(
        self,
        rate: Money,
        units: int,
        included: int,
        *,
        unit: str = "unit",
        source: IncludedUnitsSource = IncludedUnitsSource.PRICE,
    ) -> tuple[ChargeComponent, ...]:
        """An allowance and its overage, as **two** components.

        The included band is carried explicitly at zero rather than silently subtracted, because
        "you used 1,400 minutes, 1,000 were included, 400 were charged" is the sentence a customer
        needs, and a breakdown that only shows 400 cannot produce it."""
        consumed = max(units, 0)
        covered = min(consumed, max(included, 0))
        over = self._included.overage(consumed, included)
        return (
            ChargeComponent(
                kind=ComponentKind.INCLUDED,
                label="Included",
                amount=Money.zero(rate.currency),
                units=covered,
                detail=f"{covered} {unit} included ({source.value})",
            ),
            ChargeComponent(
                kind=ComponentKind.OVERAGE_UNITS,
                label="Overage",
                amount=rate.times(over),
                units=over,
                unit_amount=rate,
                detail=f"{over} {unit} × {rate}",
            ),
        )

    def graduated(
        self, tiers: Sequence[PriceTier], units: int, *, unit: str = "unit"
    ) -> tuple[ChargeComponent, ...]:
        """Graduated tiers: each band charges only the units that fall *inside* it.

        The bands are walked in declared order and the cumulative ceiling carried forward, so a
        band's width is derived rather than assumed. A band with `up_to=None` is open-ended and
        absorbs everything remaining."""
        remaining = max(units, 0)
        floor = 0
        components: list[ChargeComponent] = []
        for index, tier in enumerate(tiers):
            if remaining <= 0:
                break
            width = remaining if tier.up_to is None else max(tier.up_to - floor, 0)
            band_units = min(remaining, width)
            if band_units <= 0 and tier.flat_fee is None:
                floor = tier.up_to if tier.up_to is not None else floor
                continue
            ceiling = "∞" if tier.up_to is None else str(tier.up_to)
            components.append(
                ChargeComponent(
                    kind=ComponentKind.TIER_BAND,
                    label=f"Tier {index + 1} ({floor}–{ceiling})",
                    amount=tier.unit_price.times(band_units),
                    units=band_units,
                    unit_amount=tier.unit_price,
                    detail=f"{band_units} {unit} × {tier.unit_price}",
                )
            )
            if tier.flat_fee is not None and not tier.flat_fee.is_zero:
                components.append(
                    ChargeComponent(
                        kind=ComponentKind.TIER_FLAT_FEE,
                        label=f"Tier {index + 1} flat fee",
                        amount=tier.flat_fee,
                    )
                )
            remaining -= band_units
            floor = tier.up_to if tier.up_to is not None else floor
        return tuple(components)

    def volume(
        self, tiers: Sequence[PriceTier], units: int, *, unit: str = "unit"
    ) -> tuple[ChargeComponent, ...]:
        """Volume tiers: the **whole** quantity is charged at the band it lands in.

        The difference from graduated pricing is the entire commercial point of having both, and
        it is why they are separate `ChargeKind`s: a customer who crosses a volume threshold gets
        a cheaper rate on everything, including the units they had already consumed."""
        consumed = max(units, 0)
        band = self._band_for(tiers, consumed)
        if band is None:
            return ()
        index = list(tiers).index(band)
        ceiling = "∞" if band.up_to is None else str(band.up_to)
        components = [
            ChargeComponent(
                kind=ComponentKind.TIER_BAND,
                label=f"Volume tier {index + 1} (≤{ceiling})",
                amount=band.unit_price.times(consumed),
                units=consumed,
                unit_amount=band.unit_price,
                detail=f"all {consumed} {unit} × {band.unit_price}",
            )
        ]
        if band.flat_fee is not None and not band.flat_fee.is_zero:
            components.append(
                ChargeComponent(
                    kind=ComponentKind.TIER_FLAT_FEE,
                    label=f"Volume tier {index + 1} flat fee",
                    amount=band.flat_fee,
                )
            )
        return tuple(components)

    def package(
        self, rate: Money, units: int, package_size: int, *, unit: str = "unit"
    ) -> tuple[ChargeComponent, ...]:
        """Whole blocks, always rounded **up** — a partial block is a bought block.

        Integer ceiling division (`-(-a // b)`), never `math.ceil` on a float quotient: the float
        route is wrong for large counts and is exactly the kind of arithmetic that produces a
        one-unit discrepancy nobody can reproduce."""
        consumed = max(units, 0)
        size = max(package_size, 1)
        blocks = -(-consumed // size)
        return (
            ChargeComponent(
                kind=ComponentKind.PACKAGE_BLOCKS,
                label="Packages",
                amount=rate.times(blocks),
                units=blocks,
                unit_amount=rate,
                detail=f"{consumed} {unit} → {blocks} × {size} {unit} @ {rate}",
            ),
        )

    @staticmethod
    def _band_for(tiers: Sequence[PriceTier], units: int) -> PriceTier | None:
        for tier in tiers:
            if tier.up_to is None or units <= tier.up_to:
                return tier
        return tiers[-1] if tiers else None


# ── charge assembly ─────────────────────────────────────────────────────────────


class ChargeAssembler:
    """Turns one `ChargeRequest` into one (or, for an allowance shape, one) `RatedCharge`.

    This is where the list components, the re-basing, the reductions, and the provenance are
    composed into an immutable charge whose breakdown adds up. Everything it needs is in the
    request; it reads nothing else and it has no state."""

    def __init__(
        self,
        calculator: ChargeCalculator | None = None,
        rebasing: DiscountRebasingPolicy | None = None,
        included: IncludedQuantityResolver | None = None,
    ) -> None:
        self._calc = calculator or ChargeCalculator(included)
        self._rebasing = rebasing or DiscountRebasingPolicy()
        self._included = included or IncludedQuantityResolver()

    def assemble(
        self, request: ChargeRequest, *, org_id: str, account_id: str, rating_run_id: str
    ) -> RatedCharge:
        price = request.price
        kind = self._kind(price, request)
        rebased = self._rebasing.rebase(price, kind)

        list_components = self._components(request, kind, self._list_rate(price), price.tiers)
        net_components = self._components(request, kind, rebased.rate, rebased.tiers)

        list_total = _total(list_components, price.currency)
        rebased_total = _total(net_components, price.currency)
        proportional_reduction = list_total - rebased_total
        fixed_reduction = rebased.fixed_reduction or Money.zero(price.currency)
        net_total = rebased_total - fixed_reduction

        components = list(list_components)
        if rebased.was_rebased:
            components.append(
                ChargeComponent(
                    kind=ComponentKind.REBASE,
                    label="Fixed reduction held to charge level",
                    amount=Money.zero(price.currency),
                    detail=(
                        f"{fixed_reduction} was folded into the unit rate by pricing and is "
                        "applied once here instead of per unit"
                    ),
                )
            )
        applied = self._applied(rebased, proportional_reduction, price.currency)
        for discount in applied:
            components.append(
                ChargeComponent(
                    kind=ComponentKind.DISCOUNT,
                    label=f"{discount.display_name or discount.code}",
                    amount=-discount.reduction,
                    detail=f"{discount.kind.value} ({discount.source})",
                )
            )

        _, source = self._included.resolve(
            price_included=price.included_units,
            entitlement_allowance=request.entitlement_allowance,
        )
        charge_id = derive_charge_id(
            rating_run_id,
            request.item_id,
            f"{price.price_code}@{request.price_version}",
            kind.value,
        )
        return RatedCharge(
            id=charge_id,
            org_id=org_id,
            account_id=account_id,
            kind=kind,
            description=request.label or self._describe(kind, price),
            currency=price.currency,
            quantity=max(request.quantity, 0),
            unit=request.unit,
            list_amount=list_total,
            gross_amount=list_total,
            discount_amount=proportional_reduction + fixed_reduction,
            net_amount=net_total,
            components=tuple(components),
            discounts=applied,
            provenance=ChargeProvenance(
                price_code=price.price_code,
                price_version=request.price_version,
                price_book_code=price.price_book_code,
                price_book_version=price.price_book_version,
                catalog_version=request.catalog_version
                if request.catalog_version is not None
                else price.catalog_version,
                pricing_version=request.pricing_version,
                subscription_id="",
                subscription_version=request.subscription_version,
                item_id=request.item_id,
                meter_code=request.meter_code or price.meter_code,
                meter_pin=request.meter_pin,
                usage_ids=request.usage_ids,
                usage_digest=request.usage_digest,
                entitlement_version=request.entitlement_version,
                included_units_source=source,
                override_applied=price.override_applied,
                resolution_path=price.resolution_path,
            ),
            interval=price.interval,
            shape=price.shape,
        )

    # ── internals ───────────────────────────────────────────────────────────────

    def _kind(self, price: ResolvedPrice, request: ChargeRequest) -> ChargeKind:
        if price.shape is PriceShape.ALLOWANCE_OVERAGE:
            return ChargeKind.OVERAGE
        mapped = SHAPE_TO_CHARGE_KIND.get(price.shape)
        if mapped is not None:
            return mapped
        # CREDIT prices are not charges — a caller that supplies one gets a zero usage charge
        # rather than an invented shape. Credits are a different domain entirely (M9.13).
        return ChargeKind.USAGE

    @staticmethod
    def _list_rate(price: ResolvedPrice) -> Money | None:
        return price.list_amount if price.list_amount is not None else price.unit_amount

    def _components(
        self,
        request: ChargeRequest,
        kind: ChargeKind,
        rate: Money | None,
        tiers: Sequence[PriceTier],
    ) -> tuple[ChargeComponent, ...]:
        price = request.price
        units = max(request.quantity, 0)
        if kind is ChargeKind.TIERED:
            return self._calc.graduated(tiers, units, unit=request.unit or "unit")
        if kind is ChargeKind.VOLUME:
            return self._calc.volume(tiers, units, unit=request.unit or "unit")
        if rate is None:
            return ()
        if kind is ChargeKind.FLAT:
            return self._calc.flat(rate, label=request.label or "Subscription")
        if kind is ChargeKind.ONE_OFF:
            return self._calc.one_off(rate, label=request.label or "One-off charge")
        if kind is ChargeKind.SEAT:
            return self._calc.seats(rate, units, label=request.label or "Seats")
        if kind is ChargeKind.PACKAGE:
            return self._calc.package(rate, units, price.package_size, unit=request.unit or "unit")
        if kind is ChargeKind.OVERAGE:
            included, source = self._included.resolve(
                price_included=price.included_units,
                entitlement_allowance=request.entitlement_allowance,
            )
            return self._calc.allowance_overage(
                rate, units, included, unit=request.unit or "unit", source=source
            )
        return self._calc.per_unit(
            rate, units, unit=request.unit or "unit", label=request.label or "Usage"
        )

    def _applied(
        self, rebased: RebasedRate, proportional: Money, currency: Currency
    ) -> tuple[AppliedDiscount, ...]:
        """Re-express the price-level reduction trail at **charge** level.

        The proportional total is split across the proportional discounts with `Money.allocate`,
        weighted by each one's per-unit reduction. Exact by construction: the parts always sum to
        the whole, so the reported trail and the amount can never disagree by a minor unit. The
        fixed sums are reported at their declared value, once."""
        out: list[AppliedDiscount] = []
        weights = tuple(max(d.reduction.minor, 0) for d in rebased.proportional_discounts)
        if rebased.proportional_discounts and sum(weights) > 0 and not proportional.is_zero:
            shares = proportional.allocate(weights)
            out.extend(
                replace(discount, reduction=share)
                for discount, share in zip(rebased.proportional_discounts, shares, strict=True)
            )
        elif rebased.proportional_discounts and not proportional.is_zero:
            # Every weight is zero yet a reduction exists (a tiered price whose reported band
            # figure was zero). Attribute it to the first declared policy rather than dropping it
            # — an unattributed reduction is worse than an imperfectly attributed one.
            first = rebased.proportional_discounts[0]
            out.append(replace(first, reduction=proportional))
            out.extend(
                replace(d, reduction=Money.zero(currency))
                for d in rebased.proportional_discounts[1:]
            )
        else:
            out.extend(
                replace(d, reduction=Money.zero(currency)) for d in rebased.proportional_discounts
            )
        out.extend(rebased.fixed_discounts)
        return tuple(out)

    @staticmethod
    def _describe(kind: ChargeKind, price: ResolvedPrice) -> str:
        return f"{kind.value.replace('_', ' ').title()} — {price.price_code}"


def _total(components: Sequence[ChargeComponent], currency: Currency) -> Money:
    total = Money.zero(currency)
    for component in components:
        total = total + component.amount
    return total


# ── minimum commitments ─────────────────────────────────────────────────────────


class CommitmentService:
    """Minimum commitments — applied **after** every other charge, never before one.

    Order matters and is fixed here rather than left to a caller: a shortfall computed against a
    partial charge set would depend on the order charges happened to be calculated in, and a
    commitment true-up that changes when you reorder a list is not a commitment."""

    def consumed(
        self, charges: Sequence[RatedCharge], commitment: MinimumCommitment, currency: Currency
    ) -> Money:
        total = Money.zero(currency)
        for charge in charges:
            if charge.kind is ChargeKind.MINIMUM_COMMITMENT:
                continue  # a commitment never counts toward itself
            if commitment.covers_price(charge.provenance.price_code):
                total = total + charge.amount
        return total

    def true_up(
        self,
        charges: Sequence[RatedCharge],
        commitment: MinimumCommitment,
        *,
        org_id: str,
        account_id: str,
        rating_run_id: str,
        currency: Currency,
    ) -> RatedCharge | None:
        """The shortfall charge, or `None` when the floor was met.

        `None` rather than a zero charge: a met commitment produced no charge, and a zero line on
        an invoice invites the question "why am I being billed nothing for this?"."""
        consumed = self.consumed(charges, commitment, currency)
        shortfall = commitment.shortfall(consumed)
        if shortfall.is_zero:
            return None
        charge_id = derive_charge_id(
            rating_run_id, commitment.code, f"{commitment.code}@0", ChargeKind.MINIMUM_COMMITMENT
        )
        return RatedCharge(
            id=charge_id,
            org_id=org_id,
            account_id=account_id,
            kind=ChargeKind.MINIMUM_COMMITMENT,
            description=commitment.description or f"Minimum commitment {commitment.code}",
            currency=currency,
            quantity=0,
            unit="",
            list_amount=shortfall,
            gross_amount=shortfall,
            discount_amount=Money.zero(currency),
            net_amount=shortfall,
            components=(
                ChargeComponent(
                    kind=ComponentKind.COMMITMENT_SHORTFALL,
                    label="Commitment shortfall",
                    amount=shortfall,
                    detail=f"committed {commitment.amount}, consumed {consumed}",
                ),
            ),
            provenance=ChargeProvenance(
                price_code=commitment.code,
                price_version=0,
                item_id=commitment.code,
                subscription_id=commitment.subscription_id,
            ),
            interval=commitment.interval,
        )


# ── the rating engine ───────────────────────────────────────────────────────────


class RatingEngine:
    """The pure correctness core: immutable inputs → charges.

    `rate` reads no clock, opens no transaction, and consults nothing outside its arguments.
    Given equal arguments it returns an equal result — including equal charge ids, because those
    are derived from the run id and the run id is derived from the basis digest. That is what
    makes "replay produces byte-identical rating results" a property two `==` comparisons can
    check rather than a claim a reviewer has to accept."""

    def __init__(
        self,
        assembler: ChargeAssembler | None = None,
        commitments: CommitmentService | None = None,
    ) -> None:
        self._assembler = assembler or ChargeAssembler()
        self._commitments = commitments or CommitmentService()

    def rate(
        self,
        basis: RatingBasis,
        requests: Sequence[ChargeRequest],
        *,
        rating_run_id: str,
        commitments: Sequence[MinimumCommitment] = (),
        mode: RatingMode = RatingMode.AUTHORITATIVE,
        at: float = 0.0,
    ) -> RatingResult:
        charges = [
            self._assembler.assemble(
                request,
                org_id=basis.org_id,
                account_id=basis.account_id,
                rating_run_id=rating_run_id,
            )
            for request in self._ordered(requests)
        ]
        # Provenance carries the agreement it came from. Set here rather than in the assembler,
        # because the assembler is given one item and does not know which agreement it belongs to.
        charges = [
            replace(
                charge,
                provenance=replace(charge.provenance, subscription_id=basis.subscription_id),
            )
            for charge in charges
        ]
        for commitment in sorted(commitments, key=lambda c: c.code):
            true_up = self._commitments.true_up(
                charges,
                commitment,
                org_id=basis.org_id,
                account_id=basis.account_id,
                rating_run_id=rating_run_id,
                currency=basis.currency,
            )
            if true_up is not None:
                charges.append(
                    replace(
                        true_up,
                        provenance=replace(
                            true_up.provenance, subscription_id=basis.subscription_id
                        ),
                    )
                )
        return RatingResult(
            org_id=basis.org_id,
            account_id=basis.account_id,
            subscription_id=basis.subscription_id,
            period=basis.period,
            currency=basis.currency,
            basis=basis,
            charges=tuple(charges),
            mode=mode,
            at=at,
        )

    @staticmethod
    def _ordered(requests: Sequence[ChargeRequest]) -> tuple[ChargeRequest, ...]:
        """A deterministic charge order, independent of how the caller assembled the list.

        Sorted by item, then price code, then shape — so two callers that build the same set of
        requests in different orders produce byte-identical results, which is the property a
        multi-instance billing run depends on."""
        return tuple(
            sorted(requests, key=lambda r: (r.item_id, r.price.price_code, r.price.shape.value))
        )


# ── charge request construction ─────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ChargeRequestSet:
    """The requests an agreement produced, plus the items nothing priced.

    `unpriced` is returned rather than swallowed. An item with no resolvable price is a real
    commercial fact — a plan referencing a price the pinned book does not carry — and rating that
    silently produced no charge would under-bill without anyone noticing. It becomes an
    `UNPRICED_SUBSCRIPTION_ITEM` diagnostic, and finalization refuses a run that has one."""

    requests: tuple[ChargeRequest, ...] = ()
    unpriced: tuple[str, ...] = ()

    @property
    def is_complete(self) -> bool:
        return not self.unpriced


class ChargeRequestBuilder:
    """Decides **what quantity** each subscribed item is rated on.

    A rule, and therefore here rather than in an orchestrator: seats come from the agreement (what
    was bought), metered items come from usage (what was consumed), and flat items carry no
    quantity at all. Getting that mapping wrong is how a platform bills a customer for seats they
    consumed or minutes they subscribed to, and it is exactly the kind of decision that must live
    in one readable place rather than inside whichever use case assembled the list."""

    def build(
        self,
        items: Sequence,
        *,
        prices: dict[str, ResolvedPrice],
        usage_units: dict[str, int] | None = None,
        usage_ids: dict[str, tuple[str, ...]] | None = None,
        usage_digest: str = "",
        allowances: dict[str, int] | None = None,
        entitlement_version: int = 0,
        subscription_version: int = 1,
        catalog_version: int | None = None,
        pricing_version: int | None = None,
    ) -> ChargeRequestSet:
        units = usage_units or {}
        ids = usage_ids or {}
        allowed = allowances or {}
        requests: list[ChargeRequest] = []
        unpriced: list[str] = []
        for item in items:
            price = prices.get(item.price_code)
            if price is None:
                unpriced.append(item.price_code)
                continue
            meter_code = item.meter_code or price.meter_code
            if meter_code:
                quantity = units.get(meter_code, 0)
                unit = ""
            elif item.is_seat_based:
                quantity = item.quantity
                unit = "seat"
            else:
                quantity = item.quantity
                unit = ""
            requests.append(
                ChargeRequest(
                    item_id=item.id,
                    price=price,
                    quantity=quantity,
                    unit=unit,
                    label=item.label,
                    meter_code=meter_code,
                    usage_ids=ids.get(meter_code, ()),
                    usage_digest=usage_digest if meter_code else "",
                    entitlement_allowance=allowed.get(meter_code),
                    entitlement_version=entitlement_version,
                    subscription_version=subscription_version,
                    catalog_version=catalog_version,
                    pricing_version=pricing_version,
                    price_version=item.price_version,
                )
            )
        return ChargeRequestSet(requests=tuple(requests), unpriced=tuple(sorted(set(unpriced))))


# ── basis construction ──────────────────────────────────────────────────────────


class RatingBasisBuilder:
    """Composes the immutable basis from the pinned inputs.

    Kept separate from the engine deliberately: building the basis is what decides *what* is being
    rated, and rating is what decides what it costs. Keeping them apart is what lets `RerateService`
    ask "have the inputs moved?" without rating anything."""

    def build(
        self,
        *,
        org_id: str,
        account_id: str,
        subscription_id: str,
        period: BillingPeriod,
        currency: Currency,
        catalog_version: int,
        pricing_version: int,
        subscription_version: int = 1,
        entitlement_version: int = 0,
        usage_digest: str = "",
        usage_record_count: int = 0,
        price_pins: Sequence[str] = (),
        seat_counts: Sequence[tuple[str, int]] = (),
        metered_units: Sequence[tuple[str, int]] = (),
        commitment_codes: Sequence[str] = (),
        coupon_code: str = "",
        promotion_codes: Sequence[str] = (),
        at: float = 0.0,
    ) -> RatingBasis:
        return RatingBasis(
            org_id=org_id,
            account_id=account_id,
            subscription_id=subscription_id,
            period=period,
            currency=currency,
            catalog_version=catalog_version,
            pricing_version=pricing_version,
            subscription_version=subscription_version,
            entitlement_version=entitlement_version,
            usage_digest=usage_digest,
            usage_record_count=usage_record_count,
            # Sorted, so the digest depends on *what* the inputs are and never on the order a
            # repository happened to return them in.
            price_pins=tuple(sorted(price_pins)),
            seat_counts=tuple(sorted(seat_counts)),
            metered_units=tuple(sorted(metered_units)),
            commitment_codes=tuple(sorted(commitment_codes)),
            coupon_code=coupon_code,
            promotion_codes=tuple(sorted(promotion_codes)),
            at=at,
        )


# ── rerating ────────────────────────────────────────────────────────────────────


class RerateService:
    """Decides whether a period must be rated again, and what kind of act that would be.

    The decision is a **digest comparison**, not a heuristic and not a timestamp check: the basis
    fingerprints every input, so "the inputs have not moved" is provable rather than assumed. A
    nightly job can therefore run over every open period and do nothing at all, cheaply, which is
    what stops rerating from rewriting the same figure every night."""

    def assess(
        self,
        proposed: RatingBasis,
        current: RatingRun | None,
        *,
        reason: RerateReason | None = None,
    ) -> RerateVerdict:
        if current is None:
            return RerateVerdict(
                subscription_id=proposed.subscription_id,
                period=proposed.period,
                required=True,
                proposed_basis_digest=proposed.digest,
                reason=reason,
                detail="no rating run exists for this period",
            )
        unchanged = current.basis_digest == proposed.digest
        return RerateVerdict(
            subscription_id=proposed.subscription_id,
            period=proposed.period,
            required=not unchanged,
            current_run_id=current.id,
            current_basis_digest=current.basis_digest,
            proposed_basis_digest=proposed.digest,
            current_state=current.state,
            reason=None if unchanged else reason,
            detail=(
                "inputs unchanged — rating again would produce identical output"
                if unchanged
                else "an input moved: the basis digest differs"
            ),
        )

    def links(self, current: RatingRun | None) -> tuple[str, str]:
        """`(supersedes, adjusts)` for a new run replacing `current`.

        A **finalized** predecessor is *adjusted*, never superseded: it was billed, so the new run
        records a difference from it and leaves it exactly as it was. A provisional predecessor is
        superseded. Returning both as one pair keeps the distinction in one place rather than at
        every call site that creates a rerun."""
        if current is None:
            return ("", "")
        return ("", current.id) if current.is_finalized else (current.id, "")


# ── comparison ──────────────────────────────────────────────────────────────────


class RatingComparisonService:
    """Compares two runs charge by charge, matched on `comparison_key`.

    Matched on what produced a charge rather than on its id or its position: ids embed the run so
    they never match, and positions would make one inserted charge look like every charge
    changing. The output keeps `UNCHANGED` entries, because "these four lines are identical and
    this one moved" is a more useful answer than the one line alone."""

    def compare(self, before: RatingRun, after: RatingRun, *, at: float = 0.0) -> RatingDelta:
        old_charges = before.result.charges if before.result else ()
        new_charges = after.result.charges if after.result else ()
        before_charges = {c.comparison_key: c for c in old_charges}
        after_charges = {c.comparison_key: c for c in new_charges}
        currency = after.currency
        differences: list[RatingDifference] = []
        for key in sorted(set(before_charges) | set(after_charges)):
            old = before_charges.get(key)
            new = after_charges.get(key)
            differences.append(self._difference(key, old, new, currency))
        return RatingDelta(
            org_id=after.org_id,
            account_id=after.account_id,
            subscription_id=after.subscription_id,
            period=after.period,
            before_run_id=before.id,
            after_run_id=after.id,
            currency=currency,
            differences=tuple(differences),
            total_before=before.total,
            total_after=after.total,
            at=at,
        )

    @staticmethod
    def _difference(key, old: RatedCharge | None, new: RatedCharge | None, currency: Currency):
        if old is None and new is not None:
            return RatingDifference(
                kind=DifferenceKind.ADDED,
                comparison_key=key,
                description=new.description,
                after=new.amount,
                delta=new.amount,
                after_quantity=new.quantity,
            )
        if new is None or old is None:
            # `old is None and new is None` cannot occur: the key came from one of the two sets.
            return RatingDifference(
                kind=DifferenceKind.REMOVED,
                comparison_key=key,
                description=old.description if old else "",
                before=old.amount if old else None,
                delta=-old.amount if old else None,
                before_quantity=old.quantity if old else 0,
            )
        delta = new.amount - old.amount
        if delta.is_zero:
            kind = DifferenceKind.UNCHANGED
        elif delta.is_negative:
            kind = DifferenceKind.DECREASED
        else:
            kind = DifferenceKind.INCREASED
        return RatingDifference(
            kind=kind,
            comparison_key=key,
            description=new.description,
            before=old.amount,
            after=new.amount,
            delta=delta,
            before_quantity=old.quantity,
            after_quantity=new.quantity,
            reason=(
                "quantity changed"
                if old.quantity != new.quantity
                else ("rate or reduction changed" if not delta.is_zero else "")
            ),
        )


class RatingAdjustmentService:
    """Builds the *adjustment* charges that express a delta against a finalized run.

    An adjustment is never an edit. It is a new charge, in a new run, whose amount is the
    difference — positive when more is owed, negative when less. That is the only shape in which
    a figure a customer has already been invoiced for can be revised, and it is why the finalized
    run's lifecycle has no edge out of it."""

    def charges(
        self, delta: RatingDelta, *, org_id: str, account_id: str, rating_run_id: str
    ) -> tuple[RatedCharge, ...]:
        out: list[RatedCharge] = []
        for difference in delta.material:
            amount = difference.delta or Money.zero(delta.currency)
            item_id, price_pin, _ = difference.comparison_key
            out.append(
                RatedCharge(
                    id=derive_charge_id(rating_run_id, item_id, price_pin, ChargeKind.ADJUSTMENT),
                    org_id=org_id,
                    account_id=account_id,
                    kind=ChargeKind.ADJUSTMENT,
                    description=f"Adjustment — {difference.description or item_id}",
                    currency=delta.currency,
                    list_amount=amount,
                    gross_amount=amount,
                    discount_amount=Money.zero(delta.currency),
                    net_amount=amount,
                    components=(
                        ChargeComponent(
                            kind=ComponentKind.DELTA,
                            label=difference.kind.value,
                            amount=amount,
                            detail=(
                                f"was {difference.before}, now {difference.after}"
                                f"{' — ' + difference.reason if difference.reason else ''}"
                            ),
                        ),
                    ),
                    provenance=ChargeProvenance(
                        price_code=price_pin.split("@")[0],
                        price_version=int(price_pin.split("@")[1]) if "@" in price_pin else 0,
                        item_id=item_id,
                        subscription_id=delta.subscription_id,
                    ),
                )
            )
        return tuple(out)


# ── lifecycle ───────────────────────────────────────────────────────────────────


class RatingLifecycleService:
    """Applies rating lifecycle triggers through the shared FSM. Illegal edges are rejected by
    the table, not by an `if` — which is what makes "a finalized run can never be edited" a
    property of the machine rather than of whoever wrote the last branch."""

    def __init__(self, definition: StateMachineDefinition = RATING_LIFECYCLE) -> None:
        self._definition = definition

    def can(self, current: RatingState, trigger: RatingTrigger) -> bool:
        return self._definition.get(current, trigger) is not None

    def next_state(self, current: RatingState, trigger: RatingTrigger) -> RatingState:
        edge = self._definition.get(current, trigger)
        if edge is None:
            legal = sorted(t.value for t in self._definition.triggers_from(current))
            raise InvalidBillingTransition(
                f"a rating run cannot {trigger.value} from {current.value}; legal triggers: {legal}"
            )
        return edge.target

    def allowed(self, current: RatingState) -> tuple[RatingTrigger, ...]:
        return tuple(sorted(self._definition.triggers_from(current), key=lambda t: t.value))

    def apply(
        self,
        run: RatingRun,
        trigger: RatingTrigger,
        *,
        at: float,
        actor_id: str = "",
        superseded_by: str = "",
        detail: str = "",
    ) -> RatingRun:
        target = self.next_state(run.state, trigger)
        changes: dict = {"state": target, "updated_at": at}
        if detail:
            changes["detail"] = detail
        if target is RatingState.FINALIZED:
            changes["finalized_at"] = at
            changes["finalized_by"] = actor_id
        if target is RatingState.SUPERSEDED and superseded_by:
            changes["superseded_by"] = superseded_by
        return replace(run, **changes)

    def guard_editable(self, run: RatingRun) -> None:
        """Fail fast when something would change a settled run (§A2.6).

        Belt and braces beside the FSM: the machine already refuses the transition, and this
        refuses the *intent* with a message that says why — which is what a caller trying to
        correct an invoiced figure actually needs to read."""
        if run.is_settled:
            raise BillingConflict(
                f"rating run {run.id} is {run.state.value} and can never be edited; "
                "rate the period again — the new run records the difference"
            )


# ── validation ──────────────────────────────────────────────────────────────────


class RatingValidator:
    """Structural validation. **Never raises** — it reports, and the factory decides (§A2.6).

    The two findings that matter most are the ones a reviewer would not think to look for: a
    breakdown that does not sum to its charge, and a fixed-sum discount authored against a metered
    price. The first means an explanation nobody can trust; the second is the M9.7 authoring trap
    this milestone had to work around."""

    def validate_commitment(self, commitment: MinimumCommitment) -> BillingDiagnostics:
        findings: list[Diagnostic] = []
        if not commitment.code:
            findings.append(
                _d(
                    "commitment.code",
                    BillingDiagnosticCode.MISSING_REQUIRED_FIELD,
                    "a code is required",
                )
            )
        if commitment.amount.is_negative:
            findings.append(
                _d(
                    "commitment.amount",
                    BillingDiagnosticCode.INVALID_COMMITMENT,
                    "a minimum commitment cannot be negative",
                    remediation="a negative floor is a credit, and credits are a different domain",
                )
            )
        if commitment.effective_until is not None and (
            commitment.effective_until <= commitment.effective_from
        ):
            findings.append(
                _d(
                    "commitment.effective",
                    BillingDiagnosticCode.INVALID_COMMITMENT,
                    "the commitment window ends before it begins",
                )
            )
        return BillingDiagnostics.of(findings)

    def validate_commitments(self, commitments: Sequence[MinimumCommitment]) -> BillingDiagnostics:
        findings: list[Diagnostic] = []
        seen: dict[str, int] = {}
        for commitment in commitments:
            seen[commitment.code] = seen.get(commitment.code, 0) + 1
        for code, count in sorted(seen.items()):
            if count > 1:
                findings.append(
                    _d(
                        f"commitment.{code}",
                        BillingDiagnosticCode.DUPLICATE_COMMITMENT_CODE,
                        f"{count} commitments share the code {code!r}",
                        remediation="two floors for one code would be applied twice",
                    )
                )
        return BillingDiagnostics.of(findings)

    def validate_requests(
        self, requests: Sequence[ChargeRequest], currency: Currency
    ) -> BillingDiagnostics:
        findings: list[Diagnostic] = []
        for request in requests:
            path = f"charge.{request.item_id or request.price.price_code}"
            if request.price.currency is not currency:
                findings.append(
                    _d(
                        path,
                        BillingDiagnosticCode.RATING_CURRENCY_MISMATCH,
                        f"price is in {request.price.currency.value}, "
                        f"the agreement bills in {currency.value}",
                    )
                )
            if request.quantity < 0:
                findings.append(
                    _d(path, BillingDiagnosticCode.INVALID_QUANTITY, "quantity cannot be negative")
                )
            shape = request.price.shape
            kind = SHAPE_TO_CHARGE_KIND.get(shape, ChargeKind.USAGE)
            if shape is PriceShape.ALLOWANCE_OVERAGE:
                kind = ChargeKind.OVERAGE
            fixed = [d for d in request.price.discounts if d.kind in FIXED_SUM_DISCOUNT_KINDS]
            if fixed and kind in EXTENDED_CHARGE_KINDS:
                findings.append(
                    Diagnostic(
                        path,
                        BillingDiagnosticCode.FIXED_DISCOUNT_ON_METERED_RATE,
                        Severity.WARNING,
                        f"{len(fixed)} fixed-sum reduction(s) are folded into a per-unit rate "
                        f"on {request.price.price_code}",
                        remediation=(
                            "rating applies them once at charge level rather than per unit; "
                            "consider authoring a percentage discount instead"
                        ),
                    )
                )
            if request.price.is_metered and not request.usage_ids and request.quantity > 0:
                findings.append(
                    Diagnostic(
                        path,
                        BillingDiagnosticCode.MISSING_CHARGE_PROVENANCE,
                        Severity.WARNING,
                        "a metered charge carries a quantity but names no usage records",
                        remediation="an amount nobody can trace to usage cannot be defended",
                    )
                )
        return BillingDiagnostics.of(findings)

    def validate_result(self, result: RatingResult) -> BillingDiagnostics:
        findings: list[Diagnostic] = []
        for charge in result.charges:
            path = f"charge.{charge.id}"
            if not charge.breaks_down_correctly:
                findings.append(
                    _d(
                        path,
                        BillingDiagnosticCode.CHARGE_BREAKDOWN_MISMATCH,
                        f"the breakdown sums to {charge.component_total} "
                        f"but the charge is {charge.amount}",
                        remediation="a breakdown that does not add up will be believed anyway",
                    )
                )
            if charge.currency is not result.currency:
                findings.append(
                    _d(
                        path,
                        BillingDiagnosticCode.RATING_CURRENCY_MISMATCH,
                        f"charge is in {charge.currency.value}, "
                        f"the run bills in {result.currency.value}",
                    )
                )
            if charge.amount.is_negative and charge.kind is not ChargeKind.ADJUSTMENT:
                findings.append(
                    _d(
                        path,
                        BillingDiagnosticCode.NEGATIVE_CHARGE,
                        f"{charge.kind.value} charge is negative ({charge.amount})",
                        remediation="only an adjustment may be negative; anything else is a credit",
                    )
                )
            if not charge.provenance.price_code:
                findings.append(
                    _d(
                        path,
                        BillingDiagnosticCode.MISSING_CHARGE_PROVENANCE,
                        "the charge names no price",
                    )
                )
        return BillingDiagnostics.of(findings)

    def validate_run(self, run: RatingRun) -> BillingDiagnostics:
        findings: list[Diagnostic] = []
        if run.result is None:
            findings.append(
                _d(
                    "run.result",
                    BillingDiagnosticCode.MISSING_REQUIRED_FIELD,
                    "the run has no result",
                )
            )
            return BillingDiagnostics.of(findings)
        if run.result.basis.digest != run.basis_digest:
            findings.append(
                _d(
                    "run.basis_digest",
                    BillingDiagnosticCode.RATING_BASIS_MISMATCH,
                    "the stored basis digest does not match the result's basis",
                    remediation="the run and what it rated have diverged; rate the period again",
                )
            )
        if run.result.is_preview:
            findings.append(
                _d(
                    "run.mode",
                    BillingDiagnosticCode.PREVIEW_NOT_PERSISTABLE,
                    "a preview result cannot be stored as a rating run",
                )
            )
        return BillingDiagnostics.of([*findings, *self.validate_result(run.result).findings])


# ── inspection, explanation, search ─────────────────────────────────────────────


class RatingInspector:
    """A structural read of a run: does every charge add up, and is every amount attributable?

    Used by the diagnostics surface and by finalization — a run that cannot explain its own
    arithmetic must not become the figure on an invoice, and `is_sound` is how finalization asks."""

    def inspect(self, run: RatingRun) -> RatingInspection:
        charges = run.result.charges if run.result else ()
        inspections = tuple(self._charge(c) for c in charges)
        notes: list[str] = []
        if run.result is None:
            notes.append("the run carries no result")
        elif run.result.basis.digest != run.basis_digest:
            notes.append("the basis digest does not match the result's basis")
        if run.is_rerate:
            notes.append(
                f"rerate of {run.adjusts or run.supersedes}"
                f"{' (adjusting a finalized run)' if run.adjusts else ''}"
            )
        return RatingInspection(
            rating_run_id=run.id,
            subscription_id=run.subscription_id,
            period=run.period,
            state=run.state,
            total=run.total,
            charges=inspections,
            basis_digest=run.basis_digest,
            result_digest=run.result_digest,
            notes=tuple(notes),
        )

    @staticmethod
    def _charge(charge: RatedCharge) -> ChargeInspection:
        notes: list[str] = []
        if not charge.breaks_down_correctly:
            notes.append(f"breakdown sums to {charge.component_total}, charge is {charge.amount}")
        traced = not charge.provenance.is_metered or bool(charge.provenance.usage_ids)
        if not traced:
            notes.append("a metered charge with no usage records named")
        return ChargeInspection(
            charge_id=charge.id,
            kind=charge.kind,
            amount=charge.amount,
            breaks_down_correctly=charge.breaks_down_correctly,
            has_provenance=bool(charge.provenance.price_code),
            has_usage_attribution=traced,
            component_count=len(charge.components),
            discount_count=len(charge.discounts),
            notes=tuple(notes),
        )


class RatingExplainer:
    """Turns a run into sentences, generated from its own arithmetic.

    Never from a separate store of prose. An explanation that could drift from the amount it
    explains is worse than no explanation, because it is the one a support agent will read out."""

    def explain_charge(self, charge: RatedCharge) -> ChargeExplanation:
        lines = [self._component_line(c) for c in charge.components]
        lines.append(f"Total: {charge.amount}")
        provenance = charge.provenance
        provenance_lines = [
            f"Price {provenance.price_pin} from book {provenance.book_pin}",
        ]
        if provenance.catalog_version is not None:
            provenance_lines.append(f"Catalog version {provenance.catalog_version}")
        if provenance.pricing_version is not None:
            provenance_lines.append(f"Pricing version {provenance.pricing_version}")
        if provenance.subscription_id:
            provenance_lines.append(
                f"Agreement {provenance.subscription_id} v{provenance.subscription_version}"
            )
        if provenance.is_metered:
            provenance_lines.append(
                f"Meter {provenance.meter_pin or provenance.meter_code} — "
                f"{provenance.usage_record_count} usage record(s)"
            )
        if provenance.included_units_source is not IncludedUnitsSource.NONE:
            provenance_lines.append(
                f"Included units declared by the {provenance.included_units_source.value}"
            )
        if provenance.override_applied:
            provenance_lines.append("A negotiated price override was applied")
        if provenance.resolution_path:
            provenance_lines.append("Resolved via " + " → ".join(provenance.resolution_path))
        return ChargeExplanation(
            charge_id=charge.id,
            kind=charge.kind,
            description=charge.description,
            amount=charge.amount,
            lines=tuple(lines),
            provenance_lines=tuple(provenance_lines),
        )

    def explain(self, run: RatingRun) -> RatingExplanation:
        result = run.result
        charges = tuple(self.explain_charge(c) for c in (result.charges if result else ()))
        basis_lines: list[str] = []
        if result is not None:
            basis = result.basis
            basis_lines = [
                f"Period {basis.period.start}–{basis.period.end}",
                f"Catalog version {basis.catalog_version}, pricing version {basis.pricing_version}",
                f"Agreement version {basis.subscription_version}, "
                f"entitlement snapshot version {basis.entitlement_version}",
                f"{basis.usage_record_count} usage record(s), digest {basis.usage_digest[:16]}",
                f"Basis digest {basis.digest[:16]}",
            ]
            if run.is_rerate:
                basis_lines.append(
                    f"This run {'adjusts' if run.adjusts else 'supersedes'} "
                    f"{run.adjusts or run.supersedes}"
                    f"{' — reason: ' + run.reason.value if run.reason else ''}"
                )
        return RatingExplanation(
            rating_run_id=run.id,
            subscription_id=run.subscription_id,
            period=run.period,
            currency=run.currency,
            total=run.total,
            charges=charges,
            basis_lines=tuple(basis_lines),
        )

    @staticmethod
    def _component_line(component: ChargeComponent) -> str:
        detail = f" — {component.detail}" if component.detail else ""
        return f"{component.label}: {component.amount}{detail}"


class RatingSearchService:
    """A deterministic filter over runs. No index, no scoring — the same runs and the same query
    always come back in the same order, which is what a billing screen and a reproducible test
    both need."""

    def search(self, runs: Sequence[RatingRun], query: RatingSearchQuery) -> RatingSearchResult:
        matches = [r for r in runs if self._matches(r, query)]
        ordered = sorted(matches, key=lambda r: (-r.rated_at, r.id))
        window = ordered[query.offset : query.offset + max(query.limit, 0)]
        return RatingSearchResult(runs=tuple(window), total=len(ordered))

    def _matches(self, run: RatingRun, query: RatingSearchQuery) -> bool:
        if query.account_id and run.account_id != query.account_id:
            return False
        if query.subscription_id and run.subscription_id != query.subscription_id:
            return False
        if query.states and run.state not in query.states:
            return False
        if query.reasons and run.reason not in query.reasons:
            return False
        if query.rerates_only and not run.is_rerate:
            return False
        if query.period_start is not None and run.period.start < query.period_start:
            return False
        if query.period_end is not None and run.period.end > query.period_end:
            return False
        if query.min_total_minor is not None and run.total.minor < query.min_total_minor:
            return False
        if query.max_total_minor is not None and run.total.minor > query.max_total_minor:
            return False
        if query.kinds:
            kinds = set(run.result.kinds) if run.result else set()
            if not kinds & set(query.kinds):
                return False
        return not (
            query.text
            and not self._text_matches(
                query.text, run.id, run.subscription_id, run.account_id, run.detail
            )
        )

    @staticmethod
    def _text_matches(text: str, *fields: str) -> bool:
        needle = text.lower()
        return any(needle in (f or "").lower() for f in fields)


class RatingHistoryService:
    """Rating's own ordered history. Newest first, deterministic on ties."""

    def timeline(self, entries: Sequence, *, limit: int = 100) -> tuple:
        ordered = sorted(entries, key=lambda a: (-a.at, a.id))
        return tuple(ordered[: max(limit, 0)])

    def for_run(self, entries: Sequence, rating_run_id: str) -> tuple:
        return tuple(
            sorted(
                (a for a in entries if a.rating_run_id == rating_run_id),
                key=lambda a: (a.at, a.id),
            )
        )


# ── diagnostics helpers ─────────────────────────────────────────────────────────


def _d(
    path: str, code: BillingDiagnosticCode, message: str, *, remediation: str = ""
) -> Diagnostic:
    return Diagnostic(path, code, Severity.ERROR, message, remediation)


__all__ = [
    "ChargeAssembler",
    "ChargeCalculator",
    "ChargeRequest",
    "ChargeRequestBuilder",
    "ChargeRequestSet",
    "CommitmentService",
    "DiscountRebasingPolicy",
    "IncludedQuantityResolver",
    "RatingAdjustmentService",
    "RatingBasisBuilder",
    "RatingComparisonService",
    "RatingEngine",
    "RatingExplainer",
    "RatingHistoryService",
    "RatingInspector",
    "RatingLifecycleService",
    "RatingSearchService",
    "RatingValidator",
    "RebasedRate",
    "RerateService",
]
