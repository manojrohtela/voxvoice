"""M9.7 — Pricing Engine: tests.

Four layers, one domain: pure Foundation rules, then the Application over a real database,
then the HTTP surface, then architecture compliance. The heaviest coverage sits on the three
properties pricing cannot be retrofitted with — **immutability after publication**,
**deterministic resolution**, and **history that is appended to rather than rewritten** — and
on the boundary that keeps pricing pricing: **it answers what something costs, and never what
someone owes.**
"""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.customer.jwt import encode as epic8_encode
from billing.application import (
    AllowAllAuthorizer,
    ArchivePriceBook,
    BillingAuthorizationError,
    CreateCoupon,
    CreateDiscountPolicy,
    CreatePrice,
    CreatePriceBook,
    CreatePromotion,
    DenyAllAuthorizer,
    DuplicatePriceBook,
    GetPricingBook,
    GetPricingVersion,
    ListCoupons,
    ListPromotions,
    PriceVersionHistory,
    PricingHistory,
    PricingVersionHistory,
    PublishPricing,
    PublishPricingBook,
    ResolvePrice,
    RestorePriceBook,
    RollbackPricing,
    SearchPriceBooks,
    UpdateDraftPrice,
)
from billing.customer import BillingApiSettings, BillingContainer, create_billing_app
from billing.foundation import (
    PRICE_BOOK_LIFECYCLE,
    BillingConflict,
    BillingInterval,
    BillingValidationError,
    Coupon,
    Currency,
    CustomerTier,
    DiscountEngine,
    DiscountKind,
    DiscountPolicy,
    EffectiveDatingService,
    EffectiveRange,
    FixedClock,
    ImmutabilityViolation,
    InvalidBillingTransition,
    Money,
    Price,
    PriceBookSearchQuery,
    PriceOverride,
    PriceResolutionRequest,
    PriceResolver,
    PriceShape,
    PriceTier,
    PricingAssembler,
    PricingBook,
    PricingFactory,
    PricingLifecycleService,
    PricingScope,
    PricingState,
    PricingTrigger,
    PricingValidator,
    Promotion,
    RecordingEventEmitter,
    Region,
    SequentialIdGenerator,
)
from billing.persistence import BillingDatabase, build_persistent_billing_application

PLATFORM = Path(__file__).resolve().parents[1]
BILLING = PLATFORM / "billing"
PREFIX = "/v1/billing/pricing"
SECRET = "test-billing-secret"
ORG = dict(actor_id="usr_1", org_id="org_1")
INR = Currency.INR


# ── fixtures ────────────────────────────────────────────────────────────────────


def _factory() -> tuple[PricingFactory, RecordingEventEmitter]:
    emitter = RecordingEventEmitter()
    return (
        PricingFactory(ids=SequentialIdGenerator(), clock=FixedClock(), emitter=emitter),
        emitter,
    )


def rupees(major: int) -> Money:
    return Money(major * 100, INR)


_UNSET = object()


def _price(
    code="pro",
    *,
    amount=_UNSET,
    shape=PriceShape.FLAT_RECURRING,
    version=1,
    frm=0.0,
    until=None,
    **kw,
) -> Price:
    return Price(
        id=f"prc_{code}_{version}",
        price_book_id="pbk_1",
        code=code,
        shape=shape,
        currency=INR,
        version=version,
        effective=EffectiveRange(frm, until),
        amount=rupees(10_000) if amount is _UNSET else amount,
        interval=BillingInterval.MONTHLY,
        **kw,
    )


def _book(code="public", *, prices=(), state=PricingState.DRAFT, scope=None, **kw) -> PricingBook:
    return PricingBook(
        id=f"pbk_{code}",
        org_id="org_1",
        code=code,
        display_name=code.title(),
        currency=INR,
        state=state,
        scope=scope or PricingScope(),
        prices=tuple(prices),
        **kw,
    )


def _discount(code="launch", *, bp=2500, priority=100, stackable=True, **kw) -> DiscountPolicy:
    return DiscountPolicy(
        id=f"dsc_{code}",
        org_id="org_1",
        code=code,
        display_name=code.title(),
        percentage_bp=bp,
        priority=priority,
        stackable=stackable,
        **kw,
    )


@pytest.fixture()
def app():
    wired = build_persistent_billing_application(
        ids=SequentialIdGenerator(), clock=FixedClock(), authorizer=AllowAllAuthorizer()
    )
    yield wired.application
    wired.database.close()


def _client(authorizer=None, **overrides) -> TestClient:
    settings = BillingApiSettings(environment="testing", jwt_secret=SECRET, **overrides)
    container = BillingContainer.build(
        settings,
        authorizer=authorizer if authorizer is not None else AllowAllAuthorizer(),
        ids=SequentialIdGenerator(),
        clock=FixedClock(),
    )
    return TestClient(
        create_billing_app(settings=settings, container=container), raise_server_exceptions=False
    )


def _auth(user_id="usr_1", org_id="org_1") -> dict[str, str]:
    claims: dict = {"sub": user_id}
    if org_id:
        claims["org"] = org_id
    return {"Authorization": "Bearer " + epic8_encode(claims, SECRET, ttl_seconds=3600)}


@pytest.fixture()
def client():
    with _client() as c:
        yield c


# ── 1. Foundation: lifecycle FSM ────────────────────────────────────────────────


def test_every_price_book_starts_as_a_draft():
    factory, _ = _factory()
    book = factory.create_book(org_id="org_1", code="public", display_name="Public", currency=INR)
    assert book.state is PricingState.DRAFT
    assert book.is_editable and not book.is_live
    assert book.revision == 1 and book.published_version is None


@pytest.mark.parametrize(
    ("state", "trigger", "expected"),
    [
        (PricingState.DRAFT, PricingTrigger.PUBLISH, PricingState.PUBLISHED),
        (PricingState.DRAFT, PricingTrigger.DELETE, PricingState.DELETED),
        (PricingState.PUBLISHED, PricingTrigger.ARCHIVE, PricingState.ARCHIVED),
        (PricingState.ARCHIVED, PricingTrigger.RESTORE, PricingState.PUBLISHED),
        (PricingState.ARCHIVED, PricingTrigger.DELETE, PricingState.DELETED),
    ],
)
def test_legal_lifecycle_edges(state, trigger, expected):
    assert PricingLifecycleService().next_state(state, trigger) is expected


@pytest.mark.parametrize(
    ("state", "trigger"),
    [
        (PricingState.DRAFT, PricingTrigger.ARCHIVE),
        (PricingState.DRAFT, PricingTrigger.RESTORE),
        (PricingState.PUBLISHED, PricingTrigger.PUBLISH),
        (PricingState.PUBLISHED, PricingTrigger.RESTORE),
        (PricingState.ARCHIVED, PricingTrigger.PUBLISH),
        (PricingState.ARCHIVED, PricingTrigger.ARCHIVE),
    ],
)
def test_illegal_lifecycle_edges_are_rejected_never_coerced(state, trigger):
    with pytest.raises(InvalidBillingTransition):
        PricingLifecycleService().next_state(state, trigger)


def test_a_published_price_book_cannot_be_deleted_in_one_step():
    """A book a customer may have been priced from must be archived first — and archival keeps
    it readable forever."""
    lifecycle = PricingLifecycleService()
    assert not lifecycle.can(PricingState.PUBLISHED, PricingTrigger.DELETE)
    assert lifecycle.can(PricingState.PUBLISHED, PricingTrigger.ARCHIVE)
    assert lifecycle.can(PricingState.ARCHIVED, PricingTrigger.DELETE)


def test_a_published_price_book_can_never_return_to_draft():
    """The edge that would make an old invoice unexplainable does not exist."""
    for trigger in PricingTrigger:
        edge = PRICE_BOOK_LIFECYCLE.get(PricingState.PUBLISHED, trigger)
        assert edge is None or edge.target is not PricingState.DRAFT


def test_deleted_is_terminal():
    assert PricingLifecycleService().allowed(PricingState.DELETED) == ()


def test_allowed_triggers_describe_exactly_the_legal_moves():
    lifecycle = PricingLifecycleService()
    assert set(lifecycle.allowed(PricingState.DRAFT)) == {
        PricingTrigger.PUBLISH,
        PricingTrigger.DELETE,
    }
    assert set(lifecycle.allowed(PricingState.PUBLISHED)) == {PricingTrigger.ARCHIVE}
    assert set(lifecycle.allowed(PricingState.ARCHIVED)) == {
        PricingTrigger.RESTORE,
        PricingTrigger.DELETE,
    }


def test_lifecycle_transitions_never_mutate_the_input():
    book = _book()
    moved = PricingLifecycleService().apply(book, PricingTrigger.PUBLISH, at=5.0)
    assert book.state is PricingState.DRAFT
    assert moved.state is PricingState.PUBLISHED and moved.updated_at == 5.0


