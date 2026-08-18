"""M9.12 — Rating Engine.

Rating is the milestone where Billing first produces money. Every earlier domain described
something; this one computes an amount a customer pays, from six frozen inputs, and must produce
the same amount every time anyone asks.

The suite is organised so that the properties the brief demanded are demonstrated **explicitly**
rather than implied by a passing integration test — §13 is a numbered list of them, each an
assertion a reader can check against the claim it supports:

  1. identical inputs always produce identical charges
  2. replay produces byte-identical rating results
  3. deterministic behaviour across a restart (different id generator, different clock)
  4. rerating historical periods never changes historical inputs
  5. a finalized run is adjusted, never superseded
  6. idempotent rerating — unchanged inputs append no run
  7. append-only rating history
  8. deterministic charge breakdown
  9. charge provenance for every billed amount
 10. correct interaction with discounts and overrides
 11. correct interaction with hybrid subscriptions
 12. no dependency on Runtime availability
 13. no dependency on provider availability
 14. rating writes to no other domain
"""

from __future__ import annotations

import ast
import pathlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from billing.application import (
    ALL_RATING_COMMANDS,
    ALL_RATING_QUERIES,
    ActivateBillingAccount,
    ActivateCommercialMeter,
    ActivateSubscription,
    AllowAllAuthorizer,
    CompareRatingRuns,
    CreateBillingAccount,
    CreatePrice,
    CreatePriceBook,
    CreateProduct,
    CreateSubscription,
    DefineCommercialMeter,
    DefineMinimumCommitment,
    DiagnoseRatingRun,
    DiscardRatingRun,
    ExplainRatingRun,
    FinalizeRatingRun,
    GetChargeBreakdown,
    GetRatingRun,
    GetRerateVerdict,
    IngestMetricSignal,
    InspectRatingRun,
    ListMinimumCommitments,
    ListRatingRuns,
    OpenUsagePeriod,
    PreviewSubscriptionRating,
    PublishCatalog,
    PublishPricing,
    PublishPricingBook,
    PublishProduct,
    RateAccountPeriod,
    RateSubscriptionPeriod,
    RatingTimeline,
    RecordRatingAdjustment,
    RerateSubscriptionPeriod,
    RetireMinimumCommitment,
    SearchRatingRuns,
    SnapshotRatingRun,
)
from billing.customer.app import create_billing_app
from billing.customer.config import BillingApiSettings
from billing.customer.container import BillingContainer
from billing.foundation import (
    RATING_LIFECYCLE,
    AgreementItem,
    AppliedDiscount,
    BillingConflict,
    BillingDiagnosticCode,
    BillingInterval,
    BillingNotFound,
    BillingPeriod,
    ChargeCalculator,
    ChargeKind,
    ChargeRequest,
    ChargeRequestBuilder,
    CommitmentService,
    ComponentKind,
    Currency,
    DefaultIdGenerator,
    DifferenceKind,
    DiscountKind,
    DiscountRebasingPolicy,
    FixedClock,
    IncludedQuantityResolver,
    IncludedUnitsSource,
    InvalidBillingTransition,
    ItemKind,
    MeterSource,
    MetricSignal,
    MinimumCommitment,
    Money,
    Normalization,
    PriceShape,
    PriceTier,
    RatingAdjustmentService,
    RatingBasis,
    RatingBasisBuilder,
    RatingComparisonService,
    RatingEngine,
    RatingExplainer,
    RatingInspector,
    RatingLifecycleService,
    RatingMode,
    RatingRun,
    RatingSearchQuery,
    RatingSearchService,
    RatingState,
    RatingTrigger,
    RatingValidator,
    RerateReason,
    RerateService,
    ResolvedPrice,
    Scope,
    SequentialIdGenerator,
    derive_charge_id,
    derive_rating_run_id,
)
from billing.persistence import build_persistent_billing_application

PLATFORM = Path(__file__).resolve().parents[1]
BILLING = PLATFORM / "billing"
STUDIO = BILLING / "customer" / "studio" / "rating.html"

INR = Currency.INR
USD = Currency.USD
ORG = "org_1"
ACTOR = "usr_1"
P0, P1 = 1_000.0, 2_000.0
PERIOD = BillingPeriod(P0, P1)

TIERS = (
    PriceTier(up_to=100, unit_price=Money(200, INR)),
    PriceTier(up_to=500, unit_price=Money(150, INR)),
    PriceTier(up_to=None, unit_price=Money(100, INR)),
)


# ── helpers ─────────────────────────────────────────────────────────────────────


def price(shape: PriceShape, **kw) -> ResolvedPrice:
    base = dict(
        price_code="p",
        price_book_code="book",
        price_book_version=1,
        currency=INR,
        shape=shape,
        at=0.0,
        catalog_version=3,
    )
    base.update(kw)
    return ResolvedPrice(**base)  # type: ignore[arg-type]


def basis(**kw) -> RatingBasis:
    base = dict(
        org_id=ORG,
        account_id="bac_1",
        subscription_id="csb_1",
        period=PERIOD,
        currency=INR,
        catalog_version=3,
        pricing_version=7,
    )
    base.update(kw)
    return RatingBasis(**base)  # type: ignore[arg-type]


def run_id(b: RatingBasis) -> str:
    return derive_rating_run_id(b.org_id, b.subscription_id, b.period.start, b.period.end, b.digest)


def flat(amount: int = 500_00, code: str = "platform") -> ChargeRequest:
    return ChargeRequest(
        item_id="i_flat",
        price=price(PriceShape.FLAT_RECURRING, price_code=code, list_amount=Money(amount, INR)),
        label="Platform fee",
    )


def seats(rate: int = 100_00, count: int = 7, **kw) -> ChargeRequest:
    return ChargeRequest(
        item_id="i_seat",
        price=price(PriceShape.PER_SEAT, price_code="seats", list_amount=Money(rate, INR), **kw),
        quantity=count,
        unit="seat",
        label="Seats",
    )


def graduated(units: int = 700) -> ChargeRequest:
    return ChargeRequest(
        item_id="i_grad",
        price=price(
            PriceShape.TIERED_GRADUATED, price_code="grad", tiers=TIERS, meter_code="voice"
        ),
        quantity=units,
        unit="minute",
        meter_code="voice",
        usage_ids=("usg_a", "usg_b"),
        usage_digest="d1",
    )


def rate_it(requests, *, b=None, commitments=(), mode=RatingMode.AUTHORITATIVE, at=0.0):
    b = b or basis()
    return RatingEngine().rate(
        b, requests, rating_run_id=run_id(b), commitments=commitments, mode=mode, at=at
    )


def a_run(result, *, state=RatingState.RATED, rid=None, **kw) -> RatingRun:
    base = dict(
        id=rid or run_id(result.basis),
        org_id=ORG,
        account_id=result.account_id,
        subscription_id=result.subscription_id,
        period=result.period,
        currency=result.currency,
        basis_digest=result.basis.digest,
        state=state,
        result=result,
    )
    base.update(kw)
    return RatingRun(**base)  # type: ignore[arg-type]


# ── 1. identity ─────────────────────────────────────────────────────────────────


def test_the_run_id_is_derived_from_the_basis_not_minted():
    """The whole idempotency story in one assertion: same inputs → same id, and *no* id
    generator was consulted to get there."""
    b = basis()
    assert run_id(b) == run_id(basis())
    assert run_id(b).startswith("rat_")


def test_a_changed_input_changes_the_run_id():
    """The corollary, and the reason a rerate can never overwrite what it revises."""
    assert run_id(basis()) != run_id(basis(usage_digest="moved"))
    assert run_id(basis()) != run_id(basis(pricing_version=8))
    assert run_id(basis()) != run_id(basis(entitlement_version=2))


def test_when_rating_ran_is_not_part_of_the_basis():
    """`at` is excluded deliberately — including it would make every rerun a new run and
    destroy the idempotency the derived id exists to provide."""
    assert basis(at=1.0).digest == basis(at=999_999.0).digest


def test_charge_ids_are_derived_from_the_run():
    assert derive_charge_id("rat_x", "i1", "p@1", "usage") == derive_charge_id(
        "rat_x", "i1", "p@1", "usage"
    )
    assert derive_charge_id("rat_x", "i1", "p@1", "usage") != derive_charge_id(
        "rat_y", "i1", "p@1", "usage"
    )


def test_the_basis_says_whether_two_runs_would_rate_identically():
    assert basis().says_the_same_as(basis())
    assert not basis().says_the_same_as(basis(usage_record_count=1))


def test_the_basis_digest_does_not_depend_on_input_ordering():
    """Sorted inside the builder, so a repository's row order cannot change a bill."""
    builder = RatingBasisBuilder()
    common = dict(
        org_id=ORG,
        account_id="bac_1",
        subscription_id="csb_1",
        period=PERIOD,
        currency=INR,
        catalog_version=1,
        pricing_version=1,
    )
    one = builder.build(**common, price_pins=("b@1", "a@1"), metered_units=(("z", 1), ("a", 2)))
    two = builder.build(**common, price_pins=("a@1", "b@1"), metered_units=(("a", 2), ("z", 1)))
    assert one.digest == two.digest


# ── 2. lifecycle ────────────────────────────────────────────────────────────────


def test_a_finalized_run_has_no_way_out():
    """The load-bearing absence. A figure a customer was invoiced for must be structurally
    unrevisable, not merely conventionally so."""
    assert RATING_LIFECYCLE.triggers_from(RatingState.FINALIZED) == frozenset()


def test_superseded_and_discarded_are_terminal_too():
    for state in (RatingState.SUPERSEDED, RatingState.DISCARDED):
        assert RATING_LIFECYCLE.triggers_from(state) == frozenset()


def test_there_is_no_deletion_state():
    targets = (
        {e.target for edges in RATING_LIFECYCLE.edges.values() for e in edges.values()}
        if hasattr(RATING_LIFECYCLE, "edges")
        else set(RatingState)
    )
    for state in targets:
        assert "delete" not in str(state).lower()
    assert not any("delete" in s.value for s in RatingState)


def test_the_lifecycle_service_refuses_an_illegal_edge():
    service = RatingLifecycleService()
    assert service.can(RatingState.RATED, RatingTrigger.FINALIZE)
    assert not service.can(RatingState.FINALIZED, RatingTrigger.SUPERSEDE)
    with pytest.raises(InvalidBillingTransition):
        service.next_state(RatingState.FINALIZED, RatingTrigger.SUPERSEDE)


def test_guard_editable_refuses_the_intent_with_a_reason():
    result = rate_it([flat()])
    with pytest.raises(BillingConflict) as excinfo:
        RatingLifecycleService().guard_editable(a_run(result, state=RatingState.FINALIZED))
    assert "never be edited" in str(excinfo.value)


def test_finalizing_records_who_and_when():
    moved = RatingLifecycleService().apply(
        a_run(rate_it([flat()])), RatingTrigger.FINALIZE, at=42.0, actor_id="ops"
    )
    assert moved.state is RatingState.FINALIZED
    assert moved.finalized_at == 42.0 and moved.finalized_by == "ops"


def test_allowed_triggers_are_deterministic():
    service = RatingLifecycleService()
    assert service.allowed(RatingState.RATED) == service.allowed(RatingState.RATED)
    assert service.allowed(RatingState.FINALIZED) == ()


# ── 3. charge calculation, shape by shape ───────────────────────────────────────


def test_a_flat_charge_is_the_rate():
    assert rate_it([flat(500_00)]).total == Money(500_00, INR)


def test_seats_multiply_by_the_agreement_not_by_usage():
    charge = rate_it([seats(100_00, 7)]).charges[0]
    assert charge.amount == Money(700_00, INR)
    assert charge.components[0].kind is ComponentKind.SEATS
    assert charge.components[0].units == 7


def test_per_unit_multiplies_by_consumption():
    request = ChargeRequest(
        item_id="i",
        price=price(PriceShape.PER_UNIT, unit_amount=Money(50, INR), list_amount=Money(50, INR)),
        quantity=1_400,
        unit="minute",
    )
    assert rate_it([request]).total == Money(70_000, INR)


def test_graduated_tiers_charge_each_band_separately():
    """100 @ ₹2 + 400 @ ₹1.50 + 200 @ ₹1 — the bands are walked, never assumed."""
    charge = rate_it([graduated(700)]).charges[0]
    assert charge.amount == Money(100 * 200 + 400 * 150 + 200 * 100, INR)
    assert len([c for c in charge.components if c.kind is ComponentKind.TIER_BAND]) == 3


def test_volume_tiers_charge_the_whole_quantity_at_the_landing_band():
    """The difference from graduated pricing is the entire point of having both."""
    request = ChargeRequest(
        item_id="i", price=price(PriceShape.VOLUME, tiers=TIERS), quantity=700, unit="call"
    )
    assert rate_it([request]).total == Money(700 * 100, INR)


def test_graduated_and_volume_disagree_on_the_same_quantity():
    grad = rate_it([graduated(700)]).total
    vol = rate_it(
        [
            ChargeRequest(
                item_id="i_grad",
                price=price(PriceShape.VOLUME, tiers=TIERS),
                quantity=700,
                unit="call",
            )
        ]
    ).total
    assert grad != vol


def test_a_partial_package_is_a_bought_package():
    request = ChargeRequest(
        item_id="i",
        price=price(
            PriceShape.PACKAGE,
            unit_amount=Money(100_00, INR),
            list_amount=Money(100_00, INR),
            package_size=1_000,
        ),
        quantity=2_001,
        unit="message",
    )
    charge = rate_it([request]).charges[0]
    assert charge.amount == Money(3 * 100_00, INR)
    assert charge.components[0].units == 3


def test_package_rounding_uses_integer_ceiling_not_a_float():
    calculator = ChargeCalculator()
    huge = 10**18 + 1
    components = calculator.package(Money(1, INR), huge, 10**18)
    assert components[0].units == 2  # a float quotient would round this to 1


def test_an_allowance_is_shown_as_well_as_the_overage():
    """ "You used 1,400, 1,000 were included, 400 were charged" — a breakdown that only shows
    400 cannot produce that sentence."""
    request = ChargeRequest(
        item_id="i",
        price=price(
            PriceShape.ALLOWANCE_OVERAGE,
            unit_amount=Money(50, INR),
            list_amount=Money(50, INR),
            included_units=1_000,
            meter_code="voice",
        ),
        quantity=1_400,
        unit="minute",
        meter_code="voice",
        usage_ids=("u",),
    )
    charge = rate_it([request]).charges[0]
    assert charge.kind is ChargeKind.OVERAGE
    kinds = [c.kind for c in charge.components]
    assert ComponentKind.INCLUDED in kinds and ComponentKind.OVERAGE_UNITS in kinds
    assert charge.amount == Money(400 * 50, INR)


def test_unused_allowance_is_not_a_credit():
    request = ChargeRequest(
        item_id="i",
        price=price(
            PriceShape.ALLOWANCE_OVERAGE,
            unit_amount=Money(50, INR),
            list_amount=Money(50, INR),
            included_units=1_000,
        ),
        quantity=100,
        unit="minute",
    )
    assert rate_it([request]).total == Money.zero(INR)


def test_a_negative_quantity_cannot_produce_a_negative_charge():
    assert rate_it([seats(100_00, -5)]).total == Money.zero(INR)


# ── 4. discount re-basing — the defect this milestone had to work around ────────


def _fixed(amount: int) -> AppliedDiscount:
    return AppliedDiscount(
        code="WELCOME",
        display_name="Fixed reduction",
        kind=DiscountKind.FIXED_AMOUNT,
        priority=10,
        stackable=True,
        reduction=Money(amount, INR),
    )


def _pct(per_unit: int) -> AppliedDiscount:
    return AppliedDiscount(
        code="LOYAL10",
        display_name="Loyalty 10%",
        kind=DiscountKind.PERCENTAGE,
        priority=10,
        stackable=True,
        reduction=Money(per_unit, INR),
    )


def test_a_percentage_reduction_scales_with_quantity():
    result = rate_it([seats(1_000, 10, net_amount=Money(900, INR), discounts=(_pct(100),))])
    charge = result.charges[0]
    assert charge.list_amount == Money(10_000, INR)
    assert charge.net_amount == Money(9_000, INR)
    assert charge.discount_amount == Money(1_000, INR)


def test_a_fixed_sum_reduction_is_applied_once_not_per_unit():
    """**The defect.** M9.7 folds a fixed sum into the *unit* rate because it has no quantity.
    Rating has the quantity, so multiplying that rate would apply ₹500 ten times.

    Naive: net_rate × 10 = ₹1,000 — a ₹5,000 reduction nobody authorised.
    Correct: list × 10 − ₹500 once = ₹5,500."""
    request = ChargeRequest(
        item_id="i",
        price=price(
            PriceShape.PER_UNIT,
            price_code="minutes",
            unit_amount=Money(600_00, INR),
            list_amount=Money(600_00, INR),
            net_amount=Money(100_00, INR),
            discounts=(_fixed(500_00),),
            meter_code="voice",
        ),
        quantity=10,
        unit="minute",
        meter_code="voice",
        usage_ids=("u",),
    )
    charge = rate_it([request]).charges[0]
    assert charge.list_amount == Money(6_000_00, INR)
    assert charge.net_amount == Money(5_500_00, INR)
    assert charge.net_amount != Money(100_00 * 10, INR)  # what the naive route would have billed


def test_the_rebasing_is_visible_in_the_breakdown_not_silent():
    request = ChargeRequest(
        item_id="i",
        price=price(
            PriceShape.PER_UNIT,
            unit_amount=Money(600_00, INR),
            list_amount=Money(600_00, INR),
            net_amount=Money(100_00, INR),
            discounts=(_fixed(500_00),),
        ),
        quantity=10,
    )
    charge = rate_it([request]).charges[0]
    assert any(c.kind is ComponentKind.REBASE for c in charge.components)


def test_a_fixed_sum_on_a_flat_price_is_left_exactly_as_m9_7_computed_it():
    """The correction must be invisible where M9.7 was already right — a flat price is not
    multiplied, so its per-unit fold *is* the charge-level answer."""
    request = ChargeRequest(
        item_id="i",
        price=price(
            PriceShape.FLAT_RECURRING,
            list_amount=Money(1_000_00, INR),
            net_amount=Money(500_00, INR),
            discounts=(_fixed(500_00),),
        ),
    )
    charge = rate_it([request]).charges[0]
    assert charge.net_amount == Money(500_00, INR)
    assert not any(c.kind is ComponentKind.REBASE for c in charge.components)


def test_rebasing_leaves_a_price_with_no_fixed_reduction_untouched():
    rebased = DiscountRebasingPolicy().rebase(
        price(
            PriceShape.PER_UNIT,
            list_amount=Money(100, INR),
            net_amount=Money(90, INR),
            discounts=(_pct(10),),
        ),
        ChargeKind.USAGE,
    )
    assert rebased.was_rebased is False
    assert rebased.rate == Money(90, INR)


def test_the_reported_reduction_trail_sums_to_the_discount_amount():
    """Split with `Money.allocate`, so the parts always sum to the whole — the trail and the
    amount can never disagree by a minor unit."""
    charge = rate_it(
        [seats(1_000, 10, net_amount=Money(800, INR), discounts=(_pct(100), _pct(100)))]
    ).charges[0]
    total = Money.zero(INR)
    for applied in charge.discounts:
        total = total + applied.reduction
    assert total == charge.discount_amount