def test_a_published_book_refuses_edits():
    with pytest.raises(BillingConflict) as excinfo:
        PricingLifecycleService().guard_editable(_book(state=PricingState.PUBLISHED))
    assert "immutable" in str(excinfo.value)


# ── 2. Foundation: effective dating ─────────────────────────────────────────────


def test_half_open_windows_do_not_double_cover_the_boundary():
    """A price ending at midnight and its successor starting at midnight must not both apply
    for one instant — which closed intervals would guarantee."""
    dates = EffectiveDatingService()
    first, second = EffectiveRange(0.0, 100.0), EffectiveRange(100.0, 200.0)
    assert first.covers(99.999) and not first.covers(100.0)
    assert second.covers(100.0)
    assert not dates.overlaps(first, second)


def test_overlap_detection():
    dates = EffectiveDatingService()
    assert dates.overlaps(EffectiveRange(0.0, 100.0), EffectiveRange(50.0, 150.0))
    assert dates.overlaps(EffectiveRange(0.0, None), EffectiveRange(500.0, None))
    assert not dates.overlaps(EffectiveRange(0.0, 10.0), EffectiveRange(20.0, 30.0))


def test_scheduled_live_and_expired_are_distinguishable():
    dates = EffectiveDatingService()
    future = EffectiveRange(1000.0, 2000.0)
    assert dates.is_scheduled(future, at=500.0)
    assert dates.is_live(future, at=1500.0)
    assert dates.is_expired(future, at=2500.0)
    assert not dates.is_live(future, at=2000.0)  # end is exclusive


def test_an_inverted_window_is_invalid():
    assert not EffectiveDatingService().is_valid(EffectiveRange(100.0, 50.0))
    assert EffectiveDatingService().is_valid(EffectiveRange(100.0, None))


def test_historical_lookup_returns_the_price_of_that_day():
    prices = [
        _price(version=1, amount=rupees(10_000), frm=0.0, until=1000.0),
        _price(version=2, amount=rupees(12_000), frm=1000.0),
    ]
    dates = EffectiveDatingService()
    assert dates.resolve(prices, at=500.0).amount == rupees(10_000)
    assert dates.resolve(prices, at=1500.0).amount == rupees(12_000)
    assert dates.resolve(prices, at=-1.0) is None


def test_same_code_same_version_overlap_is_a_collision():
    collisions = EffectiveDatingService().collisions(
        [_price(version=1, frm=0.0, until=200.0), _price(version=1, frm=100.0, until=300.0)]
    )
    assert len(collisions) == 1


def test_different_versions_of_a_code_may_overlap():
    """That is exactly how a superseding price is expressed; resolution picks the highest."""
    assert (
        EffectiveDatingService().collisions(
            [_price(version=1, frm=0.0), _price(version=2, frm=0.0)]
        )
        == ()
    )


def test_overlapping_windows_are_reported_by_validation():
    diagnostics = PricingValidator().validate_book(
        _book(prices=[_price(version=1, frm=0.0, until=200.0), _price(version=1, frm=100.0)])
    )
    assert any(f.code.value == "overlapping_effective_range" for f in diagnostics.errors)


# ── 3. Foundation: validation ───────────────────────────────────────────────────


def test_validation_never_raises_and_reports_every_finding():
    diagnostics = PricingValidator().validate_book(
        PricingBook(id="", org_id="", code="", display_name="", currency=INR, revision=0)
    )
    assert len(diagnostics.errors) >= 4
    assert diagnostics.findings == tuple(sorted(diagnostics.findings))


def test_a_price_in_the_wrong_currency_is_rejected():
    book = _book(prices=[replace(_price(), currency=Currency.USD)])
    diagnostics = PricingValidator().validate_book(book)
    assert any(f.code.value == "currency_mismatch" for f in diagnostics.errors)


def test_duplicate_price_versions_are_detected():
    diagnostics = PricingValidator().validate_book(
        _book(prices=[_price(version=1, frm=0.0, until=10.0), _price(version=1, frm=50.0)])
    )
    assert any(f.code.value == "duplicate_price_code" for f in diagnostics.errors)


def test_negative_money_is_rejected():
    diagnostics = PricingValidator().validate_book(_book(prices=[_price(amount=Money(-100, INR))]))
    assert any(f.code.value == "negative_amount" for f in diagnostics.errors)


def test_tier_ordering_and_contiguity_are_validated():
    """Delegated to the M9.2 validator — one definition of a well-formed tier, not two."""
    bad = _price(
        shape=PriceShape.TIERED_GRADUATED,
        amount=None,
        meter_code="minutes",
        tiers=(
            PriceTier(up_to=100, unit_price=rupees(1)),
            PriceTier(up_to=50, unit_price=rupees(2)),  # goes backwards
        ),
    )
    diagnostics = PricingValidator().validate_price(bad, currency=INR)
    assert diagnostics.has_errors


def test_bounded_volume_tiers_warn_that_usage_beyond_them_is_unpriced():
    """A warning, not an error: bounded tiers are a real (if usually unintended) pricing
    choice, and M9.2 already made that call — this asserts it survived."""
    bounded = _price(
        shape=PriceShape.VOLUME,
        amount=None,
        meter_code="minutes",
        tiers=(PriceTier(up_to=100, unit_price=rupees(1)),),
    )
    diagnostics = PricingValidator().validate_price(bounded, currency=INR)
    assert not diagnostics.has_errors
    assert any("unpriced" in f.remediation or "unpriced" in f.message for f in diagnostics.warnings)


@pytest.mark.parametrize("bp", [0, -1, 10_001])
def test_percentage_discounts_must_be_sane_basis_points(bp):
    assert PricingValidator().validate_discount(_discount(bp=bp)).has_errors


def test_a_discount_carries_either_basis_points_or_an_amount_never_both():
    both = _discount(bp=2500, amount=rupees(100))
    neither = _discount(bp=0)
    for policy in (both, neither):
        diagnostics = PricingValidator().validate_discount(policy)
        assert any(f.code.value == "invalid_discount" for f in diagnostics.errors)


def test_a_volume_discount_may_be_expressed_as_a_percentage():
    """`kind` says *why* a discount exists; basis points versus a fixed sum says *how much*.
    Tying the shape to the reason would force volume discounts into the wrong form."""
    policy = replace(_discount(bp=1500), kind=DiscountKind.VOLUME, min_quantity=100)
    assert not PricingValidator().validate_discount(policy).has_errors
    assert policy.is_percentage and policy.reduction(rupees(10_000)) == rupees(1_500)


def test_a_negative_discount_is_rejected():
    policy = replace(_discount(bp=0), kind=DiscountKind.FIXED_AMOUNT, amount=Money(-1, INR))
    assert any(
        f.code.value == "negative_amount"
        for f in PricingValidator().validate_discount(policy).errors
    )


def test_volume_and_commitment_discounts_must_declare_their_gate():
    volume = replace(_discount(bp=1000), kind=DiscountKind.VOLUME, min_quantity=0)
    commitment = replace(_discount(bp=1000), kind=DiscountKind.COMMITMENT, min_commitment=None)
    assert PricingValidator().validate_discount(volume).has_errors
    assert PricingValidator().validate_discount(commitment).has_errors


def test_two_exclusive_promotions_in_one_campaign_conflict():
    first = Promotion(
        id="pmo_1",
        org_id="org_1",
        code="a",
        display_name="A",
        discount=_discount(code="a"),
        effective=EffectiveRange(0.0, 100.0),
        campaign="diwali",
        exclusive=True,
    )
    second = replace(first, id="pmo_2", code="b", effective=EffectiveRange(50.0, 150.0))
    diagnostics = PricingValidator().validate_promotion(second, known=[first])
    assert any(f.code.value == "promotion_conflict" for f in diagnostics.errors)


def test_a_single_use_coupon_cannot_allow_several_redemptions():
    coupon = Coupon(
        id="cpn_1",
        org_id="org_1",
        code="x",
        display_name="X",
        discount=_discount(code="x"),
        single_use=True,
        max_redemptions=5,
    )
    assert any(
        f.code.value == "coupon_conflict" for f in PricingValidator().validate_coupon(coupon).errors
    )


def test_an_override_must_target_a_price_the_book_actually_has():
    book = _book(prices=[_price("pro")], overrides=(PriceOverride("ghost", amount=rupees(1)),))
    diagnostics = PricingValidator().validate_book(book)
    assert any(f.code.value == "unknown_price" for f in diagnostics.errors)


def test_pricing_completeness_names_what_is_unpriced():
    diagnostics = PricingValidator().validate_pricing(
        books=[_book(prices=[_price("pro")])], priced_codes=frozenset({"pro", "enterprise"})
    )
    missing = [f for f in diagnostics.errors if f.code.value == "incomplete_pricing"]
    assert len(missing) == 1 and "enterprise" in missing[0].message