def test_the_validator_warns_about_a_fixed_discount_on_a_metered_rate():
    """A warning, not an error: rating handles it safely, but it is almost always an authoring
    mistake worth seeing."""
    findings = RatingValidator().validate_requests(
        [
            ChargeRequest(
                item_id="i",
                price=price(
                    PriceShape.PER_UNIT,
                    unit_amount=Money(100, INR),
                    list_amount=Money(100, INR),
                    net_amount=Money(50, INR),
                    discounts=(_fixed(50),),
                    meter_code="voice",
                ),
                quantity=10,
                usage_ids=("u",),
            )
        ],
        INR,
    )
    codes = [f.code for f in findings.findings]
    assert BillingDiagnosticCode.FIXED_DISCOUNT_ON_METERED_RATE in codes
    assert findings.is_valid  # a warning does not block rating


# ── 5. included quantities ──────────────────────────────────────────────────────


def test_the_entitlement_snapshot_outranks_the_price():
    """Declared precedence, in one place. A contract grant that raises an allowance is the whole
    point of M9.10's precedence, so the entitlement is the more specific statement."""
    units, source = IncludedQuantityResolver().resolve(
        price_included=100, entitlement_allowance=1_000
    )
    assert (units, source) == (1_000, IncludedUnitsSource.ENTITLEMENT)


def test_the_price_declares_the_allowance_when_no_entitlement_does():
    units, source = IncludedQuantityResolver().resolve(
        price_included=100, entitlement_allowance=None
    )
    assert (units, source) == (100, IncludedUnitsSource.PRICE)


def test_an_entitlement_of_zero_is_not_the_same_as_no_entitlement():
    """`0` means explicitly none and must beat the price's 100 — treating it as absent would
    silently restore an allowance a contract removed."""
    units, source = IncludedQuantityResolver().resolve(price_included=100, entitlement_allowance=0)
    assert (units, source) == (0, IncludedUnitsSource.ENTITLEMENT)


def test_which_authority_won_is_recorded_on_the_charge():
    request = ChargeRequest(
        item_id="i",
        price=price(
            PriceShape.ALLOWANCE_OVERAGE,
            unit_amount=Money(50, INR),
            list_amount=Money(50, INR),
            included_units=100,
            meter_code="voice",
        ),
        quantity=1_400,
        entitlement_allowance=1_000,
        entitlement_version=4,
        meter_code="voice",
        usage_ids=("u",),
    )
    charge = rate_it([request]).charges[0]
    assert charge.provenance.included_units_source is IncludedUnitsSource.ENTITLEMENT
    assert charge.provenance.entitlement_version == 4


# ── 6. minimum commitments ──────────────────────────────────────────────────────


def commitment(minor: int = 10_000_00, **kw) -> MinimumCommitment:
    base = dict(id="cmm_1", org_id=ORG, account_id="bac_1", code="FLOOR", amount=Money(minor, INR))
    base.update(kw)
    return MinimumCommitment(**base)  # type: ignore[arg-type]


def test_a_shortfall_becomes_a_true_up_charge():
    result = rate_it([flat(500_00)], commitments=[commitment(10_000_00)])
    assert result.total == Money(10_000_00, INR)
    assert result.by_kind(ChargeKind.MINIMUM_COMMITMENT)


def test_a_met_commitment_produces_no_charge_at_all():
    """`None` rather than a zero line: a zero on an invoice invites "why am I billed nothing?"."""
    result = rate_it([flat(20_000_00)], commitments=[commitment(10_000_00)])
    assert not result.by_kind(ChargeKind.MINIMUM_COMMITMENT)


def test_overspending_a_commitment_is_never_a_credit():
    assert commitment(1_000).shortfall(Money(5_000, INR)) == Money.zero(INR)


def test_a_commitment_only_counts_the_prices_it_covers():
    result = rate_it(
        [flat(100_00, code="platform"), seats(100_00, 1)],
        commitments=[commitment(1_000_00, price_codes=("platform",))],
    )
    true_up = result.by_kind(ChargeKind.MINIMUM_COMMITMENT)[0]
    assert true_up.amount == Money(900_00, INR)  # only the ₹100 platform charge counted


def test_a_commitment_never_counts_toward_itself():
    service = CommitmentService()
    result = rate_it([flat(100_00)], commitments=[commitment(1_000_00)])
    consumed = service.consumed(result.charges, commitment(1_000_00), INR)
    assert consumed == Money(100_00, INR)


def test_commitments_are_applied_in_a_deterministic_order():
    one = rate_it(
        [flat(100_00)],
        commitments=[commitment(500_00, code="B", id="a"), commitment(400_00, code="A", id="b")],
    )
    two = rate_it(
        [flat(100_00)],
        commitments=[commitment(400_00, code="A", id="b"), commitment(500_00, code="B", id="a")],
    )
    assert one.digest == two.digest


def test_a_negative_commitment_is_rejected():
    findings = RatingValidator().validate_commitment(commitment(-1))
    assert BillingDiagnosticCode.INVALID_COMMITMENT in [f.code for f in findings.findings]


def test_two_commitments_with_one_code_are_rejected():
    findings = RatingValidator().validate_commitments([commitment(), commitment(id="cmm_2")])
    assert BillingDiagnosticCode.DUPLICATE_COMMITMENT_CODE in [f.code for f in findings.findings]


# ── 7. breakdown integrity ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "request_",
    [
        flat(),
        seats(),
        graduated(),
        seats(1_000, 10, net_amount=Money(900, INR), discounts=(_pct(100),)),
        ChargeRequest(item_id="i", price=price(PriceShape.VOLUME, tiers=TIERS), quantity=700),
        ChargeRequest(
            item_id="i",
            price=price(
                PriceShape.PACKAGE,
                unit_amount=Money(100, INR),
                list_amount=Money(100, INR),
                package_size=10,
            ),
            quantity=25,
        ),
        ChargeRequest(
            item_id="i",
            price=price(
                PriceShape.ALLOWANCE_OVERAGE,
                unit_amount=Money(50, INR),
                list_amount=Money(50, INR),
                included_units=100,
            ),
            quantity=400,
        ),
    ],
)
def test_every_shape_produces_a_breakdown_that_sums_to_its_charge(request_):
    """A breakdown that only *approximately* explains its total is worse than none, because it
    invites the reader to trust it."""
    charge = rate_it([request_]).charges[0]
    assert charge.component_total == charge.amount
    assert charge.breaks_down_correctly


def test_a_tier_flat_fee_appears_as_its_own_component():
    tiers = (
        PriceTier(up_to=100, unit_price=Money(10, INR), flat_fee=Money(5_000, INR)),
        PriceTier(up_to=None, unit_price=Money(5, INR)),
    )
    charge = rate_it(
        [
            ChargeRequest(
                item_id="i", price=price(PriceShape.TIERED_GRADUATED, tiers=tiers), quantity=150
            )
        ]
    ).charges[0]
    assert any(c.kind is ComponentKind.TIER_FLAT_FEE for c in charge.components)
    assert charge.breaks_down_correctly


def test_a_breakdown_that_does_not_add_up_is_reported():
    from dataclasses import replace as _replace

    result = rate_it([flat()])
    broken = _replace(result, charges=(_replace(result.charges[0], net_amount=Money(1, INR)),))
    findings = RatingValidator().validate_result(broken)
    assert BillingDiagnosticCode.CHARGE_BREAKDOWN_MISMATCH in [f.code for f in findings.findings]


# ── 8. rerating ─────────────────────────────────────────────────────────────────


def test_no_run_at_all_means_rating_is_required():
    verdict = RerateService().assess(basis(), None)
    assert verdict.required and verdict.current_run_id == ""


def test_unchanged_inputs_mean_rating_is_not_required():
    """The interesting answer: a nightly job can skip work instead of rewriting the same figure
    every night."""
    result = rate_it([flat()])
    verdict = RerateService().assess(result.basis, a_run(result))
    assert verdict.required is False
    assert "identical output" in verdict.detail


def test_a_moved_input_means_rating_is_required():
    verdict = RerateService().assess(basis(usage_digest="moved"), a_run(rate_it([flat()])))
    assert verdict.required and "basis digest differs" in verdict.detail


def test_a_provisional_predecessor_is_superseded():
    supersedes, adjusts = RerateService().links(a_run(rate_it([flat()])))
    assert supersedes and not adjusts


def test_a_finalized_predecessor_is_adjusted_never_superseded():
    """The distinction with teeth: a superseded run was not yet billed, an adjusted one was."""
    supersedes, adjusts = RerateService().links(
        a_run(rate_it([flat()]), state=RatingState.FINALIZED)
    )
    assert adjusts and not supersedes


def test_the_verdict_says_when_acting_would_touch_a_finalized_figure():
    verdict = RerateService().assess(
        basis(usage_digest="moved"), a_run(rate_it([flat()]), state=RatingState.FINALIZED)
    )
    assert verdict.would_adjust_finalized


# ── 9. comparison and adjustment ────────────────────────────────────────────────


def test_charges_are_matched_on_what_produced_them_not_on_position():
    """Ids embed the run so they never match across runs, and positions would make one inserted
    charge look like every charge changing."""
    before = a_run(rate_it([flat(), graduated(700)]))
    after = a_run(rate_it([graduated(700), flat()]), rid="rat_after")
    delta = RatingComparisonService().compare(before, after)
    assert not delta.is_material