def test_an_empty_catalog_version_pin_set_rejects_rather_than_skips():
    """An **empty** set means "this org has published no catalog", which must reject the pin —
    the distinction an empty-collection falsiness test silently loses."""
    book = _book(catalog_version=3)
    assert PricingValidator().validate_book(book, known_catalog_versions=frozenset()).has_errors
    assert not PricingValidator().validate_book(book, known_catalog_versions=None).has_errors
    assert (
        not PricingValidator().validate_book(book, known_catalog_versions=frozenset({3})).has_errors
    )


def test_a_dangling_discount_reference_is_rejected():
    diagnostics = PricingValidator().validate_pricing(
        books=[_book(prices=[_price()], discount_codes=("ghost",))], discounts=[_discount()]
    )
    assert any(f.code.value == "discount_conflict" for f in diagnostics.errors)


def test_pricing_with_no_books_cannot_be_published():
    assert PricingValidator().validate_pricing(books=[]).has_errors


# ── 4. Foundation: discounts ────────────────────────────────────────────────────


def test_percentage_reduction_uses_integer_basis_points():
    assert _discount(bp=2500).reduction(rupees(10_000)) == rupees(2_500)
    assert _discount(bp=3333).reduction(Money(10_000, INR)) == Money(3_333, INR)
    # exact integer maths, HALF_UP on the boundary — never a float
    assert _discount(bp=1).reduction(Money(15_000, INR)) == Money(2, INR)


def test_a_discount_can_never_invert_a_price():
    """A discount that could take more than the price would be a credit, and credits are a
    different domain."""
    huge = replace(_discount(bp=0), kind=DiscountKind.FIXED_AMOUNT, amount=rupees(999_999))
    assert huge.reduction(rupees(100)) == rupees(100)


def test_discounts_apply_in_priority_then_code_order():
    engine = DiscountEngine()
    net, applied = engine.apply(
        rupees(10_000),
        [
            _discount(code="b", bp=1000, priority=10),
            _discount(code="a", bp=1000, priority=10),
            _discount(code="c", bp=1000, priority=5),
        ],
    )
    assert [a.code for a in applied] == ["c", "a", "b"]
    assert net == rupees(7_000)


def test_each_reduction_comes_off_the_list_price_not_the_running_balance():
    """Two 25% discounts are worth 50%, not 43.75% — so the order they are listed in never
    silently changes the total."""
    net, applied = DiscountEngine().apply(
        rupees(10_000), [_discount(code="a", bp=2500), _discount(code="b", bp=2500)]
    )
    assert net == rupees(5_000)
    assert all(a.reduction == rupees(2_500) for a in applied)


def test_a_non_stackable_discount_ends_the_chain():
    net, applied = DiscountEngine().apply(
        rupees(10_000),
        [
            _discount(code="exclusive", bp=3000, priority=1, stackable=False),
            _discount(code="other", bp=1000, priority=2),
        ],
    )
    assert [a.code for a in applied] == ["exclusive"]
    assert net == rupees(7_000)


def test_discount_stacking_is_floored_at_zero():
    net, _ = DiscountEngine().apply(
        rupees(100), [_discount(code="a", bp=8000), _discount(code="b", bp=8000)]
    )
    assert net == Money(0, INR) and not net.is_negative


def test_eligibility_gates_on_quantity_and_commitment():
    engine = DiscountEngine()
    volume = replace(_discount(code="v", bp=1000), kind=DiscountKind.VOLUME, min_quantity=100)
    assert (
        engine.eligible(policies=[volume], price_code="pro", currency=INR, at=0.0, quantity=50)
        == ()
    )
    assert (
        len(
            engine.eligible(policies=[volume], price_code="pro", currency=INR, at=0.0, quantity=100)
        )
        == 1
    )

    commit = replace(
        _discount(code="c", bp=1000), kind=DiscountKind.COMMITMENT, min_commitment=rupees(100_000)
    )
    assert (
        engine.eligible(
            policies=[commit], price_code="pro", currency=INR, at=0.0, commitment=rupees(50_000)
        )
        == ()
    )
    assert (
        len(
            engine.eligible(
                policies=[commit],
                price_code="pro",
                currency=INR,
                at=0.0,
                commitment=rupees(200_000),
            )
        )
        == 1
    )


def test_an_expired_discount_is_not_eligible():
    expired = _discount(effective=EffectiveRange(0.0, 100.0))
    engine = DiscountEngine()
    assert len(engine.eligible(policies=[expired], price_code="pro", currency=INR, at=50.0)) == 1
    assert engine.eligible(policies=[expired], price_code="pro", currency=INR, at=150.0) == ()


def test_a_discount_scoped_to_another_segment_is_not_eligible():
    scoped = _discount(scope=PricingScope(customer_tiers=(CustomerTier.ENTERPRISE,)))
    engine = DiscountEngine()
    assert (
        engine.eligible(
            policies=[scoped],
            price_code="pro",
            currency=INR,
            at=0.0,
            customer_tier=CustomerTier.STANDARD,
        )
        == ()
    )
    assert (
        len(
            engine.eligible(
                policies=[scoped],
                price_code="pro",
                currency=INR,
                at=0.0,
                customer_tier=CustomerTier.ENTERPRISE,
            )
        )
        == 1
    )


def test_a_discount_targeting_other_prices_is_not_eligible():
    targeted = _discount(price_codes=("enterprise",))
    assert (
        DiscountEngine().eligible(policies=[targeted], price_code="pro", currency=INR, at=0.0) == ()
    )


# ── 5. Foundation: resolution ───────────────────────────────────────────────────


def _frozen(book: PricingBook, version: int = 1):
    return PricingAssembler().freeze_book(
        book, version_id=f"pbv_{book.code}_{version}", version=version, published_at=0.0
    )


def test_resolution_is_deterministic():
    books = [_frozen(_book(prices=[_price()]))]
    request = PriceResolutionRequest(org_id="org_1", price_code="pro", currency=INR, at=10.0)
    first = PriceResolver().resolve(request, books=books, discounts=[_discount()])
    second = PriceResolver().resolve(request, books=books, discounts=[_discount()])
    assert first == second


def test_resolution_returns_none_when_nothing_prices_the_request():
    """An unpriced code is a fact the caller handles, never a zero this layer invents."""
    resolved = PriceResolver().resolve(
        PriceResolutionRequest(org_id="org_1", price_code="ghost", currency=INR, at=0.0),
        books=[_frozen(_book(prices=[_price()]))],
    )
    assert resolved is None


def test_currency_selects_the_book():
    inr = _frozen(_book(code="inr", prices=[_price()]))
    usd = _frozen(
        replace(
            _book(code="usd", prices=[replace(_price(), currency=Currency.USD)]),
            currency=Currency.USD,
        )
    )
    resolved = PriceResolver().resolve(
        PriceResolutionRequest(org_id="org_1", price_code="pro", currency=Currency.USD, at=0.0),
        books=[inr, usd],
    )
    assert resolved.price_book_code == "usd" and resolved.currency is Currency.USD


def test_the_most_specific_scope_wins():
    """Adding an India-only enterprise book must never silently change anyone else's price."""
    general = _frozen(_book(code="global", prices=[_price(amount=rupees(10_000))]))
    regional = _frozen(
        _book(
            code="india",
            prices=[_price(amount=rupees(8_000))],
            scope=PricingScope(region=Region.IN, customer_tiers=(CustomerTier.ENTERPRISE,)),
        )
    )
    resolver = PriceResolver()
    india = resolver.resolve(
        PriceResolutionRequest(
            org_id="org_1",
            price_code="pro",
            currency=INR,
            at=0.0,
            region=Region.IN,
            customer_tier=CustomerTier.ENTERPRISE,
        ),
        books=[general, regional],
    )
    everyone_else = resolver.resolve(
        PriceResolutionRequest(org_id="org_1", price_code="pro", currency=INR, at=0.0),
        books=[general, regional],
    )
    assert india.price_book_code == "india" and india.list_amount == rupees(8_000)
    assert everyone_else.price_book_code == "global" and everyone_else.list_amount == rupees(10_000)


def test_resolution_respects_the_instant():
    book = _frozen(
        _book(
            prices=[
                _price(version=1, amount=rupees(10_000), frm=0.0, until=1000.0),
                _price(version=2, amount=rupees(12_000), frm=1000.0),
            ]
        )
    )
    resolver = PriceResolver()
    old = resolver.resolve(
        PriceResolutionRequest(org_id="org_1", price_code="pro", currency=INR, at=500.0),
        books=[book],
    )
    new = resolver.resolve(
        PriceResolutionRequest(org_id="org_1", price_code="pro", currency=INR, at=5000.0),
        books=[book],
    )
    assert old.list_amount == rupees(10_000) and new.list_amount == rupees(12_000)


def test_an_account_override_beats_a_book_wide_one():
    book = _frozen(
        _book(
            prices=[_price(amount=rupees(10_000))],
            overrides=(
                PriceOverride("pro", amount=rupees(9_000)),
                PriceOverride("pro", amount=rupees(6_000), account_id="bac_1"),
            ),
        )
    )
    resolver = PriceResolver()
    named = resolver.resolve(
        PriceResolutionRequest(
            org_id="org_1", price_code="pro", currency=INR, at=0.0, account_id="bac_1"
        ),
        books=[book],
    )
    other = resolver.resolve(
        PriceResolutionRequest(
            org_id="org_1", price_code="pro", currency=INR, at=0.0, account_id="bac_2"
        ),
        books=[book],
    )
    assert named.list_amount == rupees(6_000) and named.override_applied
    assert other.list_amount == rupees(9_000)


def test_a_request_override_beats_anything_authored_in_the_book():
    book = _frozen(
        _book(
            prices=[_price(amount=rupees(10_000))],
            overrides=(PriceOverride("pro", amount=rupees(9_000)),),
        )
    )
    resolved = PriceResolver().resolve(
        PriceResolutionRequest(
            org_id="org_1",
            price_code="pro",
            currency=INR,
            at=0.0,
            override=PriceOverride("pro", amount=rupees(5_000), contract_id="ctr_1"),
        ),
        books=[book],
    )
    assert resolved.list_amount == rupees(5_000)
    assert "override=request" in " ".join(resolved.resolution_path)


def test_a_coupon_applies_only_when_presented():
    book = _frozen(_book(prices=[_price()], discount_codes=()))
    coupon = Coupon(
        id="cpn_1",
        org_id="org_1",
        code="WELCOME20",
        display_name="Welcome",
        discount=_discount(code="WELCOME20", bp=2000, stackable=False),
    )
    resolver = PriceResolver()
    without = resolver.resolve(
        PriceResolutionRequest(org_id="org_1", price_code="pro", currency=INR, at=0.0),
        books=[book],
        coupons=[coupon],
    )
    with_coupon = resolver.resolve(
        PriceResolutionRequest(
            org_id="org_1", price_code="pro", currency=INR, at=0.0, coupon_code="WELCOME20"
        ),
        books=[book],
        coupons=[coupon],
    )
    assert not without.discounts
    assert [d.source for d in with_coupon.discounts] == ["coupon"]
    assert with_coupon.net_amount == rupees(8_000)


def test_an_exclusive_promotion_is_the_offer_not_one_of_them():
    book = _frozen(_book(prices=[_price()]))
    promotion = Promotion(
        id="pmo_1",
        org_id="org_1",
        code="diwali",
        display_name="Diwali",
        discount=_discount(code="diwali", bp=3000, priority=1),
        exclusive=True,
    )
    resolved = PriceResolver().resolve(
        PriceResolutionRequest(
            org_id="org_1",
            price_code="pro",
            currency=INR,
            at=0.0,
            promotion_codes=("diwali",),
        ),
        books=[book],
        discounts=[_discount(code="launch", bp=1000)],
        promotions=[promotion],
    )
    assert [d.code for d in resolved.discounts] == ["diwali"]
    assert resolved.net_amount == rupees(7_000)


def test_tiered_prices_discount_per_band():
    tiered = _price(
        shape=PriceShape.TIERED_GRADUATED,
        amount=None,
        meter_code="minutes",
        tiers=(
            PriceTier(up_to=1000, unit_price=Money(100, INR)),
            PriceTier(up_to=None, unit_price=Money(50, INR)),
        ),
    )
    resolved = PriceResolver().resolve(
        PriceResolutionRequest(org_id="org_1", price_code="pro", currency=INR, at=0.0),
        books=[_frozen(_book(prices=[tiered]))],
        discounts=[_discount(bp=2000)],
    )
    assert resolved.list_amount is None  # tiered prices carry their money in the bands
    assert [t.unit_price.minor for t in resolved.net_tiers] == [80, 40]


def test_resolution_path_explains_the_answer():
    resolved = PriceResolver().resolve(
        PriceResolutionRequest(org_id="org_1", price_code="pro", currency=INR, at=0.0),
        books=[_frozen(_book(prices=[_price()]))],
        discounts=[_discount()],
    )
    joined = " ".join(resolved.resolution_path)
    assert "book=public@v1" in joined and "price=pro@v1" in joined and "discount=launch" in joined


def test_quantity_is_an_eligibility_input_and_never_a_multiplier():
    """The boundary between pricing and rating, asserted rather than intended: changing the
    quantity changes *which* discounts qualify, never the price itself."""
    book = _frozen(_book(prices=[_price(amount=rupees(10_000))]))
    resolver = PriceResolver()
    one = resolver.resolve(
        PriceResolutionRequest(org_id="org_1", price_code="pro", currency=INR, at=0.0, quantity=1),
        books=[book],
    )
    thousand = resolver.resolve(
        PriceResolutionRequest(
            org_id="org_1", price_code="pro", currency=INR, at=0.0, quantity=1000
        ),
        books=[book],
    )
    assert one.net_amount == thousand.net_amount == rupees(10_000)


def test_a_resolved_price_carries_no_charge_no_period_and_no_tax():
    resolved = PriceResolver().resolve(
        PriceResolutionRequest(org_id="org_1", price_code="pro", currency=INR, at=0.0),
        books=[_frozen(_book(prices=[_price()]))],
    )
    fields = set(resolved.__slots__)
    for forbidden in ("quantity", "total", "tax", "period", "subtotal", "charge", "invoice"):
        assert not any(forbidden in f for f in fields), forbidden


# ── 6. Foundation: snapshots + rollback ─────────────────────────────────────────


def test_publishing_freezes_a_self_contained_price_book_version():
    factory, _ = _factory()
    frozen = factory.publish_book(_book(prices=[_price()]), version=1)
    assert frozen.price_count == 1 and frozen.prices[0].amount == rupees(10_000)
    assert frozen.currency is INR and frozen.code == "public"


def test_snapshots_are_deterministic_for_identical_input():
    book = _book(prices=[_price("b"), _price("a")])
    assembler = PricingAssembler()
    first = assembler.freeze_book(book, version_id="pbv_1", version=1, published_at=0.0)
    second = assembler.freeze_book(book, version_id="pbv_1", version=1, published_at=0.0)
    assert first == second
    assert [p.code for p in first.prices] == ["a", "b"]  # sorted on the way in


def test_a_published_version_is_immutable():
    frozen = _frozen(_book(prices=[_price()]))
    with pytest.raises(Exception):  # noqa: B017 — frozen dataclass, any attribute
        frozen.prices = ()


def test_rollback_publishes_forward_and_records_provenance():
    assembler = PricingAssembler()
    source = assembler.assemble(
        snapshot_id="pvr_1",
        org_id="org_1",
        version=1,
        published_at=0.0,
        books=[_frozen(_book(prices=[_price()]))],
    )
    rolled = assembler.rollback(source, snapshot_id="pvr_3", version=3, published_at=9.0)
    assert rolled.version == 3 and rolled.rolled_back_from == 1 and rolled.is_rollback
    assert rolled.books == source.books
    assert source.version == 1  # the source is untouched


def test_rollback_cannot_rewind_the_version_number():
    assembler = PricingAssembler()
    source = assembler.assemble(
        snapshot_id="pvr_5",
        org_id="org_1",
        version=5,
        published_at=0.0,
        books=[_frozen(_book(prices=[_price()]))],
    )
    with pytest.raises(BillingConflict):
        assembler.rollback(source, snapshot_id="pvr_x", version=3, published_at=1.0)


def test_a_snapshot_rollback_source_must_be_earlier():
    assembler = PricingAssembler()
    snapshot = assembler.assemble(
        snapshot_id="pvr_1",
        org_id="org_1",
        version=1,
        published_at=0.0,
        books=[_frozen(_book(prices=[_price()]))],
    )
    broken = replace(snapshot, rolled_back_from=5)
    assert PricingValidator().validate_snapshot(broken).has_errors


def test_publishing_an_invalid_book_fails_before_the_version_exists():
    factory, emitter = _factory()
    with pytest.raises(BillingValidationError):
        factory.publish_book(_book(prices=[replace(_price(), amount=Money(-1, INR))]), version=1)
    assert emitter.events == ()  # nothing was emitted; nothing was frozen


# ── 7. Application over a real database ─────────────────────────────────────────