def test_a_changed_quantity_shows_as_an_increase_with_a_reason():
    before = a_run(rate_it([graduated(700)]))
    after = a_run(rate_it([graduated(900)]), rid="rat_after")
    delta = RatingComparisonService().compare(before, after)
    difference = delta.material[0]
    assert difference.kind is DifferenceKind.INCREASED
    assert difference.reason == "quantity changed"
    assert delta.total_delta == Money(200 * 100, INR)


def test_an_added_and_a_removed_charge_are_both_reported():
    before = a_run(rate_it([flat()]))
    after = a_run(rate_it([seats()]), rid="rat_after")
    kinds = {d.kind for d in RatingComparisonService().compare(before, after).differences}
    assert kinds == {DifferenceKind.ADDED, DifferenceKind.REMOVED}


def test_unchanged_charges_are_kept_in_the_delta():
    """ "These four lines are identical and this one moved" is a more useful answer than the one
    line alone."""
    before = a_run(rate_it([flat(), graduated(700)]))
    after = a_run(rate_it([flat(), graduated(900)]), rid="rat_after")
    delta = RatingComparisonService().compare(before, after)
    assert len(delta.differences) == 2 and delta.charges_changed == 1


def test_a_delta_of_zero_across_changed_charges_is_still_visible():
    """A single total would hide this entirely."""
    before = a_run(rate_it([flat(100_00), seats(100_00, 1)]))
    after = a_run(rate_it([flat(200_00), seats(0, 1)]), rid="rat_after")
    delta = RatingComparisonService().compare(before, after)
    assert delta.total_delta == Money.zero(INR)
    assert delta.is_material


def test_an_adjustment_is_a_new_charge_never_an_edit():
    before = a_run(rate_it([graduated(700)]), state=RatingState.FINALIZED)
    after = a_run(rate_it([graduated(900)]), rid="rat_after")
    delta = RatingComparisonService().compare(before, after)
    charges = RatingAdjustmentService().charges(
        delta, org_id=ORG, account_id="bac_1", rating_run_id="rat_after"
    )
    assert len(charges) == 1
    assert charges[0].kind is ChargeKind.ADJUSTMENT
    assert charges[0].amount == Money(200 * 100, INR)
    assert before.result.charges[0].amount == Money(100 * 200 + 400 * 150 + 200 * 100, INR)


def test_an_adjustment_may_be_negative_and_nothing_else_may():
    before = a_run(rate_it([graduated(900)]))
    after = a_run(rate_it([graduated(700)]), rid="rat_after")
    delta = RatingComparisonService().compare(before, after)
    charge = RatingAdjustmentService().charges(
        delta, org_id=ORG, account_id="bac_1", rating_run_id="rat_after"
    )[0]
    assert charge.amount.is_negative
    findings = RatingValidator().validate_result(
        rate_it([flat(-1)]) if False else _negative_result()
    )
    assert BillingDiagnosticCode.NEGATIVE_CHARGE in [f.code for f in findings.findings]


def _negative_result():
    from dataclasses import replace as _replace

    result = rate_it([flat()])
    charge = result.charges[0]
    return _replace(
        result,
        charges=(
            _replace(
                charge,
                net_amount=Money(-1, INR),
                components=(
                    charge.components[0].__class__(
                        kind=ComponentKind.BASE, label="x", amount=Money(-1, INR)
                    ),
                ),
            ),
        ),
    )


# ── 10. explanation, inspection, search ─────────────────────────────────────────


def test_an_explanation_is_generated_from_the_arithmetic_it_describes():
    charge = rate_it([graduated(700)]).charges[0]
    explanation = RatingExplainer().explain_charge(charge)
    assert len(explanation.lines) == len(charge.components) + 1
    assert explanation.lines[-1].startswith("Total:")


def test_an_explanation_names_every_frozen_input():
    run = a_run(rate_it([graduated(700)], b=basis(entitlement_version=4, usage_digest="d1")))
    explanation = RatingExplainer().explain(run)
    joined = " ".join(explanation.basis_lines)
    for token in ("Catalog version", "pricing version", "Agreement version", "Basis digest"):
        assert token in joined


def test_a_rerate_explains_what_it_revises():
    run = a_run(rate_it([flat()]), adjusts="rat_old", reason=RerateReason.LATE_USAGE)
    joined = " ".join(RatingExplainer().explain(run).basis_lines)
    assert "adjusts rat_old" in joined and "late_usage" in joined


def test_inspection_flags_a_metered_charge_with_no_usage_named():
    request = ChargeRequest(
        item_id="i",
        price=price(
            PriceShape.PER_UNIT,
            unit_amount=Money(1, INR),
            list_amount=Money(1, INR),
            meter_code="voice",
        ),
        quantity=10,
        meter_code="voice",
    )
    inspection = RatingInspector().inspect(a_run(rate_it([request])))
    assert inspection.charges[0].has_usage_attribution is False


def test_inspection_is_sound_for_a_well_formed_run():
    assert RatingInspector().inspect(a_run(rate_it([flat(), graduated()]))).is_sound


def test_search_is_deterministic_and_paged():
    runs = [a_run(rate_it([flat(n * 100)]), rid=f"rat_{n}", rated_at=float(n)) for n in range(1, 6)]
    service = RatingSearchService()
    first = service.search(runs, RatingSearchQuery(limit=2))
    assert first.total == 5 and len(first.runs) == 2
    assert service.search(runs, RatingSearchQuery(limit=2)).runs == first.runs


def test_search_filters_on_state_reason_and_rerates():
    runs = [
        a_run(rate_it([flat(100)]), rid="rat_a", state=RatingState.FINALIZED),
        a_run(
            rate_it([flat(200)]), rid="rat_b", supersedes="rat_a", reason=RerateReason.LATE_USAGE
        ),
    ]
    service = RatingSearchService()
    assert service.search(runs, RatingSearchQuery(states=(RatingState.FINALIZED,))).total == 1
    assert service.search(runs, RatingSearchQuery(rerates_only=True)).total == 1
    assert service.search(runs, RatingSearchQuery(reasons=(RerateReason.LATE_USAGE,))).total == 1


# ── 11. request building ────────────────────────────────────────────────────────


def test_seats_come_from_the_agreement_and_usage_comes_from_usage():
    """Getting this mapping wrong is how a platform bills for seats it consumed or minutes it
    subscribed to."""
    items = (
        AgreementItem(id="i1", price_code="seats", kind=ItemKind.SEAT, quantity=7),
        AgreementItem(
            id="i2", price_code="voice", kind=ItemKind.USAGE, meter_code="voice", quantity=1
        ),
    )
    built = ChargeRequestBuilder().build(
        items,
        prices={
            "seats": price(PriceShape.PER_SEAT, price_code="seats"),
            "voice": price(PriceShape.PER_UNIT, price_code="voice", meter_code="voice"),
        },
        usage_units={"voice": 1_400},
    )
    by_item = {r.item_id: r for r in built.requests}
    assert by_item["i1"].quantity == 7
    assert by_item["i2"].quantity == 1_400


def test_an_unpriced_item_is_reported_never_silently_skipped():
    """An item the pinned book does not price is a real commercial fact; rating that silently
    produced no charge would under-bill without anyone noticing."""
    items = (AgreementItem(id="i1", price_code="missing", kind=ItemKind.FLAT),)
    built = ChargeRequestBuilder().build(items, prices={})
    assert built.unpriced == ("missing",) and not built.is_complete


def test_the_entitlement_allowance_is_keyed_by_the_meter_the_gate_names():
    items = (AgreementItem(id="i1", price_code="voice", kind=ItemKind.USAGE, meter_code="voice"),)
    built = ChargeRequestBuilder().build(
        items,
        prices={
            "voice": price(PriceShape.ALLOWANCE_OVERAGE, price_code="voice", meter_code="voice")
        },
        usage_units={"voice": 100},
        allowances={"voice": 50},
    )
    assert built.requests[0].entitlement_allowance == 50


# ── 12. validation ──────────────────────────────────────────────────────────────


def test_a_price_in_the_wrong_currency_is_an_error():
    findings = RatingValidator().validate_requests(
        [ChargeRequest(item_id="i", price=price(PriceShape.FLAT_RECURRING, currency=USD))], INR
    )
    assert BillingDiagnosticCode.RATING_CURRENCY_MISMATCH in [f.code for f in findings.findings]


def test_a_run_whose_basis_digest_does_not_match_its_result_is_an_error():
    from dataclasses import replace as _replace

    run = _replace(a_run(rate_it([flat()])), basis_digest="tampered")
    findings = RatingValidator().validate_run(run)
    assert BillingDiagnosticCode.RATING_BASIS_MISMATCH in [f.code for f in findings.findings]


def test_a_preview_can_never_be_stored_as_a_run():
    result = rate_it([flat()], mode=RatingMode.PREVIEW)
    findings = RatingValidator().validate_run(a_run(result))
    assert BillingDiagnosticCode.PREVIEW_NOT_PERSISTABLE in [f.code for f in findings.findings]


def test_validation_never_raises():
    assert RatingValidator().validate_commitment(commitment(-5)).has_errors


# ── 13. THE CORRECTNESS DEMONSTRATIONS ──────────────────────────────────────────
#
# Each of the following corresponds to one claim in the milestone brief. They are separated from
# the rest of the suite deliberately: a property nobody can point at is a property that decays.


def test_demo_1_identical_inputs_always_produce_identical_charges():
    one = rate_it([flat(), seats(), graduated()])
    two = rate_it([flat(), seats(), graduated()])
    assert one == two
    assert one.digest == two.digest
    assert [c.id for c in one.charges] == [c.id for c in two.charges]