def _seed(app, *, publish=True, code="public", amount=10_000, **book_kw) -> None:
    app.commands.dispatch(
        CreatePriceBook(
            **ORG, code=code, display_name=code.title(), currency=INR, effective_from=0.0, **book_kw
        )
    )
    app.commands.dispatch(
        CreatePrice(
            **ORG,
            book_code=code,
            code="pro",
            shape=PriceShape.FLAT_RECURRING,
            amount=rupees(amount),
            interval=BillingInterval.MONTHLY,
            effective_from=0.0,
        )
    )
    if publish:
        app.commands.dispatch(PublishPricingBook(**ORG, code=code))


def test_price_book_lifecycle_through_the_application(app):
    _seed(app, publish=False)
    book = app.queries.dispatch(GetPricingBook(**ORG, code="public"))
    assert book.state == "draft" and book.is_editable

    version = app.commands.dispatch(PublishPricingBook(**ORG, code="public"))
    assert version.version == 1 and version.price_count == 1

    book = app.queries.dispatch(GetPricingBook(**ORG, code="public"))
    assert book.state == "published" and not book.is_editable
    assert book.published_version == 1
    assert book.allowed_triggers == ("archive",)

    app.commands.dispatch(ArchivePriceBook(**ORG, code="public"))
    assert app.queries.dispatch(GetPricingBook(**ORG, code="public")).state == "archived"
    app.commands.dispatch(RestorePriceBook(**ORG, code="public"))
    assert app.queries.dispatch(GetPricingBook(**ORG, code="public")).state == "published"


def test_illegal_transition_is_rejected_through_the_application(app):
    _seed(app, publish=False)
    with pytest.raises(InvalidBillingTransition):
        app.commands.dispatch(ArchivePriceBook(**ORG, code="public"))


def test_a_published_book_refuses_new_prices(app):
    _seed(app)
    with pytest.raises(BillingConflict):
        app.commands.dispatch(
            CreatePrice(
                **ORG, book_code="public", code="extra", shape=PriceShape.ONE_OFF, amount=rupees(1)
            )
        )


def test_a_published_book_refuses_price_amendments(app):
    _seed(app)
    with pytest.raises(BillingConflict):
        app.commands.dispatch(
            UpdateDraftPrice(**ORG, book_code="public", code="pro", amount=rupees(1))
        )


def test_a_draft_price_can_be_amended(app):
    _seed(app, publish=False)
    app.commands.dispatch(
        UpdateDraftPrice(**ORG, book_code="public", code="pro", amount=rupees(12_000))
    )
    book = app.queries.dispatch(GetPricingBook(**ORG, code="public"))
    assert book.prices[0].amount.minor == 1_200_000


def test_repricing_creates_a_new_revision_and_keeps_the_old_one(app):
    """The whole immutability story in one test: the book an old invoice was rated against is
    still there, unchanged, after a reprice."""
    _seed(app)
    app.commands.dispatch(DuplicatePriceBook(**ORG, source_code="public"))
    app.commands.dispatch(
        UpdateDraftPrice(**ORG, book_code="public", code="pro", amount=rupees(12_000))
    )
    app.commands.dispatch(PublishPricingBook(**ORG, code="public"))

    v1 = app.queries.dispatch(GetPricingBook(**ORG, code="public", revision=1))
    v2 = app.queries.dispatch(GetPricingBook(**ORG, code="public", revision=2))
    assert v1.prices[0].amount.minor == 1_000_000  # untouched
    assert v2.prices[0].amount.minor == 1_200_000
    history = app.queries.dispatch(PriceVersionHistory(**ORG, code="public"))
    assert sorted(v.version for v in history) == [1, 2]


def test_a_price_book_version_can_never_be_republished(app):
    _seed(app)
    wired_repo = app.context.pricing.book_versions
    frozen = wired_repo.latest("org_1", "public")
    with pytest.raises(ImmutabilityViolation):
        wired_repo.publish(frozen)


def test_duplicate_price_book_code_is_rejected(app):
    _seed(app, publish=False)
    with pytest.raises(BillingConflict):
        app.commands.dispatch(
            CreatePriceBook(**ORG, code="public", display_name="Again", currency=INR)
        )


def test_resolution_through_the_application(app):
    _seed(app)
    app.commands.dispatch(
        CreateDiscountPolicy(
            **ORG, code="launch", display_name="Launch", percentage_bp=2500, effective_from=0.0
        )
    )
    resolved = app.queries.dispatch(
        ResolvePrice(
            **ORG,
            request=PriceResolutionRequest(
                org_id="org_1", price_code="pro", currency=INR, at=100.0
            ),
        )
    )
    assert resolved.list_amount.minor == 1_000_000
    assert resolved.net_amount.minor == 750_000
    assert resolved.total_discount.minor == 250_000


def test_resolution_of_an_unpriced_code_is_none(app):
    _seed(app)
    assert (
        app.queries.dispatch(
            ResolvePrice(
                **ORG,
                request=PriceResolutionRequest(
                    org_id="org_1", price_code="ghost", currency=INR, at=0.0
                ),
            )
        )
        is None
    )


def test_publishing_pricing_freezes_a_version(app):
    _seed(app)
    snapshot = app.commands.dispatch(PublishPricing(**ORG, notes="first"))
    assert snapshot.version == 1 and snapshot.price_count == 1
    view = app.queries.dispatch(GetPricingVersion(**ORG))
    assert view.version == 1 and view.book_codes == ("public@1",)


def test_pricing_versions_increment_and_history_is_readable(app):
    _seed(app)
    for _ in range(3):
        app.commands.dispatch(PublishPricing(**ORG))
    history = app.queries.dispatch(PricingVersionHistory(**ORG))
    assert [v.version for v in history] == [3, 2, 1]


def test_republishing_a_pricing_version_is_impossible(app):
    _seed(app)
    app.commands.dispatch(PublishPricing(**ORG))
    repo = app.context.pricing.versions
    with pytest.raises(ImmutabilityViolation):
        repo.publish(repo.latest("org_1"))


def test_rolling_back_pricing_publishes_forward(app):
    _seed(app)
    app.commands.dispatch(PublishPricing(**ORG, notes="v1"))
    app.commands.dispatch(PublishPricing(**ORG, notes="v2"))
    rolled = app.commands.dispatch(RollbackPricing(**ORG, to_version=1))
    assert rolled.version == 3 and rolled.rolled_back_from == 1
    # the versions in between remain readable forever
    assert app.queries.dispatch(GetPricingVersion(**ORG, version=2)) is not None


def test_rolling_back_to_an_unknown_version_is_not_found(app):
    from billing.foundation import BillingNotFound

    _seed(app)
    with pytest.raises(BillingNotFound):
        app.commands.dispatch(RollbackPricing(**ORG, to_version=99))


def test_a_historical_lookup_reads_the_frozen_snapshot(app):
    """Repricing after a snapshot must not change what that snapshot says."""
    _seed(app)
    app.commands.dispatch(PublishPricing(**ORG, notes="v1"))
    app.commands.dispatch(DuplicatePriceBook(**ORG, source_code="public"))
    app.commands.dispatch(
        UpdateDraftPrice(**ORG, book_code="public", code="pro", amount=rupees(20_000))
    )
    app.commands.dispatch(PublishPricingBook(**ORG, code="public"))

    historical = app.queries.dispatch(
        ResolvePrice(
            **ORG,
            request=PriceResolutionRequest(
                org_id="org_1", price_code="pro", currency=INR, at=100.0, pricing_version=1
            ),
        )
    )
    live = app.queries.dispatch(
        ResolvePrice(
            **ORG,
            request=PriceResolutionRequest(
                org_id="org_1", price_code="pro", currency=INR, at=100.0
            ),
        )
    )
    assert historical.list_amount.minor == 1_000_000
    assert live.list_amount.minor == 2_000_000


def test_publishing_pricing_pins_a_catalog_version_without_republishing_it(app):
    """Catalog and pricing version independently — the property that lets a price change
    without touching the product."""
    from billing.application import CreateProduct, GetCatalogVersion, PublishCatalog, PublishProduct

    app.commands.dispatch(CreateProduct(**ORG, code="voice", display_name="Voice"))
    app.commands.dispatch(PublishProduct(**ORG, code="voice"))
    catalog = app.commands.dispatch(PublishCatalog(**ORG))

    _seed(app)
    snapshot = app.commands.dispatch(PublishPricing(**ORG, catalog_version=catalog.version))
    assert snapshot.catalog_version == catalog.version
    # publishing pricing did not move the catalog
    assert app.queries.dispatch(GetCatalogVersion(**ORG)).version == catalog.version


def test_a_book_pinned_to_an_unknown_catalog_version_is_rejected(app):
    with pytest.raises(BillingValidationError):
        app.commands.dispatch(
            CreatePriceBook(**ORG, code="bad", display_name="Bad", currency=INR, catalog_version=42)
        )