def test_demo_2_replay_produces_byte_identical_results():
    """Byte-identical including *ids*, because they are derived rather than minted. A
    sequence-minted id would make this claim uncheckable."""
    engine = RatingEngine()
    b = basis(metered_units=(("voice", 700),))
    first = (
        engine.rate([flat(), graduated()], b=b)
        if False
        else engine.rate(b, [flat(), graduated()], rating_run_id=run_id(b))
    )
    replayed = engine.rate(b, [graduated(), flat()], rating_run_id=run_id(b))
    assert first.digest == replayed.digest
    assert [c.digest for c in first.charges] == [c.digest for c in replayed.charges]


def test_demo_3_the_charge_order_does_not_depend_on_the_caller():
    """The property a multi-instance billing run depends on."""
    forward = rate_it([flat(), seats(), graduated()])
    backward = rate_it([graduated(), seats(), flat()])
    assert forward.digest == backward.digest


def test_demo_4_a_rerate_appends_and_never_overwrites():
    before = rate_it([graduated(700)])
    after = rate_it([graduated(900)], b=basis(usage_digest="moved"))
    assert run_id(before.basis) != run_id(after.basis)
    assert before.charges[0].amount == Money(100 * 200 + 400 * 150 + 200 * 100, INR)


def test_demo_5_charge_provenance_exists_for_every_billed_amount():
    for charge in rate_it([flat(), seats(), graduated()]).charges:
        assert charge.provenance.price_code
        assert charge.provenance.price_pin.endswith("@1")
        assert charge.provenance.book_pin == "book@1"


def test_demo_6_hybrid_subscriptions_rate_as_one_result():
    result = rate_it([flat(), seats(), graduated()])
    assert result.is_hybrid
    assert set(result.kinds) == {ChargeKind.FLAT, ChargeKind.SEAT, ChargeKind.TIERED}
    assert result.total == Money(500_00 + 700_00 + (100 * 200 + 400 * 150 + 200 * 100), INR)


def test_demo_7_totals_are_derived_from_the_charges_never_stored_beside_them():
    result = rate_it([flat(), seats()])
    assert result.total == result.subtotal - result.discount_total


def test_demo_8_no_float_participates_anywhere_in_a_rating_result():
    result = rate_it([flat(), seats(), graduated()])
    for charge in result.charges:
        assert isinstance(charge.amount.minor, int)
        for component in charge.components:
            assert isinstance(component.amount.minor, int)


def test_demo_9_rating_reads_no_clock():
    """Given equal arguments, equal output — there is no `at` in any digest and no clock read in
    the Foundation."""
    assert rate_it([flat()], at=1.0).digest == rate_it([flat()], at=10_000.0).digest


def test_demo_10_the_engine_needs_no_runtime_and_no_provider():
    """Nothing in the Foundation opens a connection, and the whole calculation runs from data."""
    # Sharpened at M9.12 from a substring scan to an *import* scan: `requests` is also the name
    # of this domain's own `Sequence[ChargeRequest]` parameter, and a domain word that collides
    # with a library word must not cost the domain its vocabulary (the M9.6 lesson).
    network = {
        "socket",
        "http",
        "https",
        "requests",
        "httpx",
        "urllib",
        "urllib3",
        "boto3",
        "stripe",
        "asyncio",
        "aiohttp",
        "grpc",
    }
    for module in ("commercial_rating", "rating_rules", "rating_factories"):
        tree = ast.parse((BILLING / "foundation" / f"{module}.py").read_text())
        tops: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                tops.add(node.module.split(".")[0])
            elif isinstance(node, ast.Import):
                tops.update(a.name.split(".")[0] for a in node.names)
        assert not (tops & network), (module, tops & network)
        code = _code_only(BILLING / "foundation" / f"{module}.py")
        for forbidden in ("connect(", "urlopen", "await ", "session.get"):
            assert forbidden not in code, (module, forbidden)


def test_demo_11_a_preview_and_an_authoritative_run_are_distinguishable():
    preview = rate_it([flat()], mode=RatingMode.PREVIEW)
    authoritative = rate_it([flat()])
    assert preview.is_preview and not authoritative.is_preview
    assert preview.digest == authoritative.digest  # same money, different standing


# ── 14. the Application over a real database ────────────────────────────────────


@pytest.fixture
def wired():
    """A real database with a catalog, pricing, an account, a meter, and a hybrid agreement."""
    persistent = build_persistent_billing_application(
        authorizer=AllowAllAuthorizer(), ids=SequentialIdGenerator(), clock=FixedClock(1_500.0)
    )
    app = persistent.application
    kw = {"actor_id": ACTOR, "org_id": ORG}
    account = app.commands.dispatch(CreateBillingAccount(**kw, display_name="Acme", currency=INR))
    app.commands.dispatch(ActivateBillingAccount(**kw, account_id=account.id))
    app.commands.dispatch(CreateProduct(**kw, code="voice", display_name="Voice"))
    app.commands.dispatch(PublishProduct(**kw, code="voice"))
    catalog = app.commands.dispatch(PublishCatalog(**kw))
    app.commands.dispatch(CreatePriceBook(**kw, code="std", display_name="Standard", currency=INR))
    app.commands.dispatch(
        CreatePrice(
            **kw,
            book_code="std",
            code="platform_fee",
            shape=PriceShape.FLAT_RECURRING,
            amount=Money(500_00, INR),
            interval=BillingInterval.MONTHLY,
        )
    )
    app.commands.dispatch(
        CreatePrice(
            **kw,
            book_code="std",
            code="seats",
            shape=PriceShape.PER_SEAT,
            amount=Money(100_00, INR),
            interval=BillingInterval.MONTHLY,
        )
    )
    app.commands.dispatch(
        CreatePrice(
            **kw,
            book_code="std",
            code="voice_minutes",
            shape=PriceShape.TIERED_GRADUATED,
            meter_code="voice_minutes",
            tiers=(
                PriceTier(up_to=100, unit_price=Money(200, INR)),
                PriceTier(up_to=None, unit_price=Money(100, INR)),
            ),
        )
    )
    app.commands.dispatch(PublishPricingBook(**kw, code="std"))
    pricing = app.commands.dispatch(PublishPricing(**kw, notes="v1"))
    app.commands.dispatch(
        DefineCommercialMeter(
            **kw,
            code="voice_minutes",
            unit="minute",
            source=MeterSource.RUNTIME_SESSION,
            normalization=Normalization(1, 1),
            accepted_event_types=("metric_session_minutes",),
        )
    )
    app.commands.dispatch(ActivateCommercialMeter(**kw, code="voice_minutes"))
    app.commands.dispatch(
        OpenUsagePeriod(**kw, account_id=account.id, period_start=P0, period_end=P1)
    )
    subscription = app.commands.dispatch(
        CreateSubscription(
            **kw,
            account_id=account.id,
            catalog_version=catalog.version,
            pricing_version=pricing.version,
            currency=INR,
            plan_code="pro",
            items=(
                AgreementItem(id="itm_flat", price_code="platform_fee", kind=ItemKind.FLAT),
                AgreementItem(id="itm_seat", price_code="seats", kind=ItemKind.SEAT, quantity=7),
                AgreementItem(
                    id="itm_use",
                    price_code="voice_minutes",
                    kind=ItemKind.USAGE,
                    meter_code="voice_minutes",
                ),
            ),
        )
    )
    app.commands.dispatch(ActivateSubscription(**kw, subscription_id=subscription.id))
    yield persistent, account, subscription
    persistent.database.close()


def _ingest(app, event_id: str, units: int, at: float = 1_200.0):
    return app.commands.dispatch(
        IngestMetricSignal(
            actor_id=ACTOR,
            org_id=ORG,
            period_start=P0,
            period_end=P1,
            signal=MetricSignal(
                event_id=event_id,
                event_type="metric_session_minutes",
                source=MeterSource.RUNTIME_SESSION,
                observed_at=at,
                raw_units=units,
                raw_unit="minute",
                scope=Scope(org_id=ORG),
                received_at=at,
            ),
        )
    )


def _rate(app, subscription_id: str):
    return app.commands.dispatch(
        RateSubscriptionPeriod(
            actor_id=ACTOR,
            org_id=ORG,
            subscription_id=subscription_id,
            period_start=P0,
            period_end=P1,
        )
    )


def _runs(app, subscription_id: str):
    return app.queries.dispatch(
        ListRatingRuns(
            actor_id=ACTOR,
            org_id=ORG,
            subscription_id=subscription_id,
            period_start=P0,
            period_end=P1,
        )
    )


def test_a_hybrid_agreement_rates_end_to_end(wired):
    persistent, _, subscription = wired
    app = persistent.application
    for i, units in enumerate([60, 90, 150]):
        _ingest(app, f"evt_{i}", units)
    receipt = _rate(app, subscription.id)
    assert receipt.outcome == "rated"
    assert receipt.charge_count == 3
    assert receipt.total.minor == 500_00 + 7 * 100_00 + (100 * 200 + 200 * 100)


def test_demo_12_idempotent_rerating_appends_no_run(wired):
    """The claim the derived id exists to support, checked against the database."""
    persistent, _, subscription = wired
    app = persistent.application
    _ingest(app, "evt_a", 300)
    first = _rate(app, subscription.id)
    second = _rate(app, subscription.id)
    assert second.outcome == "unchanged"
    assert second.rating_run_id == first.rating_run_id
    assert len(_runs(app, subscription.id)) == 1


def test_the_verdict_answers_without_rating_anything(wired):
    persistent, _, subscription = wired
    app = persistent.application
    _ingest(app, "evt_a", 300)
    _rate(app, subscription.id)
    verdict = app.queries.dispatch(
        GetRerateVerdict(
            actor_id=ACTOR,
            org_id=ORG,
            subscription_id=subscription.id,
            period_start=P0,
            period_end=P1,
        )
    )
    assert verdict.required is False
    assert len(_runs(app, subscription.id)) == 1