def test_promotions_and_coupons_flow_through(app):
    _seed(app)
    app.commands.dispatch(
        CreatePromotion(
            **ORG,
            code="diwali",
            display_name="Diwali",
            percentage_bp=3000,
            campaign="festive",
            effective_from=0.0,
        )
    )
    app.commands.dispatch(
        CreateCoupon(
            **ORG, code="WELCOME20", display_name="Welcome", percentage_bp=2000, effective_from=0.0
        )
    )
    assert [p.code for p in app.queries.dispatch(ListPromotions(**ORG))] == ["diwali"]
    assert [c.code for c in app.queries.dispatch(ListCoupons(**ORG))] == ["WELCOME20"]
    assert app.queries.dispatch(ListPromotions(**ORG, live_at=50.0))


def test_search_is_deterministic_and_filtered(app):
    _seed(app, code="public")
    _seed(app, code="partner")
    result = app.queries.dispatch(SearchPriceBooks(**ORG))
    assert [b.code for b in result.books] == ["partner", "public"]
    assert result.total == 2

    filtered = app.queries.dispatch(
        SearchPriceBooks(**ORG, query=PriceBookSearchQuery(text="part"))
    )
    assert [b.code for b in filtered.books] == ["partner"]

    paged = app.queries.dispatch(SearchPriceBooks(**ORG, query=PriceBookSearchQuery(limit=1)))
    assert len(paged.books) == 1 and paged.total == 2  # the page shrinks, the total does not


def test_every_command_appends_pricing_activity(app):
    _seed(app)
    app.commands.dispatch(
        CreateDiscountPolicy(**ORG, code="d", display_name="D", percentage_bp=500)
    )
    app.commands.dispatch(PublishPricing(**ORG))
    actions = {a.action for a in app.queries.dispatch(PricingHistory(**ORG))}
    assert actions == {
        "price_book.created",
        "price.created",
        "price_book.published",
        "discount.created",
        "pricing.published",
    }


def test_activity_can_be_read_per_subject(app):
    _seed(app)
    entries = app.queries.dispatch(PricingHistory(**ORG, subject_code="public"))
    assert {e.action for e in entries} == {"price_book.created", "price_book.published"}


def test_a_failed_command_leaves_no_activity_behind(app):
    """History that could disagree with prices would be worse than no history at all."""
    before = len(app.queries.dispatch(PricingHistory(**ORG)))
    with pytest.raises(BillingValidationError):
        app.commands.dispatch(CreatePriceBook(**ORG, code="", display_name="", currency=INR))
    assert len(app.queries.dispatch(PricingHistory(**ORG))) == before


# ── 8. isolation, authorization, durability ─────────────────────────────────────


def test_cross_org_isolation(app):
    _seed(app)
    other = dict(actor_id="usr_2", org_id="org_2")
    assert app.queries.dispatch(GetPricingBook(**other, code="public")) is None
    assert app.queries.dispatch(SearchPriceBooks(**other)).total == 0
    assert app.queries.dispatch(GetPricingVersion(**other)) is None


def test_two_orgs_may_hold_the_same_price_book_code(app):
    _seed(app)
    other = dict(actor_id="usr_2", org_id="org_2")
    app.commands.dispatch(
        CreatePriceBook(**other, code="public", display_name="Theirs", currency=Currency.USD)
    )
    assert app.queries.dispatch(GetPricingBook(**ORG, code="public")).currency == "INR"
    assert app.queries.dispatch(GetPricingBook(**other, code="public")).currency == "USD"


def test_resolution_never_crosses_an_org(app):
    _seed(app)
    other = dict(actor_id="usr_2", org_id="org_2")
    assert (
        app.queries.dispatch(
            ResolvePrice(
                **other,
                request=PriceResolutionRequest(
                    org_id="org_2", price_code="pro", currency=INR, at=0.0
                ),
            )
        )
        is None
    )


def test_every_pricing_command_and_query_is_authorized():
    from billing.application import ALL_PRICING_COMMANDS, ALL_PRICING_QUERIES

    wired = build_persistent_billing_application(
        ids=SequentialIdGenerator(), clock=FixedClock(), authorizer=DenyAllAuthorizer()
    )
    app = wired.application
    for message in ALL_PRICING_COMMANDS:
        with pytest.raises(BillingAuthorizationError):
            app.commands.dispatch(message(**ORG))
    for message in ALL_PRICING_QUERIES:
        with pytest.raises(BillingAuthorizationError):
            app.queries.dispatch(message(**ORG))
    wired.database.close()


def test_prices_and_history_survive_a_restart():
    """A second Application over the same storage proves the data landed in the database
    rather than in some object's memory."""
    database = BillingDatabase("sqlite://")
    first = build_persistent_billing_application(
        database=database,
        ids=SequentialIdGenerator(),
        clock=FixedClock(),
        authorizer=AllowAllAuthorizer(),
    )
    _seed(first.application)
    first.application.commands.dispatch(PublishPricing(**ORG, notes="kept"))

    second = build_persistent_billing_application(
        database=database,
        create_all=False,
        ids=SequentialIdGenerator(),
        clock=FixedClock(),
        authorizer=AllowAllAuthorizer(),
    )
    app = second.application
    assert app.queries.dispatch(GetPricingBook(**ORG, code="public")).published_version == 1
    assert app.queries.dispatch(GetPricingVersion(**ORG)).notes == "kept"
    assert len(app.queries.dispatch(PricingHistory(**ORG))) == 4
    resolved = app.queries.dispatch(
        ResolvePrice(
            **ORG,
            request=PriceResolutionRequest(org_id="org_1", price_code="pro", currency=INR, at=10.0),
        )
    )
    assert resolved.list_amount.minor == 1_000_000
    database.close()


def test_pricing_tables_are_created_by_the_migration_chain():
    from sqlalchemy import inspect

    from billing.persistence.migrations import MIGRATIONS, v0017_billing_pricing
    from billing.persistence.pricing_models import PRICING_TABLE_NAMES

    # The chain grows additively and never reorders: this migration keeps its position
    # whatever lands after it. Asserting the position states that rule; asserting the
    # tail would only state today's length.
    assert MIGRATIONS[4] is v0017_billing_pricing
    assert v0017_billing_pricing.VERSION == "0017"
    versions = [m.VERSION for m in MIGRATIONS]
    assert versions == sorted(versions)
    for earlier, later in zip(versions[:-1], versions[1:], strict=True):
        assert int(later) == int(earlier) + 1  # ordered and contiguous

    database = BillingDatabase("sqlite://")
    for module in MIGRATIONS:
        module.upgrade(database)
    tables = set(inspect(database.engine).get_table_names())
    assert set(PRICING_TABLE_NAMES) <= tables
    database.close()


def test_pricing_migration_is_idempotent_and_alters_nothing():
    from sqlalchemy import inspect

    from billing.persistence.migrations import v0017_billing_pricing

    source = Path(v0017_billing_pricing.__file__).read_text()
    assert "ALTER TABLE" not in source and "DROP" not in source

    database = BillingDatabase("sqlite://")
    v0017_billing_pricing.upgrade(database)
    before = set(inspect(database.engine).get_table_names())
    v0017_billing_pricing.upgrade(database)  # again
    assert set(inspect(database.engine).get_table_names()) == before
    database.close()


def test_append_only_pricing_stores_expose_no_mutation_path():
    from billing.foundation import (
        PriceBookVersionRepository,
        PricingActivityRepository,
        PricingVersionRepository,
    )

    for contract in (
        PriceBookVersionRepository,
        PricingVersionRepository,
        PricingActivityRepository,
    ):
        methods = {n for n in dir(contract) if not n.startswith("_")}
        assert not methods & {"update", "delete", "remove", "replace", "amend"}, contract.__name__


def test_no_money_is_stored_in_a_projection_column():
    """A bare numeric column is exactly how a currency gets separated from its amount."""
    from billing.persistence.pricing_models import PRICING_TABLES

    for table in PRICING_TABLES:
        for column in table.__table__.columns:
            assert "minor" not in column.name, (table.__tablename__, column.name)
            assert column.name not in {"amount", "unit_amount", "price"}, column.name


# ── 9. HTTP surface ─────────────────────────────────────────────────────────────


def _http_seed(client, *, code="public", amount=999900, publish=True):
    client.post(
        f"{PREFIX}/books",
        headers=_auth(),
        json={"code": code, "display_name": code.title(), "currency": "INR", "effective_from": 0},
    )
    client.post(
        f"{PREFIX}/books/{code}/prices",
        headers=_auth(),
        json={
            "code": "pro",
            "shape": "flat_recurring",
            "amount": {"minor": amount, "currency": "INR"},
            "interval": "monthly",
            "effective_from": 0,
        },
    )
    if publish:
        client.post(f"{PREFIX}/books/{code}/publish", headers=_auth(), json={"notes": ""})


def test_price_book_lifecycle_over_http(client):
    created = client.post(
        f"{PREFIX}/books",
        headers=_auth(),
        json={"code": "public", "display_name": "Public", "currency": "INR", "effective_from": 0},
    )
    assert created.status_code == 201
    assert created.json()["state"] == "draft"
    assert created.json()["allowed_triggers"] == ["publish", "delete"]

    client.post(
        f"{PREFIX}/books/public/prices",
        headers=_auth(),
        json={
            "code": "pro",
            "shape": "flat_recurring",
            "amount": {"minor": 999900, "currency": "INR"},
            "interval": "monthly",
        },
    )
    published = client.post(f"{PREFIX}/books/public/publish", headers=_auth(), json={"notes": "go"})
    assert published.status_code == 201
    assert published.json()["version"] == 1 and published.json()["price_count"] == 1

    book = client.get(f"{PREFIX}/books/public", headers=_auth()).json()
    assert book["state"] == "published" and book["is_editable"] is False


def test_editing_a_published_book_over_http_is_409(client):
    _http_seed(client)
    response = client.patch(
        f"{PREFIX}/books/public/prices/pro",
        headers=_auth(),
        json={"amount": {"minor": 1, "currency": "INR"}},
    )
    assert response.status_code == 409


def test_illegal_transition_over_http_is_409(client):
    _http_seed(client, publish=False)
    assert client.post(f"{PREFIX}/books/public/archive", headers=_auth()).status_code == 409