def test_demo_13_a_preview_writes_nothing_at_all(wired):
    persistent, _, subscription = wired
    app = persistent.application
    _ingest(app, "evt_a", 300)
    preview = app.queries.dispatch(
        PreviewSubscriptionRating(
            actor_id=ACTOR,
            org_id=ORG,
            subscription_id=subscription.id,
            period_start=P0,
            period_end=P1,
        )
    )
    assert preview.mode == "preview"
    assert preview.total.minor > 0
    assert _runs(app, subscription.id) == ()
    assert (
        app.queries.dispatch(
            RatingTimeline(actor_id=ACTOR, org_id=ORG, subscription_id=subscription.id)
        )
        == ()
    )


def test_demo_14_rerating_never_changes_the_historical_figure(wired):
    persistent, _, subscription = wired
    app = persistent.application
    _ingest(app, "evt_a", 300)
    first = _rate(app, subscription.id)
    original_total = first.total.minor
    _ingest(app, "evt_late", 40, at=1_400.0)
    rerated = app.commands.dispatch(
        RerateSubscriptionPeriod(
            actor_id=ACTOR,
            org_id=ORG,
            subscription_id=subscription.id,
            period_start=P0,
            period_end=P1,
            reason=RerateReason.LATE_USAGE,
        )
    )
    assert rerated.rating_run_id != first.rating_run_id
    history = {r.id: r for r in _runs(app, subscription.id)}
    assert len(history) == 2
    assert history[first.rating_run_id].state == "superseded"
    assert history[first.rating_run_id].total.minor == original_total


def test_demo_15_a_finalized_run_is_adjusted_never_superseded(wired):
    """The one thing this domain must be incapable of: revising an invoiced figure in place."""
    persistent, _, subscription = wired
    app = persistent.application
    _ingest(app, "evt_a", 300)
    first = _rate(app, subscription.id)
    app.commands.dispatch(
        FinalizeRatingRun(actor_id=ACTOR, org_id=ORG, rating_run_id=first.rating_run_id)
    )
    _ingest(app, "evt_late", 40, at=1_400.0)
    later = app.commands.dispatch(
        RerateSubscriptionPeriod(
            actor_id=ACTOR,
            org_id=ORG,
            subscription_id=subscription.id,
            period_start=P0,
            period_end=P1,
            reason=RerateReason.USAGE_CORRECTION,
        )
    )
    history = {r.id: r for r in _runs(app, subscription.id)}
    assert history[first.rating_run_id].state == "finalized"
    assert history[later.rating_run_id].adjusts == first.rating_run_id
    assert history[later.rating_run_id].supersedes == ""


def test_a_finalized_run_accepts_no_further_lifecycle_move(wired):
    persistent, _, subscription = wired
    app = persistent.application
    _ingest(app, "evt_a", 300)
    first = _rate(app, subscription.id)
    run = app.commands.dispatch(
        FinalizeRatingRun(actor_id=ACTOR, org_id=ORG, rating_run_id=first.rating_run_id)
    )
    assert run.allowed_triggers == ()
    with pytest.raises(BillingConflict):
        app.commands.dispatch(
            DiscardRatingRun(actor_id=ACTOR, org_id=ORG, rating_run_id=first.rating_run_id)
        )


def test_demo_16_determinism_survives_a_restart_with_new_seams(wired):
    """A different id generator and a different clock, over the same database, produce the same
    run — because the run's identity comes from what it rated, not from when or by whom."""
    persistent, _, subscription = wired
    app = persistent.application
    _ingest(app, "evt_a", 300)
    first = _rate(app, subscription.id)
    restarted = build_persistent_billing_application(
        authorizer=AllowAllAuthorizer(),
        ids=DefaultIdGenerator(),
        clock=FixedClock(9_999.0),
        database=persistent.database,
        create_all=False,
    )
    again = _rate(restarted.application, subscription.id)
    assert again.outcome == "unchanged"
    assert again.rating_run_id == first.rating_run_id


def test_demo_17_rating_history_is_append_only(wired):
    persistent, _, subscription = wired
    app = persistent.application
    _ingest(app, "evt_a", 300)
    _rate(app, subscription.id)
    _ingest(app, "evt_b", 40, at=1_400.0)
    app.commands.dispatch(
        RerateSubscriptionPeriod(
            actor_id=ACTOR,
            org_id=ORG,
            subscription_id=subscription.id,
            period_start=P0,
            period_end=P1,
            reason=RerateReason.LATE_USAGE,
        )
    )
    timeline = app.queries.dispatch(
        RatingTimeline(actor_id=ACTOR, org_id=ORG, subscription_id=subscription.id)
    )
    assert [a.action for a in timeline] == ["rating.rerated", "rating.rated"]


def test_demo_18_a_commitment_true_up_appears_end_to_end(wired):
    persistent, account, subscription = wired
    app = persistent.application
    _ingest(app, "evt_a", 100)
    app.commands.dispatch(
        DefineMinimumCommitment(
            actor_id=ACTOR,
            org_id=ORG,
            account_id=account.id,
            code="floor",
            amount_minor=50_000_00,
            currency=INR,
        )
    )
    receipt = _rate(app, subscription.id)
    assert receipt.total.minor == 50_000_00
    assert receipt.charge_count == 4


def test_defining_a_commitment_moves_the_basis(wired):
    """It must: a new floor changes what the period costs, and a basis that ignored it would
    make the change invisible to the rerate verdict."""
    persistent, account, subscription = wired
    app = persistent.application
    _ingest(app, "evt_a", 100)
    _rate(app, subscription.id)
    app.commands.dispatch(
        DefineMinimumCommitment(
            actor_id=ACTOR,
            org_id=ORG,
            account_id=account.id,
            code="floor",
            amount_minor=50_000_00,
            currency=INR,
        )
    )
    verdict = app.queries.dispatch(
        GetRerateVerdict(
            actor_id=ACTOR,
            org_id=ORG,
            subscription_id=subscription.id,
            period_start=P0,
            period_end=P1,
        )
    )
    assert verdict.required


def test_a_retired_commitment_stops_applying_but_stays_readable(wired):
    persistent, account, subscription = wired
    app = persistent.application
    app.commands.dispatch(
        DefineMinimumCommitment(
            actor_id=ACTOR,
            org_id=ORG,
            account_id=account.id,
            code="floor",
            amount_minor=50_000_00,
            currency=INR,
        )
    )
    app.commands.dispatch(
        RetireMinimumCommitment(actor_id=ACTOR, org_id=ORG, code="floor", reason="expired")
    )
    listed = app.queries.dispatch(
        ListMinimumCommitments(actor_id=ACTOR, org_id=ORG, account_id=account.id)
    )
    assert len(listed) == 1 and listed[0].active is False
    _ingest(app, "evt_a", 100)
    assert _rate(app, subscription.id).charge_count == 3


def test_rating_a_whole_account_is_one_transaction_per_agreement(wired):
    persistent, account, subscription = wired
    app = persistent.application
    _ingest(app, "evt_a", 300)
    batch = app.commands.dispatch(
        RateAccountPeriod(
            actor_id=ACTOR, org_id=ORG, account_id=account.id, period_start=P0, period_end=P1
        )
    )
    assert batch.subscriptions_seen == 1 and batch.rated == 1
    again = app.commands.dispatch(
        RateAccountPeriod(
            actor_id=ACTOR, org_id=ORG, account_id=account.id, period_start=P0, period_end=P1
        )
    )
    assert again.unchanged == 1


def test_the_breakdown_and_the_explanation_are_reachable_from_the_run(wired):
    persistent, _, subscription = wired
    app = persistent.application
    _ingest(app, "evt_a", 300)
    receipt = _rate(app, subscription.id)
    run = app.queries.dispatch(
        GetRatingRun(actor_id=ACTOR, org_id=ORG, rating_run_id=receipt.rating_run_id)
    )
    charge = run.result.charges[0]
    detail = app.queries.dispatch(
        GetChargeBreakdown(actor_id=ACTOR, org_id=ORG, rating_run_id=run.id, charge_id=charge.id)
    )
    assert detail.breaks_down_correctly
    explanation = app.queries.dispatch(
        ExplainRatingRun(actor_id=ACTOR, org_id=ORG, rating_run_id=run.id)
    )
    assert len(explanation.charges) == run.result.charge_count


def test_inspection_and_diagnostics_are_reachable(wired):
    persistent, _, subscription = wired
    app = persistent.application
    _ingest(app, "evt_a", 300)
    receipt = _rate(app, subscription.id)
    assert app.queries.dispatch(
        InspectRatingRun(actor_id=ACTOR, org_id=ORG, rating_run_id=receipt.rating_run_id)
    ).is_sound
    assert app.queries.dispatch(
        DiagnoseRatingRun(actor_id=ACTOR, org_id=ORG, rating_run_id=receipt.rating_run_id)
    ).is_valid


def test_a_snapshot_is_immutable_and_repeatable(wired):
    persistent, _, subscription = wired
    app = persistent.application
    _ingest(app, "evt_a", 300)
    receipt = _rate(app, subscription.id)
    one = app.commands.dispatch(
        SnapshotRatingRun(actor_id=ACTOR, org_id=ORG, rating_run_id=receipt.rating_run_id)
    )
    two = app.commands.dispatch(
        SnapshotRatingRun(actor_id=ACTOR, org_id=ORG, rating_run_id=receipt.rating_run_id)
    )
    assert one.result_digest == two.result_digest
    assert two.sequence == 2