def test_invalid_money_over_http_is_422_with_diagnostics(client):
    client.post(
        f"{PREFIX}/books",
        headers=_auth(),
        json={"code": "public", "display_name": "Public", "currency": "INR"},
    )
    response = client.post(
        f"{PREFIX}/books/public/prices",
        headers=_auth(),
        json={
            "code": "pro",
            "shape": "flat_recurring",
            "amount": {"minor": -500, "currency": "INR"},
            "interval": "monthly",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["diagnostics"]


def test_money_crosses_the_wire_as_minor_units_and_a_currency(client):
    _http_seed(client)
    price = client.get(f"{PREFIX}/books/public", headers=_auth()).json()["prices"][0]
    assert price["amount"] == {"minor": 999900, "currency": "INR"}
    assert isinstance(price["amount"]["minor"], int)


def test_resolve_over_http(client):
    _http_seed(client)
    client.post(
        f"{PREFIX}/discounts",
        headers=_auth(),
        json={
            "code": "launch",
            "display_name": "Launch",
            "kind": "percentage",
            "percentage_bp": 2500,
            "effective_from": 0,
        },
    )
    response = client.post(
        f"{PREFIX}/resolve",
        headers=_auth(),
        json={"price_code": "pro", "currency": "INR", "at": 100.0},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["list_amount"]["minor"] == 999900
    assert body["net_amount"]["minor"] == 749925
    assert body["resolution_path"][0] == "book=public@v1"
    assert [d["code"] for d in body["discounts"]] == ["launch"]


def test_resolving_an_unpriced_code_over_http_is_404(client):
    _http_seed(client)
    response = client.post(
        f"{PREFIX}/resolve",
        headers=_auth(),
        json={"price_code": "ghost", "currency": "INR", "at": 0.0},
    )
    assert response.status_code == 404


def test_publish_and_rollback_over_http(client):
    _http_seed(client)
    first = client.post(f"{PREFIX}/versions", headers=_auth(), json={"notes": "v1"})
    assert first.status_code == 201 and first.json()["version"] == 1
    client.post(f"{PREFIX}/versions", headers=_auth(), json={"notes": "v2"})
    rolled = client.post(
        f"{PREFIX}/versions/rollback", headers=_auth(), json={"to_version": 1, "notes": ""}
    )
    assert rolled.status_code == 201
    assert rolled.json()["version"] == 3 and rolled.json()["rolled_back_from"] == 1
    assert client.get(f"{PREFIX}/versions/2", headers=_auth()).status_code == 200
    assert len(client.get(f"{PREFIX}/versions", headers=_auth()).json()) == 3


def test_pricing_versions_have_no_mutation_verbs(client):
    """No PUT, no PATCH, no DELETE on a published version — the immutability is in the surface,
    not only in the docs."""
    schema = client.get("/openapi.json").json()
    for path, verbs in schema["paths"].items():
        if "/pricing/versions" in path or "/books/{code}/versions" in path:
            assert not ({"put", "patch", "delete"} & set(verbs)), path


def test_price_book_search_over_http(client):
    _http_seed(client, code="public")
    _http_seed(client, code="partner")
    body = client.get(f"{PREFIX}/books?limit=10", headers=_auth()).json()
    assert [b["code"] for b in body["books"]] == ["partner", "public"]
    assert body["total"] == 2
    filtered = client.get(f"{PREFIX}/books?text=part", headers=_auth()).json()
    assert [b["code"] for b in filtered["books"]] == ["partner"]


def test_discounts_promotions_coupons_over_http(client):
    for path, payload in (
        ("/discounts", {"code": "d1", "display_name": "D1", "percentage_bp": 1000}),
        ("/promotions", {"code": "p1", "display_name": "P1", "percentage_bp": 2000}),
        ("/coupons", {"code": "c1", "display_name": "C1", "percentage_bp": 500}),
    ):
        created = client.post(PREFIX + path, headers=_auth(), json={**payload, "effective_from": 0})
        assert created.status_code == 201, (path, created.text)
    assert len(client.get(f"{PREFIX}/discounts", headers=_auth()).json()) == 1
    assert len(client.get(f"{PREFIX}/promotions", headers=_auth()).json()) == 1
    coupons = client.get(f"{PREFIX}/coupons", headers=_auth()).json()
    assert coupons[0]["max_redemptions"] == 0 and coupons[0]["single_use"] is False


def test_reprice_over_http_keeps_the_old_version(client):
    _http_seed(client)
    client.post(f"{PREFIX}/books/public/duplicate", headers=_auth(), json={"code": ""})
    client.patch(
        f"{PREFIX}/books/public/prices/pro",
        headers=_auth(),
        json={"amount": {"minor": 1299900, "currency": "INR"}},
    )
    client.post(f"{PREFIX}/books/public/publish", headers=_auth(), json={"notes": "2026 prices"})
    versions = client.get(f"{PREFIX}/books/public/versions", headers=_auth()).json()
    assert [v["version"] for v in versions] == [2, 1]
    old = client.get(f"{PREFIX}/books/public?revision=1", headers=_auth()).json()
    assert old["prices"][0]["amount"]["minor"] == 999900


def test_activity_history_over_http(client):
    _http_seed(client)
    entries = client.get(f"{PREFIX}/activity", headers=_auth()).json()
    assert {e["action"] for e in entries} == {
        "price_book.created",
        "price.created",
        "price_book.published",
    }


def test_pricing_routes_require_identity_and_an_org(client):
    assert client.get(f"{PREFIX}/books", headers={}).status_code == 401
    assert client.get(f"{PREFIX}/books", headers=_auth(org_id="")).status_code == 401


def test_pricing_authorization_denial_is_403():
    with _client(authorizer=DenyAllAuthorizer()) as denied:
        response = denied.get(f"{PREFIX}/books", headers=_auth())
        assert response.status_code == 403
        assert "denied" in response.json()["error"]["message"]


def test_cross_org_isolation_over_http(client):
    _http_seed(client)
    assert client.get(f"{PREFIX}/books/public", headers=_auth(org_id="org_2")).status_code == 404


def test_the_organization_always_comes_from_the_token(client):
    """A body-supplied org must never influence what is written."""
    client.post(
        f"{PREFIX}/books",
        headers=_auth(org_id="org_1"),
        json={"code": "public", "display_name": "P", "currency": "INR", "org_id": "org_hacker"},
    )
    assert (
        client.get(f"{PREFIX}/books/public", headers=_auth(org_id="org_hacker")).status_code == 404
    )
    assert client.get(f"{PREFIX}/books/public", headers=_auth(org_id="org_1")).status_code == 200


def test_pricing_openapi_is_tagged_and_documents_errors(client):
    schema = client.get("/openapi.json").json()
    resolve = schema["paths"][f"{PREFIX}/resolve"]["post"]
    assert resolve["tags"] == ["pricing"]
    assert {"401", "403", "404", "409", "422"} <= set(resolve["responses"])
    assert any(t["name"] == "pricing" for t in schema["tags"])


# ── 10. Studio ──────────────────────────────────────────────────────────────────


STUDIO = BILLING / "customer" / "studio" / "pricing.html"


def test_pricing_studio_is_served(client):
    response = client.get("/studio/pricing.html")
    assert response.status_code == 200
    assert "Pricing" in response.text


def test_pricing_studio_is_self_contained():
    """No CDN, no external font, no build step — a strict-CSP-friendly single file."""
    html = STUDIO.read_text()
    for forbidden in ("http://", "https://", "cdn.", "<script src", "@import"):
        assert forbidden not in html, forbidden


def test_pricing_studio_has_a_screen_for_every_required_area():
    tabs = dict(_studio_tabs(STUDIO.read_text()))
    for required in (
        "books",
        "editor",
        "currencies",
        "regions",
        "discounts",
        "promotions",
        "coupons",
        "versions",
        "resolver",
    ):
        assert required in tabs, required
    assert len(tabs) == 9


def test_pricing_studio_computes_no_price_and_owns_no_state():
    """ADR-0021 taken literally: the Studio renders the server's answer, including the
    resolution path, and stores nothing."""
    html = STUDIO.read_text()
    assert "localStorage" not in html and "sessionStorage" not in html
    assert "resolution_path" in html
    # No client-side pricing arithmetic: the resolver posts and displays.
    assert "percentage_bp *" not in html
    assert "* quantity" not in html and "quantity *" not in html


def _studio_tabs(html: str) -> list[tuple[str, str]]:
    start = html.index("const TABS = [")
    body = html[start : html.index("];", start)]
    return [
        (m.group(1), m.group(2))
        for m in __import__("re").finditer(r'\["(\w+)",\s*"([^"]+)"\]', body)
    ]


# ── 11. architecture compliance ─────────────────────────────────────────────────


PRICING_MODULES = (
    BILLING / "foundation" / "price_books.py",
    BILLING / "foundation" / "pricing_rules.py",
    BILLING / "foundation" / "pricing_factories.py",
    BILLING / "application" / "pricing.py",
    BILLING / "persistence" / "pricing_models.py",
    BILLING / "persistence" / "pricing_repositories.py",
    BILLING / "customer" / "pricing_schemas.py",
    BILLING / "customer" / "routers" / "pricing_engine.py",
)


def _code_only(path: Path) -> str:
    """Source with docstrings stripped. These scans are about what pricing *is*, and prose
    explaining why it computes no invoice must not read as computing one."""
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


def test_pricing_owns_no_subscription_invoice_usage_or_payment():
    """The boundary this whole domain rests on: pricing answers what something costs, never
    what someone owes."""
    for path in PRICING_MODULES:
        source = _code_only(path)
        for forbidden in (
            "Subscription(",
            "Invoice(",
            "InvoiceLine",
            "UsageRecord",
            "UsageAggregate",
            "LedgerEntry",
            "Payment(",
            "Refund(",
            "TaxDetermination",
            "ChargeLine",
        ):
            assert forbidden not in source, (path.name, forbidden)


def test_pricing_never_rates():
    """Rating multiplies a price by a quantity. Nothing in this domain does — `Money.times`
    and `Money.allocate` are the operations that would, and they appear nowhere."""
    for path in PRICING_MODULES:
        source = _code_only(path)
        for forbidden in (".times(", ".allocate(", "quantity *", "* quantity", "* usage"):
            assert forbidden not in source, (path.name, forbidden)


def test_pricing_uses_no_float_arithmetic_on_money():
    """Percentages are integer basis points and amounts are integer minor units, all the way
    down. A float literal in a reduction path is a rounding artefact waiting to happen."""
    for path in (
        BILLING / "foundation" / "price_books.py",
        BILLING / "foundation" / "pricing_rules.py",
    ):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.Mult)):
                unparsed = ast.unparse(node)
                assert "minor" not in unparsed, (path.name, unparsed)


def test_pricing_foundation_stays_pure():
    for path in (
        BILLING / "foundation" / "price_books.py",
        BILLING / "foundation" / "pricing_rules.py",
        BILLING / "foundation" / "pricing_factories.py",
    ):
        source = path.read_text().lower()
        for forbidden in ("sqlmodel", "sqlalchemy", "fastapi", "pydantic", "session.", "select("):
            assert forbidden not in source, (path.name, forbidden)


def test_pricing_layers_import_only_downward():
    def imports(path: Path) -> set[str]:
        tree = ast.parse(path.read_text())
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
        return found

    allowed = {
        "core",
        "billing",
        "__future__",
        "abc",
        "dataclasses",
        "enum",
        "typing",
        "collections",
    }
    for path in (
        BILLING / "foundation" / "price_books.py",
        BILLING / "foundation" / "pricing_rules.py",
        BILLING / "foundation" / "pricing_factories.py",
    ):
        assert imports(path) <= allowed, (path.name, imports(path))

    app_imports = imports(BILLING / "application" / "pricing.py")
    assert "sqlmodel" not in app_imports and "fastapi" not in app_imports


def test_pricing_use_cases_contain_no_rule():
    """Orchestration only: no validation, no discount ordering, no book selection, no
    lifecycle table — all of it is called, none of it is re-implemented."""
    source = (BILLING / "application" / "pricing.py").read_text()
    for forbidden in (
        "def validate_",
        "def select_book",
        "def reduction",
        "PRICE_BOOK_LIFECYCLE",
        "percentage_bp /",
        "def eligible",
    ):
        assert forbidden not in source, forbidden


def test_pricing_controllers_stay_transport_only():
    tree = ast.parse((BILLING / "customer" / "routers" / "pricing_engine.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name != "_book_response":
            body = ast.unparse(node)
            assert "commands.dispatch" in body or "queries.dispatch" in body, node.name
            for forbidden in (
                "repositories",
                "session",
                ".commit()",
                "uow.",
                "validate",
                "reduction",
            ):
                assert forbidden not in body, (node.name, forbidden)


def test_repositories_make_no_pricing_decision():
    """A repository that quietly filtered by effective date would be making a pricing decision
    in the storage layer, where nobody would look for it."""
    source = _code_only(BILLING / "persistence" / "pricing_repositories.py")
    for forbidden in (
        "DiscountEngine",
        "PriceResolver",
        "select_book",
        ".reduction(",
        "specificity",
    ):
        assert forbidden not in source, forbidden


def test_command_and_query_coverage_is_complete():
    from billing.application import ALL_PRICING_COMMANDS, ALL_PRICING_QUERIES

    wired = build_persistent_billing_application(
        ids=SequentialIdGenerator(), clock=FixedClock(), authorizer=AllowAllAuthorizer()
    )
    for message in (*ALL_PRICING_COMMANDS, *ALL_PRICING_QUERIES):
        bus = (
            wired.application.commands
            if message in ALL_PRICING_COMMANDS
            else wired.application.queries
        )
        assert message in bus._handlers, message.__name__  # noqa: SLF001
    assert len(ALL_PRICING_COMMANDS) == 12
    assert len(ALL_PRICING_QUERIES) == 10
    wired.database.close()


def test_pricing_is_additive_and_optional():
    """Without pricing repositories the Application is exactly what M9.6 built — pricing is an
    extension, never a precondition."""
    from billing.application import BillingRepositories
    from billing.application.context import BillingContext

    assert "pricing" in {f.name for f in BillingRepositories.__dataclass_fields__.values()}
    assert BillingContext.__dataclass_fields__["pricing"].default is None
    assert BillingRepositories.__dataclass_fields__["pricing"].default is None


def test_no_route_collides_with_the_m9_5_pricing_surface(client):
    """`/pricing/price-books` is M9.5's and is reused unchanged, not duplicated."""
    paths = set(client.get("/openapi.json").json()["paths"])
    assert f"{PREFIX}/price-books" in paths  # M9.5, still there
    assert f"{PREFIX}/books" in paths  # M9.7
    assert len(paths) == len(set(paths))


def test_frozen_lower_layers_kept_their_surfaces():
    from billing import application, foundation, persistence

    # Additive growth only: every earlier name is still exported.
    for name in ("BillingFactory", "Money", "Product", "CatalogVersion", "PriceBook", "Price"):
        assert name in foundation.__all__
    for name in ("BillingApplication", "UnitOfWork", "CreateProduct", "PublishCatalog"):
        assert name in application.__all__
    for name in (
        "BillingDatabase",
        "build_persistent_billing_application",
        "CATALOG_SQL_REPOSITORIES",
    ):
        assert name in persistence.__all__
    assert len(foundation.__all__) > 238  # M9.6 left it at 238; growth only


def test_the_catalog_still_owns_no_money():
    """M9.6's boundary must survive the arrival of the domain that does own money."""
    for path in (
        BILLING / "foundation" / "products.py",
        BILLING / "foundation" / "catalog_rules.py",
        BILLING / "application" / "catalog.py",
        BILLING / "customer" / "routers" / "catalog_admin.py",
    ):
        code = _code_only(path)
        for forbidden in ("Money", "Currency", "unit_price", "percentage_bp"):
            assert forbidden not in code, (path.name, forbidden)


def test_billing_is_still_absent_from_the_runtime_composition_root():
    assert "billing" not in (PLATFORM / "bootstrap.py").read_text().lower()