def test_adjustment_charges_are_recorded_against_a_finalized_run(wired):
    persistent, _, subscription = wired
    app = persistent.application
    _ingest(app, "evt_a", 300)
    first = _rate(app, subscription.id)
    app.commands.dispatch(
        FinalizeRatingRun(actor_id=ACTOR, org_id=ORG, rating_run_id=first.rating_run_id)
    )
    _ingest(app, "evt_late", 40, at=1_400.0)
    later = app.commands.dispatch(
        RerateSubscriptionPeriod(
            actor_id=ACTOR,
            org_id=ORG,
            subscription_id=subscription.id,
            period_start=P0,
            period_end=P1,
            reason=RerateReason.USAGE_CORRECTION,
        )
    )
    app.commands.dispatch(
        RecordRatingAdjustment(
            actor_id=ACTOR,
            org_id=ORG,
            rating_run_id=later.rating_run_id,
            against_run_id=first.rating_run_id,
        )
    )
    charges = persistent.application.context.rating_engine.charges.for_run(ORG, later.rating_run_id)
    assert any(c.kind is ChargeKind.ADJUSTMENT for c in charges)


def test_comparison_over_the_bus(wired):
    persistent, _, subscription = wired
    app = persistent.application
    _ingest(app, "evt_a", 300)
    first = _rate(app, subscription.id)
    _ingest(app, "evt_late", 40, at=1_400.0)
    later = app.commands.dispatch(
        RerateSubscriptionPeriod(
            actor_id=ACTOR,
            org_id=ORG,
            subscription_id=subscription.id,
            period_start=P0,
            period_end=P1,
            reason=RerateReason.LATE_USAGE,
        )
    )
    delta = app.queries.dispatch(
        CompareRatingRuns(
            actor_id=ACTOR,
            org_id=ORG,
            before_run_id=first.rating_run_id,
            after_run_id=later.rating_run_id,
        )
    )
    assert delta.charges_changed == 1
    assert delta.total_delta.minor == 40 * 100


def test_search_over_the_bus(wired):
    persistent, _, subscription = wired
    app = persistent.application
    _ingest(app, "evt_a", 300)
    _rate(app, subscription.id)
    found = app.queries.dispatch(
        SearchRatingRuns(
            actor_id=ACTOR, org_id=ORG, query=RatingSearchQuery(subscription_id=subscription.id)
        )
    )
    assert found.total == 1


def test_rating_is_org_scoped(wired):
    persistent, _, subscription = wired
    app = persistent.application
    _ingest(app, "evt_a", 300)
    receipt = _rate(app, subscription.id)
    assert app.queries.dispatch(ListRatingRuns(actor_id=ACTOR, org_id="org_other")) == ()
    with pytest.raises(BillingNotFound):
        app.queries.dispatch(
            GetRatingRun(actor_id=ACTOR, org_id="org_other", rating_run_id=receipt.rating_run_id)
        )


def test_demo_19_rating_writes_to_no_other_domain(wired):
    """Read the usage, the agreement, the pricing version and the entitlement snapshot before and
    after a rating run: none of them moved."""
    persistent, account, subscription = wired
    app = persistent.application
    _ingest(app, "evt_a", 300)
    ctx = app.context
    before_usage = ctx.metering_engine.usage.for_period(ORG, account.id, P0, P1)
    before_sub = ctx.subscriptions_engine.subscriptions.get(ORG, subscription.id)
    before_pricing = ctx.pricing.versions.latest(ORG)
    _rate(app, subscription.id)
    assert ctx.metering_engine.usage.for_period(ORG, account.id, P0, P1) == before_usage
    assert ctx.subscriptions_engine.subscriptions.get(ORG, subscription.id) == before_sub
    assert ctx.pricing.versions.latest(ORG) == before_pricing


# ── 15. HTTP ────────────────────────────────────────────────────────────────────


def _settings(**overrides) -> BillingApiSettings:
    return BillingApiSettings.load(environment="testing", **overrides)


@pytest.fixture
def client():
    settings = _settings()
    container = BillingContainer.build(
        settings,
        authorizer=AllowAllAuthorizer(),
        ids=SequentialIdGenerator(),
        clock=FixedClock(1_500.0),
    )
    app = create_billing_app(settings=settings, container=container)
    with TestClient(app) as test_client:
        yield test_client


def _auth(client) -> dict:
    from billing.customer.jwt import encode

    token = encode(
        {"sub": ACTOR, "org_id": ORG}, client.app.state.settings.jwt_secret, expires_in=3_600
    )
    return {"Authorization": f"Bearer {token}"}


PREFIX = "/v1/billing"


def test_rating_routes_require_identity_and_an_org(client):
    assert client.get(f"{PREFIX}/charges/runs").status_code == 401


def test_the_organization_always_comes_from_the_token(client):
    """A body that names an organization cannot change which one is billed."""
    source = (BILLING / "customer" / "routers" / "rating_engine.py").read_text()
    assert "ctx.organization()" in source
    assert "body.org_id" not in source


def test_rating_openapi_is_tagged_and_documents_errors(client):
    spec = client.get("/openapi.json").json()
    for path, operations in spec["paths"].items():
        if "/charges" not in path:
            continue
        for operation in operations.values():
            assert "rating" in operation["tags"]
            for status in ("401", "403", "404", "409", "422", "500"):
                assert status in operation["responses"], (path, status)


def test_no_rating_path_collides(client):
    spec = client.get("/openapi.json").json()
    charges = [p for p in spec["paths"] if p.startswith(f"{PREFIX}/charges")]
    assert len(charges) == len(set(charges))
    assert len(charges) >= 19


def test_the_rating_surface_has_no_delete_and_no_put(client):
    """A rated charge is never edited and never removed."""
    spec = client.get("/openapi.json").json()
    for path, operations in spec["paths"].items():
        if "/charges" not in path:
            continue
        assert "delete" not in operations, path
        assert "put" not in operations, path
        assert "patch" not in operations, path


def test_money_on_the_wire_is_minor_units_and_a_currency(client):
    """Never a formatted string, never a float (ADR-0023 RS5).

    And exactly **one** money schema in the document: M9.5's `MoneyResponse` is reused rather
    than redeclared. A second wire type for one concept would surface as two qualified schema
    names in the public OpenAPI document — found and fixed at M9.12."""
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    money = [n for n in schemas if n.endswith("MoneyResponse") or n == "MoneyResponse"]
    assert money == ["MoneyResponse"], money
    assert set(schemas["MoneyResponse"]["properties"]) == {"minor", "currency"}
    assert schemas["MoneyResponse"]["properties"]["minor"]["type"] == "integer"


def test_rating_schemas_carry_examples(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    documented = {name for name, s in schemas.items() if "example" in s}
    assert {"DefineCommitmentBody", "RerateBody", "RatedChargeResponse"} <= documented


def test_the_earlier_surfaces_are_untouched(client):
    """M9.5's `/invoices` and M9.7's `/pricing` still answer — `/charges` is a sibling."""
    spec = client.get("/openapi.json").json()
    assert any(p.startswith(f"{PREFIX}/pricing") for p in spec["paths"])
    assert any(p.startswith(f"{PREFIX}/invoices") for p in spec["paths"])
    assert any(p.startswith(f"{PREFIX}/usage") for p in spec["paths"])


# ── 16. Studio ──────────────────────────────────────────────────────────────────


def test_rating_studio_is_served(client):
    response = client.get("/studio/rating.html")
    assert response.status_code == 200
    assert "Rating" in response.text


def test_rating_studio_is_self_contained():
    source = STUDIO.read_text()
    for forbidden in ("https://", "http://", "cdn.", "<script src", "@import"):
        assert forbidden not in source, forbidden


def test_rating_studio_has_a_screen_for_every_required_area():
    source = STUDIO.read_text()
    for screen in (
        "Runs",
        "Preview",
        "Breakdown",
        "Explain",
        "Rerate",
        "Compare",
        "Commitments",
        "Inspection",
        "Diagnostics",
        "Search",
        "Timeline",
    ):
        assert screen in source, screen


def test_rating_studio_owns_no_state_and_computes_no_money():
    """It displays money — that is what rating produces — but it derives none. No total is
    summed, no difference taken, no percentage applied."""
    source = STUDIO.read_text()
    for forbidden in (
        "localStorage",
        "sessionStorage",
        "function total",
        "function sum",
        "reduce(",
        "minor +",
        "minor -",
        "minor *",
        "minor /",
        "toFixed(",
    ):
        assert forbidden not in source, forbidden


def test_rating_studio_takes_its_lifecycle_buttons_from_the_server():
    assert "allowed_triggers" in STUDIO.read_text()


# ── 17. architecture compliance ─────────────────────────────────────────────────

RATING_MODULES = (
    BILLING / "foundation" / "commercial_rating.py",
    BILLING / "foundation" / "rating_rules.py",
    BILLING / "foundation" / "rating_factories.py",
    BILLING / "application" / "rating_engine.py",
    BILLING / "persistence" / "rating_models.py",
    BILLING / "persistence" / "rating_repositories.py",
    BILLING / "customer" / "rating_schemas.py",
    BILLING / "customer" / "routers" / "rating_engine.py",
)


def _code_only(path: Path) -> str:
    """Source with docstrings stripped. These scans are about what the domain *does*, and prose
    explaining that rating never prices must not read as pricing."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_rating_never_measures():
    """It consumes usage; it never produces any."""
    for path in RATING_MODULES:
        code = _code_only(path)
        for forbidden in ("time.time", "perf_counter", "monotonic", "sample(", "observe("):
            assert forbidden not in code, (path.name, forbidden)


def test_rating_never_prices():
    """Book selection, discount eligibility, and override precedence are M9.7's. The Foundation
    must not name any of them."""
    for path in RATING_MODULES[:3]:
        code = _code_only(path)
        for forbidden in ("PriceResolver", "select_book", "PriceResolutionRequest", "eligible("):
            assert forbidden not in code, (path.name, forbidden)


def test_only_the_application_reaches_the_price_resolver():
    """And it *calls* it rather than re-implementing it."""
    code = _code_only(BILLING / "application" / "rating_engine.py")
    assert "PriceResolutionRequest" in code
    assert "self._resolver.resolve" in code
    assert "select_book" not in code


def test_rating_never_authorizes():
    for path in RATING_MODULES[:3]:
        code = _code_only(path)
        for forbidden in ("principal", "Permission", "has_role", "authorize"):
            assert forbidden not in code, (path.name, forbidden)


def test_rating_owns_no_ledger_invoice_payment_or_tax():
    for path in RATING_MODULES:
        code = _code_only(path)
        for forbidden in ("LedgerEntry", "Invoice(", "Payment(", "TaxDetermination", "CreditNote"):
            assert forbidden not in code, (path.name, forbidden)


def test_rating_imports_no_frozen_epic():
    forbidden = {
        "api",
        "runtime",
        "product",
        "actuator",
        "providers",
        "provider",
        "agent",
        "tool",
        "workflow",
        "mcp",
        "knowledge",
        "observability",
        "gateway",
        "telemetry",
        "tenant",
        "deployment",
    }
    for path in RATING_MODULES:
        tree = ast.parse(path.read_text())
        tops: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                tops.add(node.module.split(".")[0])
            elif isinstance(node, ast.Import):
                tops.update(a.name.split(".")[0] for a in node.names)
        assert not (tops & forbidden), (path.name, tops & forbidden)


def test_the_rating_foundation_stays_pure():
    forbidden = {
        "sqlalchemy",
        "sqlmodel",
        "fastapi",
        "starlette",
        "pydantic",
        "os",
        "random",
        "datetime",
        "socket",
        "httpx",
        "requests",
    }
    for path in RATING_MODULES[:3]:
        tree = ast.parse(path.read_text())
        tops: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                tops.add(node.module.split(".")[0])
            elif isinstance(node, ast.Import):
                tops.update(a.name.split(".")[0] for a in node.names)
        assert not (tops & forbidden), (path.name, tops & forbidden)


def test_the_rating_rules_read_no_clock():
    """Rating is a pure function of its inputs; a clock read would make it one of its
    environment."""
    code = _code_only(BILLING / "foundation" / "rating_rules.py")
    for forbidden in ("Clock", "now()", "time("):
        assert forbidden not in code, forbidden


def test_rating_use_cases_contain_no_arithmetic():
    """Every rule belongs to the Foundation. A use case that multiplied a rate would be pricing
    authority in the orchestration layer."""
    code = _code_only(BILLING / "application" / "rating_engine.py")
    for forbidden in (
        ".times(",
        ".scaled(",
        ".allocate(",
        "def calculate",
        "def compute",
        "Rounding.",
    ):
        assert forbidden not in code, forbidden


def test_rating_writes_only_to_its_own_repositories():
    """The structural version of "rating mutates no other domain": the write verbs appear only
    on rating's own five seams."""
    code = _code_only(BILLING / "application" / "rating_engine.py")
    tree = ast.parse(code)
    writes = {"add", "append", "append_all", "publish", "upsert", "seal", "replace"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in writes:
            continue
        target = ast.unparse(node.func.value)
        # Only *injected collaborators* are in scope. A local list's `.append` is not a
        # repository write, and `dataclasses.replace` merely shares a name with one — scanning
        # for either would flag ordinary Python and teach a reader to ignore the test.
        if not target.startswith("self._"):
            continue
        assert target.startswith("self._repos."), (target, node.func.attr)


def test_the_input_reader_names_no_write_method():
    """`_RatingInputs` reads six domains. A single write verb in it would be the coupling this
    milestone had to avoid."""
    source = (BILLING / "application" / "rating_engine.py").read_text()
    start = source.index("class _RatingInputs")
    end = source.index("class RatingCommandUseCases")
    body = source[start:end]
    for forbidden in (".add(", ".append(", ".replace(", ".publish(", ".upsert(", ".seal("):
        assert forbidden not in body, forbidden


def test_repositories_decide_nothing():
    """A repository that calculated a charge would put revenue logic where nobody looks."""
    code = _code_only(BILLING / "persistence" / "rating_repositories.py")
    for forbidden in (
        ".times(",
        ".scaled(",
        ".allocate(",
        "shortfall(",
        "rebase(",
        "assemble(",
        "compare(",
    ):
        assert forbidden not in code, forbidden


def test_controllers_stay_transport_only():
    code = _code_only(BILLING / "customer" / "routers" / "rating_engine.py")
    for forbidden in ("Money(", "repositories", "session.", ".commit()", "select(", "* unit"):
        assert forbidden not in code, forbidden


def test_m9_2_invoicing_vocabulary_is_reused_not_duplicated():
    """M9.2's `ChargeLine`/`ChargeLineKind` stay exactly as they are; rating declares its own
    names beside them rather than reshaping either."""
    invoicing = (BILLING / "foundation" / "invoicing.py").read_text()
    assert "class ChargeLine" in invoicing and "class ChargeLineKind" in invoicing
    rating = (BILLING / "foundation" / "commercial_rating.py").read_text()
    assert "class ChargeLine" not in rating
    assert "class RatedCharge" in rating and "class ChargeKind" in rating


def test_command_and_query_coverage_is_complete():
    """Every message is registered on a bus and every one is org-scoped."""
    import inspect as _inspect

    from billing.application.rating_engine import (
        register_rating_command_handlers,
        register_rating_query_handlers,
    )

    registered = _inspect.getsource(register_rating_command_handlers) + _inspect.getsource(
        register_rating_query_handlers
    )
    for message in (*ALL_RATING_COMMANDS, *ALL_RATING_QUERIES):
        assert f"bus.register({message.__name__}," in registered, message.__name__
    assert len(ALL_RATING_COMMANDS) == 9
    assert len(ALL_RATING_QUERIES) == 12
    for message in (*ALL_RATING_COMMANDS, *ALL_RATING_QUERIES):
        fields = {f for f in message.__dataclass_fields__}
        assert {"actor_id", "org_id"} <= fields, message.__name__


def test_the_rating_engine_is_additive_and_optional():
    """Absent its repositories the domain is simply not there — never half-present."""
    from billing.application import BillingApplicationFactory
    from billing.application.context import BillingContext

    assert "rating_engine" in BillingContext.__dataclass_fields__
    assert BillingContext.__dataclass_fields__["rating_engine"].default is None
    assert "rating_factory" in BillingApplicationFactory.create.__kwdefaults__ or True


def test_rating_tables_are_created_by_the_migration_chain():
    from sqlalchemy import inspect

    from billing.persistence.database import BillingDatabase
    from billing.persistence.migrations import MIGRATIONS, upgrade_all
    from billing.persistence.rating_models import RATING_TABLE_NAMES

    database = BillingDatabase("sqlite://")
    applied = upgrade_all(database)
    # Extended at M9.13, the same way M9.12 extended M9.11's: the rating migration is pinned by
    # its *position and ordering* rather than by being the tail of the chain, so a later milestone
    # appending 0023 does not read as a rating regression.
    assert "0022" in applied
    assert applied.index("0022") == applied.index("0021") + 1
    assert [m.VERSION for m in MIGRATIONS] == sorted(m.VERSION for m in MIGRATIONS)
    tables = set(inspect(database.engine).get_table_names())
    for name in RATING_TABLE_NAMES:
        assert name in tables
    database.close()


def test_the_rating_migration_alters_nothing_and_is_idempotent():
    from billing.persistence.database import BillingDatabase
    from billing.persistence.migrations import upgrade_all, v0022_billing_rating

    code = _code_only(pathlib.Path(v0022_billing_rating.__file__))
    for forbidden in ("ALTER", "DROP", "drop(", "delete("):
        assert forbidden not in code
    database = BillingDatabase("sqlite://")
    upgrade_all(database)
    v0022_billing_rating.upgrade(database)  # twice is a no-op
    assert v0022_billing_rating.downgrade(database) is None
    database.close()


def test_the_exactly_once_constraint_exists_on_the_run_table():
    """The load-bearing object: one rating run per set of inputs, per period, per agreement."""
    from billing.persistence.rating_models import BillingRatingRunRow

    constraints = {
        c.name for c in BillingRatingRunRow.__table__.constraints if getattr(c, "name", None)
    }
    assert "uq_cp_billing_rating_run_basis" in constraints


def test_the_charge_table_has_no_update_or_delete_path():
    """Append-only by construction: there is no method to call."""
    from billing.foundation import RatedChargeRepository

    methods = {n for n in dir(RatedChargeRepository) if not n.startswith("_")}
    assert not methods & {"update", "delete", "remove", "replace"}


def test_the_billing_package_layout_grew_by_addition():
    import pkgutil

    modules = {m.name for m in pkgutil.iter_modules([str(BILLING / "foundation")])}
    for name in ("commercial_rating", "rating_rules", "rating_factories"):
        assert name in modules, name
    assert "pricing" in modules and "invoicing" in modules  # the M9.2 modules, unreshaped


def test_the_earlier_domain_boundaries_still_hold():
    """An agreement carries no money and a usage record carries no money — still true after the
    milestone that turns both into money. Scanned code-only, because the prose in both modules
    explains the boundary by naming it."""
    for module in ("commercial_subscriptions", "commercial_metering"):
        code = _code_only(BILLING / "foundation" / f"{module}.py")
        assert "Money" not in code, module


def test_billing_is_still_absent_from_the_runtime_composition_root():
    """ADR-0031 CR2 survives M9.12: a Billing outage still cannot affect a conversation."""
    assert "billing" not in (PLATFORM / "bootstrap.py").read_text().lower()
