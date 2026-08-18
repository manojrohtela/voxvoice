"""M9.9 — Subscription Engine: tests.

Four layers, one domain: pure Foundation rules, then the Application over a real database, then
the HTTP surface, then architecture compliance. The heaviest coverage sits on the four properties
a commercial agreement cannot be retrofitted with — **immutable pins into published catalog and
pricing snapshots**, **versions that roll forward rather than being rewritten**, **intent kept
separable from execution**, and **a record that is never deleted** — and on the boundary that
keeps a subscription a subscription: it says what was agreed, never what it costs.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.customer.jwt import encode as epic8_encode
from billing.application import (
    ActivateBillingAccount,
    ActivateSubscription,
    AddSubscriptionItem,
    AllowAllAuthorizer,
    ArchiveSubscription,
    BillingAuthorizationError,
    CancelScheduledChange,
    CancelSubscription,
    ConvertSubscriptionTrial,
    CreateBillingAccount,
    CreatePrice,
    CreatePriceBook,
    CreateProduct,
    CreateSubscription,
    DenyAllAuthorizer,
    ExpireSubscription,
    ExpireSubscriptionTrial,
    GetCommercialSubscription,
    GetSubscriptionSnapshot,
    ListScheduledChanges,
    MarkSubscriptionPastDue,
    PauseSubscription,
    PublishCatalog,
    PublishPricing,
    PublishPricingBook,
    PublishProduct,
    PublishSubscriptionVersion,
    RecoverSubscription,
    RemoveSubscriptionItem,
    RenewSubscription,
    ResumeSubscription,
    RollbackSubscription,
    ScheduleSubscriptionChange,
    SearchSubscriptions,
    SetRenewalPolicy,
    StartSubscriptionTrial,
    SubscriptionTimeline,
    SubscriptionVersionHistory,
    SuspendSubscription,
    UpdateSubscriptionItem,
)
from billing.customer import BillingApiSettings, BillingContainer, create_billing_app
from billing.foundation import (
    AGREEMENT_LIFECYCLE,
    CLOSED_AGREEMENT_STATES,
    INTERVAL_SECONDS,
    MAX_PENDING_CHANGES,
    AccountFactory,
    AddressKind,
    AgreementItem,
    AgreementLifecycleService,
    AgreementState,
    AgreementTrigger,
    BillingConflict,
    BillingCycle,
    BillingCycleService,
    BillingInterval,
    BillingPeriod,
    BillingValidationError,
    CancellationTiming,
    CommercialSubscription,
    ContactRole,
    Currency,
    DefaultIdGenerator,
    FixedClock,
    ImmutabilityViolation,
    InvalidBillingTransition,
    ItemKind,
    Money,
    PausePolicy,
    PriceShape,
    RecordingEventEmitter,
    RenewalMode,
    RenewalPolicy,
    ScheduledChangeKind,
    ScheduleService,
    SequentialIdGenerator,
    SubscriptionAssembler,
    SubscriptionFactory,
    SubscriptionSearchQuery,
    SubscriptionSearchService,
    SubscriptionValidator,
    TrialPolicy,
    TrialService,
)
from billing.persistence import BillingDatabase, build_persistent_billing_application

PLATFORM = Path(__file__).resolve().parents[1]
BILLING = PLATFORM / "billing"
PREFIX = "/v1/billing/agreements"
SECRET = "test-billing-secret"
ORG = dict(actor_id="usr_1", org_id="org_1")
INR = Currency.INR


# ── fixtures ────────────────────────────────────────────────────────────────────


def _factory() -> tuple[SubscriptionFactory, RecordingEventEmitter]:
    emitter = RecordingEventEmitter()
    return (
        SubscriptionFactory(ids=SequentialIdGenerator(), clock=FixedClock(), emitter=emitter),
        emitter,
    )


def _item(
    code="pro", *, version=1, kind=ItemKind.FLAT, quantity=1, meter="", **kw
) -> AgreementItem:
    return AgreementItem(
        id=kw.pop("id", f"sit_{code}_{version}"),
        price_code=code,
        price_version=version,
        kind=kind,
        quantity=quantity,
        meter_code=meter,
        **kw,
    )


def _subscription(
    *,
    state=AgreementState.ACTIVE,
    items=None,
    catalog=1,
    pricing=1,
    number="SUB-000001",
    **kw,
) -> CommercialSubscription:
    return CommercialSubscription(
        id=kw.pop("id", "csb_1"),
        org_id="org_1",
        account_id=kw.pop("account_id", "cma_1"),
        subscription_number=number,
        catalog_version=catalog,
        pricing_version=pricing,
        currency=INR,
        state=state,
        items=tuple(items if items is not None else (_item(),)),
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


def test_every_subscription_starts_as_a_draft():
    factory, _ = _factory()
    subscription = factory.create_subscription(
        org_id="org_1", account_id="cma_1", catalog_version=1, pricing_version=1, currency=INR
    )
    assert subscription.state is AgreementState.DRAFT
    assert subscription.version == 1 and subscription.started_at is None


@pytest.mark.parametrize(
    ("state", "trigger", "expected"),
    [
        (AgreementState.DRAFT, AgreementTrigger.ACTIVATE, AgreementState.ACTIVE),
        (AgreementState.DRAFT, AgreementTrigger.START_TRIAL, AgreementState.TRIALING),
        (AgreementState.DRAFT, AgreementTrigger.CANCEL, AgreementState.CANCELLED),
        (AgreementState.TRIALING, AgreementTrigger.CONVERT_TRIAL, AgreementState.ACTIVE),
        (AgreementState.TRIALING, AgreementTrigger.EXPIRE_TRIAL, AgreementState.EXPIRED),
        (AgreementState.ACTIVE, AgreementTrigger.PAYMENT_FAILED, AgreementState.PAST_DUE),
        (AgreementState.ACTIVE, AgreementTrigger.PAUSE, AgreementState.PAUSED),
        (AgreementState.PAST_DUE, AgreementTrigger.PAYMENT_RECOVERED, AgreementState.ACTIVE),
        (AgreementState.PAST_DUE, AgreementTrigger.SUSPEND, AgreementState.SUSPENDED),
        (AgreementState.SUSPENDED, AgreementTrigger.REACTIVATE, AgreementState.ACTIVE),
        (AgreementState.PAUSED, AgreementTrigger.RESUME, AgreementState.ACTIVE),
        (AgreementState.CANCELLED, AgreementTrigger.EXPIRE, AgreementState.EXPIRED),
        (AgreementState.EXPIRED, AgreementTrigger.ARCHIVE, AgreementState.ARCHIVED),
    ],
)
def test_legal_lifecycle_edges(state, trigger, expected):
    assert AgreementLifecycleService().next_state(state, trigger) is expected


@pytest.mark.parametrize(
    ("state", "trigger"),
    [
        (AgreementState.DRAFT, AgreementTrigger.PAUSE),
        (AgreementState.DRAFT, AgreementTrigger.CONVERT_TRIAL),
        (AgreementState.ACTIVE, AgreementTrigger.ACTIVATE),
        (AgreementState.ACTIVE, AgreementTrigger.RESUME),
        (AgreementState.TRIALING, AgreementTrigger.PAUSE),
        (AgreementState.PAUSED, AgreementTrigger.SUSPEND),
        (AgreementState.CANCELLED, AgreementTrigger.REACTIVATE),
        (AgreementState.EXPIRED, AgreementTrigger.ACTIVATE),
        (AgreementState.ARCHIVED, AgreementTrigger.REACTIVATE),
    ],
)
def test_illegal_lifecycle_edges_are_rejected_never_coerced(state, trigger):
    with pytest.raises(InvalidBillingTransition):
        AgreementLifecycleService().next_state(state, trigger)


def test_a_subscription_can_never_be_deleted():
    """Stronger than the catalog, pricing, or account lifecycles: there is no removal state at
    all. A subscription *is* the commercial agreement, and ADR-0023 RS4 makes it permanent."""
    assert not any(s.value == "deleted" for s in AgreementState)
    assert not any(t.value == "delete" for t in AgreementTrigger)


def test_archived_is_terminal_and_is_the_only_end():
    lifecycle = AgreementLifecycleService()
    assert lifecycle.allowed(AgreementState.ARCHIVED) == ()
    assert set(lifecycle.allowed(AgreementState.EXPIRED)) == {AgreementTrigger.ARCHIVE}


def test_a_cancelled_agreement_is_never_resurrected():
    """Winning a customer back is a *new* agreement — conflating the two makes churn
    unmeasurable."""
    lifecycle = AgreementLifecycleService()
    assert set(lifecycle.allowed(AgreementState.CANCELLED)) == {AgreementTrigger.EXPIRE}


def test_pause_and_suspend_are_never_reachable_from_one_another():
    """Pause is the customer's choice, suspension is ours; a path between them would blur which
    of the two actually happened."""
    lifecycle = AgreementLifecycleService()
    assert not lifecycle.can(AgreementState.PAUSED, AgreementTrigger.SUSPEND)
    assert not lifecycle.can(AgreementState.SUSPENDED, AgreementTrigger.PAUSE)


def test_renewal_is_not_a_lifecycle_edge():
    """Renewing advances the cycle; it does not change what state the agreement is in. Putting
    it in the FSM would have hidden a period computation inside a state machine."""
    assert not any(t.value == "renew" for t in AgreementTrigger)
    for state in AgreementState:
        for trigger in AgreementTrigger:
            edge = AGREEMENT_LIFECYCLE.get(state, trigger)
            assert edge is None or edge.target is not state  # no self-loops at all


def test_lifecycle_transitions_never_mutate_the_input():
    subscription = _subscription(state=AgreementState.DRAFT)
    moved = AgreementLifecycleService().apply(subscription, AgreementTrigger.ACTIVATE, at=7.0)
    assert subscription.state is AgreementState.DRAFT and subscription.started_at is None
    assert moved.state is AgreementState.ACTIVE and moved.started_at == 7.0


def test_cancelling_records_when_why_and_when_it_bites():
    period = BillingPeriod(0.0, 1000.0)
    subscription = _subscription(cycle=BillingCycle(current_period=period))
    cancelled = AgreementLifecycleService().apply(
        subscription,
        AgreementTrigger.CANCEL,
        at=400.0,
        reason="moved to a competitor",
        reason_code="churn",
        timing=CancellationTiming.PERIOD_END,
    )
    assert cancelled.cancelled_at == 400.0
    assert cancelled.cancellation.reason == "moved to a competitor"
    assert cancelled.cancellation.reason_code == "churn"
    # A customer who has paid for the period keeps it.
    assert cancelled.cancellation.effective_at == 1000.0


def test_immediate_cancellation_bites_now():
    cancelled = AgreementLifecycleService().apply(
        _subscription(cycle=BillingCycle(current_period=BillingPeriod(0.0, 1000.0))),
        AgreementTrigger.CANCEL,
        at=400.0,
        timing=CancellationTiming.IMMEDIATE,
    )
    assert cancelled.cancellation.effective_at == 400.0


def test_a_closed_agreement_refuses_edits():
    for state in CLOSED_AGREEMENT_STATES:
        with pytest.raises(BillingConflict):
            AgreementLifecycleService().guard_editable(_subscription(state=state))
    AgreementLifecycleService().guard_editable(_subscription())  # active: no raise


def test_the_pause_allowance_is_a_commercial_limit_not_a_structural_one():
    """The FSM says pausing is legal; the policy says the customer has none left. Two different
    refusals, both Foundation decisions."""
    lifecycle = AgreementLifecycleService()
    exhausted = _subscription(pause=PausePolicy(pauses_per_term=2, pauses_used=2))
    assert lifecycle.can(exhausted.state, AgreementTrigger.PAUSE)
    with pytest.raises(BillingConflict):
        lifecycle.guard_pausable(exhausted)


def test_pausing_counts_against_the_allowance():
    paused = AgreementLifecycleService().apply(_subscription(), AgreementTrigger.PAUSE, at=5.0)
    assert paused.pause.pauses_used == 1 and paused.pause.paused_at == 5.0
    resumed = AgreementLifecycleService().apply(paused, AgreementTrigger.RESUME, at=9.0)
    assert resumed.pause.paused_at is None and resumed.pause.resume_at == 9.0


# ── 2. Foundation: billing cycle ────────────────────────────────────────────────


def test_opening_a_period_is_idempotent():
    """Opening twice must not silently reset a customer's billing date."""
    cycles = BillingCycleService()
    first = cycles.open_period(BillingCycle(), at=100.0)
    again = cycles.open_period(first, at=500.0)
    assert first.current_period == again.current_period


def test_a_renewal_anchors_to_the_period_end_not_to_now():
    """A renewal processed late must not shift the customer's billing date forward."""
    cycles = BillingCycleService()
    cycle = BillingCycle(current_period=BillingPeriod(0.0, 1000.0))
    assert cycles.next_period(cycle, at=5_000.0).start == 1000.0


def test_renewal_advances_the_cycle_and_counts_the_term():
    cycles = BillingCycleService()
    subscription = _subscription(
        cycle=BillingCycle(current_period=BillingPeriod(0.0, 1000.0)),
        renewal=RenewalPolicy(mode=RenewalMode.AUTO, terms=3),
    )
    renewed = cycles.renew(subscription, at=1000.0)
    assert renewed.cycle.cycle_number == 2
    assert renewed.cycle.current_period.start == 1000.0
    assert renewed.renewal.terms_used == 1
    assert renewed.state is AgreementState.ACTIVE  # not a state change


def test_renewing_beyond_the_agreed_terms_is_refused():
    """A commercial decision, not a computation."""
    cycles = BillingCycleService()
    exhausted = _subscription(renewal=RenewalPolicy(terms=2, terms_used=2))
    with pytest.raises(BillingConflict):
        cycles.renew(exhausted, at=10.0)


def test_only_an_active_subscription_renews():
    with pytest.raises(BillingConflict):
        BillingCycleService().renew(_subscription(state=AgreementState.PAUSED), at=10.0)


def test_renewal_due_is_a_read_not_an_action():
    cycles = BillingCycleService()
    subscription = _subscription(cycle=BillingCycle(current_period=BillingPeriod(0.0, 1000.0)))
    assert not cycles.is_due_for_renewal(subscription, at=500.0)
    assert cycles.is_due_for_renewal(subscription, at=1000.0)
    # Reading it changed nothing.
    assert subscription.cycle.cycle_number == 1


def test_interval_seconds_covers_exactly_the_frozen_enum():
    """Adding a member to M9.2's frozen `BillingInterval` is a change this milestone has no
    mandate to make — so the table matches the enum exactly rather than inventing members."""
    assert set(INTERVAL_SECONDS) == set(BillingInterval)


# ── 3. Foundation: trials ───────────────────────────────────────────────────────


def test_starting_a_trial_computes_a_deterministic_end():
    trials = TrialService()
    subscription = _subscription(trial=TrialPolicy(enabled=True, length_days=14))
    policy = trials.start(subscription, at=1000.0)
    assert policy.ends_at == 1000.0 + 14 * 86_400


def test_a_trial_expires_at_its_end_not_before():
    trials = TrialService()
    subscription = _subscription(
        state=AgreementState.TRIALING, trial=TrialPolicy(enabled=True, ends_at=1000.0)
    )
    assert not trials.is_expired(subscription, at=999.0)
    assert trials.is_expired(subscription, at=1000.0)


def test_trial_days_remaining_never_goes_negative():
    trials = TrialService()
    subscription = _subscription(
        state=AgreementState.TRIALING, trial=TrialPolicy(enabled=True, ends_at=1000.0)
    )
    assert trials.remaining_days(subscription, at=5_000_000.0) == 0


def test_conversion_blockers_are_returned_as_a_reason_not_raised():
    """A UI needs to *show* the reason before the customer clicks."""
    trials = TrialService()
    assert "not trialing" in trials.conversion_blocked_reason(_subscription())
    empty = _subscription(state=AgreementState.TRIALING, items=[])
    assert "no items" in trials.conversion_blocked_reason(empty)
    ok = _subscription(state=AgreementState.TRIALING)
    assert trials.conversion_blocked_reason(ok) == ""


# ── 4. Foundation: scheduled changes ────────────────────────────────────────────


def _change(kind=ScheduledChangeKind.UPGRADE, *, at=5000.0, **kw):
    from billing.foundation import ScheduledSubscriptionChange

    return ScheduledSubscriptionChange(
        id=kw.pop("id", f"sch_{kind.value}"),
        kind=kind,
        effective_at=at,
        target_plan_code=kw.pop("target_plan_code", "pro"),
        **kw,
    )


def test_scheduling_records_intent_and_applies_nothing():
    """The property the whole design rests on: reading a due change does not apply it."""
    schedules = ScheduleService()
    subscription = _subscription(scheduled_changes=(_change(at=100.0),))
    due = schedules.due(subscription, at=200.0)
    assert len(due) == 1 and due[0].is_pending
    assert subscription.scheduled_changes[0].status.value == "pending"
    assert subscription.scheduled_changes[0].applied_at is None


def test_a_change_is_due_only_at_or_after_its_instant():
    subscription = _subscription(scheduled_changes=(_change(at=100.0),))
    assert ScheduleService().due(subscription, at=99.0) == ()
    assert len(ScheduleService().due(subscription, at=100.0)) == 1


def test_pending_changes_are_ordered_by_when_they_bite():
    subscription = _subscription(
        scheduled_changes=(
            _change(at=900.0, id="sch_b"),
            _change(at=100.0, id="sch_a"),
        )
    )
    assert [c.id for c in subscription.pending_changes] == ["sch_a", "sch_b"]


def test_two_pending_cancellations_conflict():
    subscription = _subscription(
        scheduled_changes=(
            _change(ScheduledChangeKind.CANCELLATION, at=100.0, id="sch_1"),
            _change(ScheduledChangeKind.CANCELLATION, at=200.0, id="sch_2"),
        )
    )
    diagnostics = SubscriptionValidator().validate_schedule(subscription.scheduled_changes)
    assert any(f.code.value == "conflicting_scheduled_change" for f in diagnostics.errors)


def test_a_change_after_a_cancellation_is_rejected():
    """The subscription will not exist to receive it."""
    subscription = _subscription(
        scheduled_changes=(
            _change(ScheduledChangeKind.CANCELLATION, at=100.0, id="sch_1"),
            _change(ScheduledChangeKind.UPGRADE, at=500.0, id="sch_2"),
        )
    )
    diagnostics = SubscriptionValidator().validate_schedule(subscription.scheduled_changes)
    assert any(f.code.value == "conflicting_scheduled_change" for f in diagnostics.errors)


def test_the_pending_change_bound_is_enforced():
    changes = tuple(_change(at=100.0 + i, id=f"sch_{i}") for i in range(MAX_PENDING_CHANGES + 1))
    diagnostics = SubscriptionValidator().validate_schedule(changes)
    assert any(f.code.value == "conflicting_scheduled_change" for f in diagnostics.errors)


def test_a_pricing_switch_must_name_the_version_it_switches_to():
    diagnostics = SubscriptionValidator().validate_schedule(
        (_change(ScheduledChangeKind.PRICING_SWITCH, target_plan_code=""),)
    )
    assert any(f.code.value == "invalid_scheduled_change" for f in diagnostics.errors)


def test_an_overdue_pending_change_warns_rather_than_blocking():
    """Nothing in this domain applies it, so an overdue change is information, not an error."""
    diagnostics = SubscriptionValidator().validate_schedule((_change(at=100.0),), at=9_000.0)
    assert not diagnostics.has_errors
    assert any(f.code.value == "scheduled_change_in_past" for f in diagnostics.warnings)


def test_marking_a_change_applied_moves_it_out_of_pending():
    schedules = ScheduleService()
    subscription = _subscription(scheduled_changes=(_change(id="sch_x"),))
    applied = schedules.mark_applied(subscription, "sch_x", at=99.0)
    assert applied.pending_changes == ()
    assert applied.scheduled_change("sch_x").applied_at == 99.0
    with pytest.raises(BillingConflict):
        schedules.mark_applied(applied, "sch_x", at=100.0)  # not twice


def test_cancelling_an_unknown_change_is_loud():
    with pytest.raises(BillingConflict):
        ScheduleService().cancel_change(_subscription(), "sch_ghost", at=1.0)


def test_schedule_conflicts_are_explained_as_a_sentence():
    schedules = ScheduleService()
    subscription = _subscription(
        scheduled_changes=(_change(ScheduledChangeKind.CANCELLATION, at=100.0),)
    )
    assert "already scheduled" in schedules.conflicts_with(
        subscription, _change(ScheduledChangeKind.CANCELLATION, at=200.0, id="sch_z")
    )
    assert "scheduled to end" in schedules.conflicts_with(
        subscription, _change(ScheduledChangeKind.UPGRADE, at=500.0, id="sch_y")
    )
    assert (
        schedules.conflicts_with(
            subscription, _change(ScheduledChangeKind.UPGRADE, at=50.0, id="sch_w")
        )
        == ""
    )


# ── 5. Foundation: validation ───────────────────────────────────────────────────


def test_validation_never_raises_and_reports_every_finding():
    broken = CommercialSubscription(
        id="csb_x",
        org_id="",
        account_id="",
        subscription_number="",
        catalog_version=0,
        pricing_version=0,
        currency=INR,
        version=0,
    )
    diagnostics = SubscriptionValidator().validate_subscription(broken)
    assert len(diagnostics.errors) >= 5
    assert diagnostics.findings == tuple(sorted(diagnostics.findings))


def test_both_commercial_pins_are_required():
    diagnostics = SubscriptionValidator().validate_subscription(_subscription(catalog=0, pricing=0))
    codes = {f.code.value for f in diagnostics.errors}
    assert "unknown_catalog_version" in codes and "unknown_pricing_version" in codes


def test_an_empty_known_version_set_rejects_rather_than_skips():
    """An **empty** set means "nothing is published", which must reject the pin — the defect
    M9.6 caught the hard way, re-asserted for both pins here."""
    validator = SubscriptionValidator()
    subscription = _subscription(catalog=3, pricing=4)
    assert validator.validate_subscription(
        subscription, known_catalog_versions=frozenset()
    ).has_errors
    assert validator.validate_subscription(
        subscription, known_pricing_versions=frozenset()
    ).has_errors
    assert not validator.validate_subscription(
        subscription, known_catalog_versions=frozenset({3}), known_pricing_versions=frozenset({4})
    ).has_errors


def test_a_live_subscription_must_agree_to_something():
    diagnostics = SubscriptionValidator().validate_subscription(_subscription(items=[]))
    assert any(f.code.value == "empty_subscription" for f in diagnostics.errors)


def test_a_draft_may_be_empty():
    assert (
        not SubscriptionValidator()
        .validate_subscription(_subscription(state=AgreementState.DRAFT, items=[]))
        .has_errors
    )


def test_duplicate_price_pins_are_rejected():
    diagnostics = SubscriptionValidator().validate_items(
        (_item("pro", id="a"), _item("pro", id="b"))
    )
    assert any(f.code.value == "duplicate_subscription_item" for f in diagnostics.errors)


def test_the_same_price_at_two_versions_is_not_a_duplicate():
    """A superseding pin is exactly how a mid-term price move is expressed."""
    assert (
        not SubscriptionValidator()
        .validate_items((_item("pro", version=1, id="a"), _item("pro", version=2, id="b")))
        .has_errors
    )


def test_an_item_must_pin_a_price_version():
    diagnostics = SubscriptionValidator().validate_items((_item(version=0),))
    assert any(f.code.value == "invalid_subscription_item" for f in diagnostics.errors)


def test_a_metered_item_must_name_its_meter():
    for kind in (ItemKind.USAGE, ItemKind.TIERED, ItemKind.VOLUME):
        diagnostics = SubscriptionValidator().validate_items((_item(kind=kind, meter=""),))
        assert any(f.code.value == "invalid_subscription_item" for f in diagnostics.errors), kind


def test_a_seat_item_needs_at_least_one_seat():
    diagnostics = SubscriptionValidator().validate_items((_item(kind=ItemKind.SEAT, quantity=0),))
    assert any(f.code.value == "invalid_subscription_item" for f in diagnostics.errors)


@pytest.mark.parametrize("day", [0, 29, 31])
def test_an_anchor_day_outside_1_to_28_is_rejected(day):
    diagnostics = SubscriptionValidator().validate_cycle(BillingCycle(anchor_day=day))
    assert any(f.code.value == "invalid_billing_cycle" for f in diagnostics.errors)


def test_an_inverted_period_is_impossible_to_construct():
    """M9.2's `BillingPeriod` refuses it outright — a stronger guarantee than a diagnostic, so
    `validate_cycle`'s matching check is defence in depth for a decoded row rather than the
    primary defence."""
    with pytest.raises(BillingValidationError):
        BillingPeriod(100.0, 50.0)
    assert (
        not SubscriptionValidator()
        .validate_cycle(BillingCycle(current_period=BillingPeriod(0.0, 1000.0)))
        .has_errors
    )


def test_an_enabled_trial_needs_a_length_or_an_end():
    diagnostics = SubscriptionValidator().validate_trial(TrialPolicy(enabled=True))
    assert any(f.code.value == "invalid_trial_policy" for f in diagnostics.errors)


def test_a_trialing_subscription_needs_an_enabled_trial():
    diagnostics = SubscriptionValidator().validate_subscription(
        _subscription(state=AgreementState.TRIALING, trial=TrialPolicy(enabled=False))
    )
    assert any(f.code.value == "invalid_trial_policy" for f in diagnostics.errors)


def test_terms_used_cannot_exceed_terms_agreed():
    diagnostics = SubscriptionValidator().validate_renewal(RenewalPolicy(terms=2, terms_used=5))
    assert any(f.code.value == "invalid_renewal_policy" for f in diagnostics.errors)


def test_an_inactive_account_cannot_hold_a_live_subscription():
    diagnostics = SubscriptionValidator().validate_subscription(
        _subscription(), account_state="suspended"
    )
    assert any(f.code.value == "inactive_billing_account" for f in diagnostics.errors)


def test_a_missing_account_is_rejected():
    diagnostics = SubscriptionValidator().validate_subscription(_subscription(), account_state="")
    assert any(f.code.value == "unknown_billing_account" for f in diagnostics.errors)


# ── 6. Foundation: versions + snapshots ─────────────────────────────────────────


def test_freezing_embeds_the_whole_agreement():
    frozen = SubscriptionAssembler().freeze(
        _subscription(), version_id="svr_1", version=1, published_at=10.0
    )
    assert frozen.subscription is not None
    assert frozen.subscription.version == 1 and frozen.item_count == 1
    assert frozen.commercial_pin == (1, 1)


def test_freezing_is_deterministic():
    subscription = _subscription(items=[_item("b", id="b"), _item("a", id="a")])
    assembler = SubscriptionAssembler()
    first = assembler.freeze(subscription, version_id="svr_1", version=1, published_at=0.0)
    second = assembler.freeze(subscription, version_id="svr_1", version=1, published_at=0.0)
    assert first == second
    assert [i.price_code for i in first.subscription.items] == ["a", "b"]  # sorted on the way in


def test_a_published_version_is_immutable():
    frozen = SubscriptionAssembler().freeze(
        _subscription(), version_id="svr_1", version=1, published_at=0.0
    )
    with pytest.raises(Exception):  # noqa: B017 — frozen dataclass, any attribute
        frozen.version = 9


def test_rollback_publishes_forward_and_records_provenance():
    assembler = SubscriptionAssembler()
    source = assembler.freeze(_subscription(), version_id="svr_1", version=1, published_at=0.0)
    rolled = assembler.rollback(source, version_id="svr_3", version=3, published_at=99.0)
    assert rolled.version == 3 and rolled.rolled_back_from == 1 and rolled.is_rollback
    assert rolled.subscription.version == 3
    assert source.version == 1  # untouched


def test_rollback_cannot_rewind_the_version_number():
    assembler = SubscriptionAssembler()
    source = assembler.freeze(_subscription(), version_id="svr_5", version=5, published_at=0.0)
    with pytest.raises(BillingConflict):
        assembler.rollback(source, version_id="svr_x", version=3, published_at=1.0)


def test_a_version_must_embed_an_agreement_at_its_own_number():
    from billing.foundation import SubscriptionVersion

    broken = SubscriptionVersion(
        id="svr_1",
        org_id="org_1",
        subscription_id="csb_1",
        version=2,
        subscription=_subscription(version=1),
    )
    diagnostics = SubscriptionValidator().validate_version(broken)
    assert any(f.code.value == "subscription_snapshot_integrity" for f in diagnostics.errors)


def test_a_version_with_no_agreement_is_not_a_snapshot():
    from billing.foundation import SubscriptionVersion

    diagnostics = SubscriptionValidator().validate_version(
        SubscriptionVersion(id="svr_1", org_id="org_1", subscription_id="csb_1", version=1)
    )
    assert any(f.code.value == "subscription_snapshot_integrity" for f in diagnostics.errors)


def test_a_snapshot_is_derived_and_reproducible():
    snapshot = SubscriptionAssembler().snapshot(
        _subscription(
            items=[
                _item(kind=ItemKind.SEAT, quantity=7),
                _item("min", meter="voice", kind=ItemKind.USAGE),
            ],
            cycle=BillingCycle(current_period=BillingPeriod(0.0, 1000.0)),
        ),
        at=500.0,
    )
    assert snapshot.is_reproducible and snapshot.in_period
    assert snapshot.seat_count == 7 and snapshot.metered_codes == ("voice",)


def test_history_answers_which_terms_were_in_force():
    assembler = SubscriptionAssembler()
    v1 = assembler.freeze(_subscription(), version_id="svr_1", version=1, published_at=100.0)
    v2 = assembler.freeze(_subscription(), version_id="svr_2", version=2, published_at=500.0)
    history = assembler.history("csb_1", versions=[v2, v1])
    assert history.current_version == 2
    assert history.version_at(300.0).version == 1
    assert history.version_at(600.0).version == 2
    assert history.version_at(50.0) is None


# ── 7. Foundation: search + facts ───────────────────────────────────────────────


def test_search_is_deterministic_and_ordered_by_number():
    subs = [_subscription(number="SUB-003", id="c"), _subscription(number="SUB-001", id="a")]
    result = SubscriptionSearchService().search(subs, SubscriptionSearchQuery())
    assert [s.subscription_number for s in result.subscriptions] == ["SUB-001", "SUB-003"]
    assert result.total == 2


def test_search_filters_compose():
    live = _subscription(id="a", number="SUB-001")
    paused = _subscription(id="b", number="SUB-002", state=AgreementState.PAUSED)
    service = SubscriptionSearchService()
    assert service.search([live, paused], SubscriptionSearchQuery(live_only=True)).total == 1
    assert service.search([live, paused], SubscriptionSearchQuery(catalog_version=1)).total == 2
    assert service.search([live, paused], SubscriptionSearchQuery(catalog_version=9)).total == 0
    assert service.search([live, paused], SubscriptionSearchQuery(account_id="cma_1")).total == 2


def test_search_pages_without_losing_the_total():
    subs = [_subscription(id=str(i), number=f"SUB-{i:03d}") for i in range(5)]
    result = SubscriptionSearchService().search(subs, SubscriptionSearchQuery(limit=2, offset=1))
    assert len(result.subscriptions) == 2 and result.total == 5


def test_a_rejected_creation_emits_no_business_event():
    """Exactly one event per business fact — and none at all for a fact that never happened."""
    factory, emitter = _factory()
    with pytest.raises(BillingValidationError):
        factory.create_subscription(
            org_id="org_1", account_id="", catalog_version=0, pricing_version=0, currency=INR
        )
    assert emitter.events == ()


def test_a_successful_creation_emits_exactly_one_business_event():
    from billing.foundation import BillingEventType

    factory, emitter = _factory()
    factory.create_subscription(
        org_id="org_1", account_id="cma_1", catalog_version=1, pricing_version=1, currency=INR
    )
    created = [e for e in emitter.events if e.type is BillingEventType.SUBSCRIPTION_CREATED]
    assert len(created) == 1


# ── 8. Application over a real database ─────────────────────────────────────────


def _commercial_context(app) -> tuple[int, int, str]:
    """Build a real account, catalog version, and pricing version — the three things a
    subscription pins to."""
    account = app.commands.dispatch(
        CreateBillingAccount(
            **ORG,
            display_name="Acme",
            currency=INR,
            contacts=(AccountFactory.contact(ContactRole.BILLING, email="ap@acme.test"),),
            addresses=(
                AccountFactory.address(
                    AddressKind.BILLING, line1="1 MG Road", city="Bengaluru", country="IN"
                ),
            ),
        )
    )
    app.commands.dispatch(ActivateBillingAccount(**ORG, account_id=account.id))

    app.commands.dispatch(CreateProduct(**ORG, code="voice", display_name="Voice"))
    app.commands.dispatch(PublishProduct(**ORG, code="voice"))
    catalog = app.commands.dispatch(PublishCatalog(**ORG))

    app.commands.dispatch(
        CreatePriceBook(
            **ORG, code="public", display_name="Public", currency=INR, effective_from=0.0
        )
    )
    app.commands.dispatch(
        CreatePrice(
            **ORG,
            book_code="public",
            code="pro",
            shape=PriceShape.FLAT_RECURRING,
            amount=Money(999900, INR),
            interval=BillingInterval.MONTHLY,
            effective_from=0.0,
        )
    )
    app.commands.dispatch(PublishPricingBook(**ORG, code="public"))
    pricing = app.commands.dispatch(PublishPricing(**ORG, catalog_version=catalog.version))
    return catalog.version, pricing.version, account.id


def _seed(app, *, activate=True, with_item=True):
    catalog, pricing, account_id = _commercial_context(app)
    subscription = app.commands.dispatch(
        CreateSubscription(
            **ORG,
            account_id=account_id,
            catalog_version=catalog,
            pricing_version=pricing,
            currency=INR,
            plan_code="pro",
        )
    )
    if with_item:
        app.commands.dispatch(
            AddSubscriptionItem(
                **ORG,
                subscription_id=subscription.id,
                price_code="pro",
                price_version=1,
                kind=ItemKind.SEAT,
                quantity=10,
            )
        )
    if activate:
        app.commands.dispatch(ActivateSubscription(**ORG, subscription_id=subscription.id))
    return subscription, catalog, pricing, account_id


def test_subscription_numbers_are_allocated_and_never_reused(app):
    catalog, pricing, account_id = _commercial_context(app)
    first = app.commands.dispatch(
        CreateSubscription(
            **ORG,
            account_id=account_id,
            catalog_version=catalog,
            pricing_version=pricing,
            currency=INR,
        )
    )
    second = app.commands.dispatch(
        CreateSubscription(
            **ORG,
            account_id=account_id,
            catalog_version=catalog,
            pricing_version=pricing,
            currency=INR,
        )
    )
    assert first.subscription_number == "SUB-000001"
    assert second.subscription_number == "SUB-000002"


def test_a_subscription_pins_real_published_versions(app):
    subscription, catalog, pricing, _ = _seed(app)
    view = app.queries.dispatch(GetCommercialSubscription(**ORG, subscription_id=subscription.id))
    assert (view.catalog_version, view.pricing_version) == (catalog, pricing)


def test_a_dangling_catalog_pin_is_rejected(app):
    catalog, pricing, account_id = _commercial_context(app)
    with pytest.raises(BillingValidationError) as excinfo:
        app.commands.dispatch(
            CreateSubscription(
                **ORG,
                account_id=account_id,
                catalog_version=99,
                pricing_version=pricing,
                currency=INR,
            )
        )
    assert any(f.code.value == "unknown_catalog_version" for f in excinfo.value.diagnostics.errors)


def test_a_dangling_pricing_pin_is_rejected(app):
    catalog, pricing, account_id = _commercial_context(app)
    with pytest.raises(BillingValidationError) as excinfo:
        app.commands.dispatch(
            CreateSubscription(
                **ORG,
                account_id=account_id,
                catalog_version=catalog,
                pricing_version=99,
                currency=INR,
            )
        )
    assert any(f.code.value == "unknown_pricing_version" for f in excinfo.value.diagnostics.errors)


def test_a_subscription_for_an_unknown_account_is_rejected(app):
    catalog, pricing, _ = _commercial_context(app)
    with pytest.raises(BillingValidationError) as excinfo:
        app.commands.dispatch(
            CreateSubscription(
                **ORG,
                account_id="cma_ghost",
                catalog_version=catalog,
                pricing_version=pricing,
                currency=INR,
            )
        )
    assert any(f.code.value == "unknown_billing_account" for f in excinfo.value.diagnostics.errors)


def test_publishing_a_new_catalog_never_mutates_an_existing_subscription(app):
    """The property M9.6 made immutable *for*."""
    subscription, catalog, pricing, _ = _seed(app)
    app.commands.dispatch(CreateProduct(**ORG, code="second", display_name="Second"))
    app.commands.dispatch(PublishProduct(**ORG, code="second"))
    newer = app.commands.dispatch(PublishCatalog(**ORG))
    assert newer.version > catalog

    view = app.queries.dispatch(GetCommercialSubscription(**ORG, subscription_id=subscription.id))
    assert view.catalog_version == catalog  # untouched


def test_publishing_new_pricing_never_mutates_an_existing_subscription(app):
    """And the property M9.7 made immutable for."""
    subscription, catalog, pricing, _ = _seed(app)
    newer = app.commands.dispatch(PublishPricing(**ORG, catalog_version=catalog))
    assert newer.version > pricing

    view = app.queries.dispatch(GetCommercialSubscription(**ORG, subscription_id=subscription.id))
    assert view.pricing_version == pricing  # untouched


def test_lifecycle_through_the_application(app):
    subscription, *_ = _seed(app, activate=False)
    view = app.queries.dispatch(GetCommercialSubscription(**ORG, subscription_id=subscription.id))
    assert view.state == "draft"
    assert set(view.allowed_triggers) == {"activate", "start_trial", "cancel"}

    app.commands.dispatch(ActivateSubscription(**ORG, subscription_id=subscription.id))
    view = app.queries.dispatch(GetCommercialSubscription(**ORG, subscription_id=subscription.id))
    assert view.state == "active" and view.cycle.current_period is not None

    app.commands.dispatch(
        MarkSubscriptionPastDue(**ORG, subscription_id=subscription.id, reason="card declined")
    )
    assert (
        app.queries.dispatch(
            GetCommercialSubscription(**ORG, subscription_id=subscription.id)
        ).state
        == "past_due"
    )
    app.commands.dispatch(RecoverSubscription(**ORG, subscription_id=subscription.id))
    app.commands.dispatch(
        PauseSubscription(**ORG, subscription_id=subscription.id, reason="summer")
    )
    app.commands.dispatch(ResumeSubscription(**ORG, subscription_id=subscription.id))
    app.commands.dispatch(
        SuspendSubscription(**ORG, subscription_id=subscription.id, reason="abuse")
    )
    app.commands.dispatch(
        CancelSubscription(
            **ORG,
            subscription_id=subscription.id,
            timing=CancellationTiming.IMMEDIATE,
            reason="churn",
        )
    )
    app.commands.dispatch(ExpireSubscription(**ORG, subscription_id=subscription.id))
    app.commands.dispatch(ArchiveSubscription(**ORG, subscription_id=subscription.id))
    view = app.queries.dispatch(GetCommercialSubscription(**ORG, subscription_id=subscription.id))
    assert view.state == "archived" and view.allowed_triggers == ()


def test_a_trial_runs_start_to_conversion(app):
    catalog, pricing, account_id = _commercial_context(app)
    subscription = app.commands.dispatch(
        CreateSubscription(
            **ORG,
            account_id=account_id,
            catalog_version=catalog,
            pricing_version=pricing,
            currency=INR,
            trial=TrialPolicy(enabled=True, length_days=14),
        )
    )
    app.commands.dispatch(
        AddSubscriptionItem(
            **ORG, subscription_id=subscription.id, price_code="pro", price_version=1
        )
    )
    app.commands.dispatch(StartSubscriptionTrial(**ORG, subscription_id=subscription.id))
    view = app.queries.dispatch(GetCommercialSubscription(**ORG, subscription_id=subscription.id))
    assert view.state == "trialing" and view.trial.ends_at is not None

    app.commands.dispatch(ConvertSubscriptionTrial(**ORG, subscription_id=subscription.id))
    view = app.queries.dispatch(GetCommercialSubscription(**ORG, subscription_id=subscription.id))
    assert view.state == "active" and view.cycle.current_period is not None


def test_converting_an_empty_trial_is_refused(app):
    catalog, pricing, account_id = _commercial_context(app)
    subscription = app.commands.dispatch(
        CreateSubscription(
            **ORG,
            account_id=account_id,
            catalog_version=catalog,
            pricing_version=pricing,
            currency=INR,
            trial=TrialPolicy(enabled=True, length_days=7),
        )
    )
    app.commands.dispatch(StartSubscriptionTrial(**ORG, subscription_id=subscription.id))
    with pytest.raises(BillingConflict) as excinfo:
        app.commands.dispatch(ConvertSubscriptionTrial(**ORG, subscription_id=subscription.id))
    assert "no items" in str(excinfo.value)


def test_a_trial_can_expire_instead_of_converting(app):
    catalog, pricing, account_id = _commercial_context(app)
    subscription = app.commands.dispatch(
        CreateSubscription(
            **ORG,
            account_id=account_id,
            catalog_version=catalog,
            pricing_version=pricing,
            currency=INR,
            trial=TrialPolicy(enabled=True, length_days=7),
        )
    )
    app.commands.dispatch(StartSubscriptionTrial(**ORG, subscription_id=subscription.id))
    app.commands.dispatch(ExpireSubscriptionTrial(**ORG, subscription_id=subscription.id))
    assert (
        app.queries.dispatch(
            GetCommercialSubscription(**ORG, subscription_id=subscription.id)
        ).state
        == "expired"
    )


def test_hybrid_items_live_on_one_agreement(app):
    subscription, *_ = _seed(app)
    app.commands.dispatch(
        AddSubscriptionItem(
            **ORG,
            subscription_id=subscription.id,
            price_code="minutes",
            price_version=1,
            kind=ItemKind.USAGE,
            meter_code="voice_minutes",
        )
    )
    app.commands.dispatch(
        AddSubscriptionItem(
            **ORG,
            subscription_id=subscription.id,
            price_code="platform",
            price_version=1,
            kind=ItemKind.FLAT,
        )
    )
    view = app.queries.dispatch(GetCommercialSubscription(**ORG, subscription_id=subscription.id))
    assert view.is_hybrid and view.seat_count == 10
    assert view.metered_codes == ("voice_minutes",)
    assert len(view.items) == 3


def test_items_can_be_amended_and_removed(app):
    subscription, *_ = _seed(app)
    view = app.queries.dispatch(GetCommercialSubscription(**ORG, subscription_id=subscription.id))
    item_id = view.items[0].id
    app.commands.dispatch(
        UpdateSubscriptionItem(**ORG, subscription_id=subscription.id, item_id=item_id, quantity=25)
    )
    view = app.queries.dispatch(GetCommercialSubscription(**ORG, subscription_id=subscription.id))
    assert view.seat_count == 25

    app.commands.dispatch(
        RemoveSubscriptionItem(**ORG, subscription_id=subscription.id, item_id=item_id)
    )
    view = app.queries.dispatch(GetCommercialSubscription(**ORG, subscription_id=subscription.id))
    assert view.items == ()


def test_a_closed_agreement_refuses_item_changes(app):
    subscription, *_ = _seed(app)
    app.commands.dispatch(
        CancelSubscription(
            **ORG, subscription_id=subscription.id, timing=CancellationTiming.IMMEDIATE
        )
    )
    with pytest.raises(BillingConflict):
        app.commands.dispatch(
            AddSubscriptionItem(
                **ORG, subscription_id=subscription.id, price_code="x", price_version=1
            )
        )


def test_renewal_through_the_application(app):
    subscription, *_ = _seed(app)
    before = app.queries.dispatch(GetCommercialSubscription(**ORG, subscription_id=subscription.id))
    app.commands.dispatch(RenewSubscription(**ORG, subscription_id=subscription.id))
    after = app.queries.dispatch(GetCommercialSubscription(**ORG, subscription_id=subscription.id))
    assert after.cycle.cycle_number == before.cycle.cycle_number + 1
    assert after.state == "active"  # not a state change
    assert after.cycle.current_period.start == before.cycle.current_period.end


def test_renewal_stops_at_the_agreed_terms(app):
    subscription, *_ = _seed(app)
    app.commands.dispatch(
        SetRenewalPolicy(
            **ORG,
            subscription_id=subscription.id,
            renewal=RenewalPolicy(mode=RenewalMode.AUTO, terms=1),
        )
    )
    app.commands.dispatch(RenewSubscription(**ORG, subscription_id=subscription.id))
    with pytest.raises(BillingConflict):
        app.commands.dispatch(RenewSubscription(**ORG, subscription_id=subscription.id))


def test_versioning_and_rollback_through_the_application(app):
    subscription, *_ = _seed(app)
    v1 = app.commands.dispatch(
        PublishSubscriptionVersion(**ORG, subscription_id=subscription.id, reason="initial")
    )
    view = app.queries.dispatch(GetCommercialSubscription(**ORG, subscription_id=subscription.id))
    app.commands.dispatch(
        UpdateSubscriptionItem(
            **ORG, subscription_id=subscription.id, item_id=view.items[0].id, quantity=50
        )
    )
    app.commands.dispatch(
        PublishSubscriptionVersion(**ORG, subscription_id=subscription.id, reason="seat increase")
    )
    rolled = app.commands.dispatch(
        RollbackSubscription(**ORG, subscription_id=subscription.id, to_version=v1.version)
    )
    assert rolled.version == 3 and rolled.rolled_back_from == 1

    history = app.queries.dispatch(
        SubscriptionVersionHistory(**ORG, subscription_id=subscription.id)
    )
    assert [h.version for h in history] == [3, 2, 1]  # the middle version survives
    restored = app.queries.dispatch(
        GetCommercialSubscription(**ORG, subscription_id=subscription.id)
    )
    assert restored.seat_count == 10  # earlier terms are back


def test_a_version_can_never_be_republished(app):
    subscription, *_ = _seed(app)
    app.commands.dispatch(PublishSubscriptionVersion(**ORG, subscription_id=subscription.id))
    repo = app.context.subscriptions_engine.versions
    latest = repo.latest("org_1", subscription.id)
    with pytest.raises(ImmutabilityViolation):
        repo.publish(latest)


def test_rolling_back_to_an_unknown_version_is_not_found(app):
    from billing.foundation import BillingNotFound

    subscription, *_ = _seed(app)
    with pytest.raises(BillingNotFound):
        app.commands.dispatch(
            RollbackSubscription(**ORG, subscription_id=subscription.id, to_version=99)
        )


def test_a_historical_snapshot_reads_the_frozen_version(app):
    """Changing the terms after a version must not change what that version says."""
    subscription, *_ = _seed(app)
    app.commands.dispatch(PublishSubscriptionVersion(**ORG, subscription_id=subscription.id))
    view = app.queries.dispatch(GetCommercialSubscription(**ORG, subscription_id=subscription.id))
    app.commands.dispatch(
        UpdateSubscriptionItem(
            **ORG, subscription_id=subscription.id, item_id=view.items[0].id, quantity=99
        )
    )
    historical = app.queries.dispatch(
        GetSubscriptionSnapshot(**ORG, subscription_id=subscription.id, at=100.0, version=1)
    )
    live = app.queries.dispatch(
        GetSubscriptionSnapshot(**ORG, subscription_id=subscription.id, at=100.0)
    )
    assert historical.seat_count == 10 and live.seat_count == 99


def test_scheduled_changes_are_recorded_and_never_applied(app):
    subscription, catalog, pricing, _ = _seed(app)
    app.commands.dispatch(
        ScheduleSubscriptionChange(
            **ORG,
            subscription_id=subscription.id,
            kind=ScheduledChangeKind.DOWNGRADE,
            effective_at=9_000_000.0,
            target_pricing_version=pricing,
            reason="smaller plan at renewal",
        )
    )
    changes = app.queries.dispatch(ListScheduledChanges(**ORG, subscription_id=subscription.id))
    assert len(changes) == 1 and changes[0].status == "pending"

    # Reading due changes at a far-future instant applies nothing.
    due = app.queries.dispatch(
        ListScheduledChanges(**ORG, subscription_id=subscription.id, due_at=9_999_999.0)
    )
    assert len(due) == 1 and due[0].status == "pending"
    view = app.queries.dispatch(GetCommercialSubscription(**ORG, subscription_id=subscription.id))
    assert view.state == "active" and view.pricing_version == pricing


def test_a_scheduled_change_can_be_cancelled(app):
    subscription, *_ = _seed(app)
    app.commands.dispatch(
        ScheduleSubscriptionChange(
            **ORG,
            subscription_id=subscription.id,
            kind=ScheduledChangeKind.UPGRADE,
            effective_at=9_000_000.0,
            target_plan_code="enterprise",
        )
    )
    changes = app.queries.dispatch(ListScheduledChanges(**ORG, subscription_id=subscription.id))
    app.commands.dispatch(
        CancelScheduledChange(**ORG, subscription_id=subscription.id, change_id=changes[0].id)
    )
    assert app.queries.dispatch(ListScheduledChanges(**ORG, subscription_id=subscription.id)) == ()


def test_search_through_the_application(app):
    subscription, catalog, pricing, account_id = _seed(app)
    app.commands.dispatch(
        CreateSubscription(
            **ORG,
            account_id=account_id,
            catalog_version=catalog,
            pricing_version=pricing,
            currency=INR,
        )
    )
    result = app.queries.dispatch(SearchSubscriptions(**ORG))
    assert result.total == 2
    live = app.queries.dispatch(
        SearchSubscriptions(**ORG, query=SubscriptionSearchQuery(live_only=True))
    )
    assert live.total == 1


def test_every_command_appends_to_the_timeline(app):
    subscription, *_ = _seed(app)
    app.commands.dispatch(RenewSubscription(**ORG, subscription_id=subscription.id))
    actions = {
        a.action
        for a in app.queries.dispatch(SubscriptionTimeline(**ORG, subscription_id=subscription.id))
    }
    assert actions == {
        "subscription.created",
        "subscription.item_added",
        "subscription.activated",
        "subscription.renewed",
    }


def test_the_timeline_records_the_states_a_transition_moved_between(app):
    subscription, *_ = _seed(app, activate=False)
    app.commands.dispatch(ActivateSubscription(**ORG, subscription_id=subscription.id))
    entries = app.queries.dispatch(SubscriptionTimeline(**ORG, subscription_id=subscription.id))
    activation = next(e for e in entries if e.action == "subscription.activated")
    assert activation.from_state == "draft" and activation.to_state == "active"


def test_a_failed_command_leaves_no_history_behind(app):
    subscription, *_ = _seed(app)
    before = len(app.queries.dispatch(SubscriptionTimeline(**ORG, subscription_id=subscription.id)))
    with pytest.raises(InvalidBillingTransition):
        app.commands.dispatch(ResumeSubscription(**ORG, subscription_id=subscription.id))
    assert (
        len(app.queries.dispatch(SubscriptionTimeline(**ORG, subscription_id=subscription.id)))
        == before
    )


# ── 9. isolation, authorization, durability ─────────────────────────────────────


def test_cross_org_isolation(app):
    subscription, *_ = _seed(app)
    other = dict(actor_id="usr_2", org_id="org_2")
    assert (
        app.queries.dispatch(GetCommercialSubscription(**other, subscription_id=subscription.id))
        is None
    )
    assert app.queries.dispatch(SearchSubscriptions(**other)).total == 0
    assert (
        app.queries.dispatch(SubscriptionTimeline(**other, subscription_id=subscription.id)) == ()
    )
    assert (
        app.queries.dispatch(ListScheduledChanges(**other, subscription_id=subscription.id)) == ()
    )


def test_subscription_numbers_are_sequenced_per_organization(app):
    subscription, *_ = _seed(app)
    assert subscription.subscription_number == "SUB-000001"
    # A second organization starts its own sequence — asserted through the allocator directly,
    # since building a whole commercial context for org_2 is not what this test is about.
    allocator = app.context.subscriptions_engine.numbers
    assert allocator.next_number("org_2") == "SUB-000001"


def test_every_subscription_command_and_query_is_authorized():
    from billing.application import ALL_SUBSCRIPTION_COMMANDS, ALL_SUBSCRIPTION_QUERIES

    wired = build_persistent_billing_application(
        ids=SequentialIdGenerator(), clock=FixedClock(), authorizer=DenyAllAuthorizer()
    )
    app = wired.application
    for message in ALL_SUBSCRIPTION_COMMANDS:
        with pytest.raises(BillingAuthorizationError):
            app.commands.dispatch(message(**ORG))
    for message in ALL_SUBSCRIPTION_QUERIES:
        with pytest.raises(BillingAuthorizationError):
            app.queries.dispatch(message(**ORG))
    wired.database.close()


def test_subscriptions_and_history_survive_a_restart():
    database = BillingDatabase("sqlite://")
    first = build_persistent_billing_application(
        database=database,
        ids=SequentialIdGenerator(),
        clock=FixedClock(),
        authorizer=AllowAllAuthorizer(),
    )
    subscription, catalog, pricing, _ = _seed(first.application)
    first.application.commands.dispatch(
        PublishSubscriptionVersion(**ORG, subscription_id=subscription.id, reason="kept")
    )

    second = build_persistent_billing_application(
        database=database,
        create_all=False,
        ids=DefaultIdGenerator(),
        clock=FixedClock(),
        authorizer=AllowAllAuthorizer(),
    )
    app = second.application
    view = app.queries.dispatch(GetCommercialSubscription(**ORG, subscription_number="SUB-000001"))
    assert view.state == "active"
    assert (view.catalog_version, view.pricing_version) == (catalog, pricing)
    assert view.seat_count == 10
    versions = app.queries.dispatch(
        SubscriptionVersionHistory(**ORG, subscription_id=subscription.id)
    )
    assert [v.version for v in versions] == [1] and versions[0].reason == "kept"
    assert (
        len(app.queries.dispatch(SubscriptionTimeline(**ORG, subscription_id=subscription.id))) == 4
    )
    database.close()


def test_subscription_tables_are_created_by_the_migration_chain():
    from sqlalchemy import inspect

    from billing.persistence.migrations import MIGRATIONS, v0019_billing_subscriptions
    from billing.persistence.subscription_models import SUBSCRIPTION_TABLE_NAMES

    assert MIGRATIONS[6] is v0019_billing_subscriptions
    assert v0019_billing_subscriptions.VERSION == "0019"
    versions = [m.VERSION for m in MIGRATIONS]
    assert versions == sorted(versions)
    for earlier, later in zip(versions[:-1], versions[1:], strict=True):
        assert int(later) == int(earlier) + 1

    database = BillingDatabase("sqlite://")
    for module in MIGRATIONS:
        module.upgrade(database)
    tables = set(inspect(database.engine).get_table_names())
    assert set(SUBSCRIPTION_TABLE_NAMES) <= tables
    assert all(n.startswith("cp_billing_") for n in SUBSCRIPTION_TABLE_NAMES)
    # M9.2's own subscription table is untouched and still there.
    assert "cp_billing_subscription" in tables
    database.close()


def test_subscription_migration_is_idempotent_and_alters_nothing():
    from sqlalchemy import inspect

    from billing.persistence.migrations import v0019_billing_subscriptions

    source = Path(v0019_billing_subscriptions.__file__).read_text()
    assert "ALTER TABLE" not in source and "DROP" not in source

    database = BillingDatabase("sqlite://")
    v0019_billing_subscriptions.upgrade(database)
    before = set(inspect(database.engine).get_table_names())
    v0019_billing_subscriptions.upgrade(database)
    assert set(inspect(database.engine).get_table_names()) == before
    database.close()


def test_append_only_subscription_stores_expose_no_mutation_path():
    from billing.foundation import SubscriptionActivityRepository, SubscriptionVersionRepository

    for contract in (SubscriptionVersionRepository, SubscriptionActivityRepository):
        methods = {n for n in dir(contract) if not n.startswith("_")}
        assert not methods & {"update", "delete", "remove", "replace", "amend"}, contract.__name__


def test_round_trip_fidelity_for_the_subscription_aggregate():
    """`decode(type(a), encode(a)) == a` — the mapping contract every Billing aggregate holds."""
    from billing.persistence.codec import decode, encode

    subscription = _subscription(
        items=[
            _item(kind=ItemKind.SEAT, quantity=9),
            _item("min", meter="voice", kind=ItemKind.USAGE),
        ],
        cycle=BillingCycle(current_period=BillingPeriod(0.0, 1000.0)),
        trial=TrialPolicy(enabled=True, length_days=14, ends_at=500.0),
        renewal=RenewalPolicy(mode=RenewalMode.MANUAL, terms=3, terms_used=1),
        scheduled_changes=(_change(),),
    )
    assert decode(CommercialSubscription, encode(subscription)) == subscription


# ── 10. HTTP surface ────────────────────────────────────────────────────────────


def _http_context(client) -> tuple[int, int, str]:
    account = client.post(
        "/v1/billing/billing-accounts",
        headers=_auth(),
        json={
            "display_name": "Acme",
            "currency": "INR",
            "contacts": [{"role": "billing", "email": "ap@acme.test"}],
            "addresses": [
                {"kind": "billing", "line1": "1 MG Road", "city": "Bengaluru", "country": "IN"}
            ],
        },
    ).json()
    client.post(f"/v1/billing/billing-accounts/{account['id']}/activate", headers=_auth())

    client.post(
        "/v1/billing/catalog/products",
        headers=_auth(),
        json={"code": "voice", "display_name": "Voice"},
    )
    client.post("/v1/billing/catalog/products/voice/publish", headers=_auth())
    catalog = client.post(
        "/v1/billing/catalog/versions", headers=_auth(), json={"notes": ""}
    ).json()

    client.post(
        "/v1/billing/pricing/books",
        headers=_auth(),
        json={"code": "public", "display_name": "Public", "currency": "INR", "effective_from": 0},
    )
    client.post(
        "/v1/billing/pricing/books/public/prices",
        headers=_auth(),
        json={
            "code": "pro",
            "shape": "flat_recurring",
            "amount": {"minor": 999900, "currency": "INR"},
            "interval": "monthly",
        },
    )
    client.post("/v1/billing/pricing/books/public/publish", headers=_auth(), json={"notes": ""})
    pricing = client.post(
        "/v1/billing/pricing/versions",
        headers=_auth(),
        json={"catalog_version": catalog["version"], "notes": ""},
    ).json()
    return catalog["version"], pricing["version"], account["id"]


def _http_seed(client, *, activate=True) -> dict:
    catalog, pricing, account_id = _http_context(client)
    created = client.post(
        PREFIX,
        headers=_auth(),
        json={
            "account_id": account_id,
            "catalog_version": catalog,
            "pricing_version": pricing,
            "currency": "INR",
            "plan_code": "pro",
            "items": [{"price_code": "pro", "price_version": 1, "kind": "seat", "quantity": 10}],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    if activate:
        body = client.post(f"{PREFIX}/{body['id']}/activate", headers=_auth()).json()
    return body


def test_subscription_lifecycle_over_http(client):
    created = _http_seed(client, activate=False)
    assert created["state"] == "draft"
    assert created["subscription_number"] == "SUB-000001"
    assert set(created["allowed_triggers"]) == {"activate", "start_trial", "cancel"}
    assert len(created["items"]) == 1

    activated = client.post(f"{PREFIX}/{created['id']}/activate", headers=_auth()).json()
    assert activated["state"] == "active" and activated["cycle"]["current_period"] is not None

    paused = client.post(
        f"{PREFIX}/{created['id']}/pause", headers=_auth(), json={"reason": "summer"}
    ).json()
    assert paused["state"] == "paused" and paused["pause"]["pauses_used"] == 1


def test_illegal_transition_over_http_is_409(client):
    created = _http_seed(client, activate=False)
    assert client.post(f"{PREFIX}/{created['id']}/resume", headers=_auth()).status_code == 409


def test_a_dangling_pin_over_http_is_422_with_diagnostics(client):
    catalog, pricing, account_id = _http_context(client)
    response = client.post(
        PREFIX,
        headers=_auth(),
        json={
            "account_id": account_id,
            "catalog_version": 99,
            "pricing_version": pricing,
            "currency": "INR",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["diagnostics"]


def test_there_is_no_delete_on_a_subscription_at_any_path(client):
    """A subscription is the commercial agreement itself. Its record is permanent."""
    schema = client.get("/openapi.json").json()
    for path, verbs in schema["paths"].items():
        if path.startswith(PREFIX) and "delete" in verbs:
            # Only sub-resources may be deleted — an item, or a *pending intention*.
            assert path.endswith(("/items/{item_id}", "/scheduled-changes/{change_id}")), path
    assert "delete" not in schema["paths"][f"{PREFIX}/{{identifier}}"]


def test_versions_have_no_mutation_verbs(client):
    schema = client.get("/openapi.json").json()
    verbs = schema["paths"][f"{PREFIX}/{{identifier}}/versions"]
    assert not ({"put", "patch", "delete"} & set(verbs))


def test_items_over_http(client):
    created = _http_seed(client)
    added = client.post(
        f"{PREFIX}/{created['id']}/items",
        headers=_auth(),
        json={"price_code": "minutes", "price_version": 1, "kind": "usage", "meter_code": "voice"},
    )
    assert added.status_code == 201
    body = added.json()
    assert body["is_hybrid"] and body["metered_codes"] == ["voice"]

    item_id = body["items"][0]["id"]
    patched = client.patch(
        f"{PREFIX}/{created['id']}/items/{item_id}", headers=_auth(), json={"quantity": 30}
    ).json()
    assert patched["seat_count"] == 30
    removed = client.delete(f"{PREFIX}/{created['id']}/items/{item_id}", headers=_auth()).json()
    assert len(removed["items"]) == 1


def test_a_metered_item_without_a_meter_is_422(client):
    created = _http_seed(client)
    response = client.post(
        f"{PREFIX}/{created['id']}/items",
        headers=_auth(),
        json={"price_code": "minutes", "price_version": 1, "kind": "usage"},
    )
    assert response.status_code == 422


def test_trial_over_http(client):
    catalog, pricing, account_id = _http_context(client)
    created = client.post(
        PREFIX,
        headers=_auth(),
        json={
            "account_id": account_id,
            "catalog_version": catalog,
            "pricing_version": pricing,
            "currency": "INR",
            "items": [{"price_code": "pro", "price_version": 1}],
            "trial": {"enabled": True, "length_days": 14},
        },
    ).json()
    started = client.post(
        f"{PREFIX}/{created['id']}/trial/start", headers=_auth(), json={"length_days": 0}
    ).json()
    assert started["state"] == "trialing" and started["trial"]["ends_at"] is not None
    converted = client.post(f"{PREFIX}/{created['id']}/trial/convert", headers=_auth()).json()
    assert converted["state"] == "active"


def test_renewal_over_http(client):
    created = _http_seed(client)
    renewed = client.post(f"{PREFIX}/{created['id']}/renew", headers=_auth()).json()
    assert renewed["cycle"]["cycle_number"] == 2 and renewed["state"] == "active"

    policy = client.patch(
        f"{PREFIX}/{created['id']}/renewal",
        headers=_auth(),
        json={"mode": "none", "notice_days": 0, "terms": None},
    ).json()
    assert policy["renewal"]["mode"] == "none" and policy["renewal"]["renews"] is False
    assert client.post(f"{PREFIX}/{created['id']}/renew", headers=_auth()).status_code == 409


def test_scheduled_changes_over_http(client):
    created = _http_seed(client)
    scheduled = client.post(
        f"{PREFIX}/{created['id']}/scheduled-changes",
        headers=_auth(),
        json={
            "kind": "cancellation",
            "effective_at": 9_000_000.0,
            "reason": "end of term",
            "items": [],
        },
    )
    assert scheduled.status_code == 201
    listed = client.get(f"{PREFIX}/{created['id']}/scheduled-changes", headers=_auth()).json()
    assert len(listed) == 1 and listed[0]["status"] == "pending"

    # A second cancellation conflicts.
    conflict = client.post(
        f"{PREFIX}/{created['id']}/scheduled-changes",
        headers=_auth(),
        json={"kind": "cancellation", "effective_at": 9_500_000.0, "items": []},
    )
    assert conflict.status_code == 422

    removed = client.delete(
        f"{PREFIX}/{created['id']}/scheduled-changes/{listed[0]['id']}", headers=_auth()
    )
    assert removed.status_code == 200
    assert client.get(f"{PREFIX}/{created['id']}/scheduled-changes", headers=_auth()).json() == []


def test_versions_and_rollback_over_http(client):
    created = _http_seed(client)
    first = client.post(
        f"{PREFIX}/{created['id']}/versions", headers=_auth(), json={"reason": "initial"}
    )
    assert first.status_code == 201 and first.json()["version"] == 1
    client.post(f"{PREFIX}/{created['id']}/versions", headers=_auth(), json={"reason": "second"})
    rolled = client.post(
        f"{PREFIX}/{created['id']}/versions/rollback",
        headers=_auth(),
        json={"to_version": 1, "reason": ""},
    )
    assert rolled.status_code == 201
    assert rolled.json()["version"] == 3 and rolled.json()["rolled_back_from"] == 1
    history = client.get(f"{PREFIX}/{created['id']}/versions", headers=_auth()).json()
    assert [h["version"] for h in history] == [3, 2, 1]


def test_snapshot_over_http(client):
    created = _http_seed(client)
    client.post(f"{PREFIX}/{created['id']}/versions", headers=_auth(), json={"reason": ""})
    snapshot = client.get(
        f"{PREFIX}/{created['id']}/snapshot?at=100.0&version=1", headers=_auth()
    ).json()
    assert snapshot["is_reproducible"] and snapshot["seat_count"] == 10
    assert snapshot["catalog_version"] == created["catalog_version"]


def test_timeline_over_http(client):
    created = _http_seed(client)
    timeline = client.get(f"{PREFIX}/{created['id']}/timeline", headers=_auth()).json()
    assert {t["action"] for t in timeline} == {"subscription.created", "subscription.activated"}


def test_search_over_http(client):
    _http_seed(client)
    body = client.get(f"{PREFIX}?limit=10", headers=_auth()).json()
    assert body["total"] == 1
    assert body["subscriptions"][0]["subscription_number"] == "SUB-000001"
    live = client.get(f"{PREFIX}?live_only=true", headers=_auth()).json()
    assert live["total"] == 1


def test_a_subscription_is_addressable_by_number_over_http(client):
    _http_seed(client)
    response = client.get(f"{PREFIX}/SUB-000001", headers=_auth())
    assert response.status_code == 200 and response.json()["subscription_number"] == "SUB-000001"


def test_no_amount_appears_anywhere_on_the_wire(client):
    """A subscription says what was agreed. What it costs is resolved against pricing v1."""
    created = _http_seed(client)
    body = client.get(f"{PREFIX}/{created['id']}", headers=_auth()).text
    for forbidden in ('"minor"', '"amount"', '"currency":"INR","minor"', '"net_amount"'):
        assert forbidden not in body, forbidden


def test_subscription_routes_require_identity_and_an_org(client):
    assert client.get(PREFIX, headers={}).status_code == 401
    assert client.get(PREFIX, headers=_auth(org_id="")).status_code == 401


def test_subscription_authorization_denial_is_403():
    with _client(authorizer=DenyAllAuthorizer()) as denied:
        response = denied.get(PREFIX, headers=_auth())
        assert response.status_code == 403


def test_cross_org_isolation_over_http(client):
    created = _http_seed(client)
    assert client.get(f"{PREFIX}/{created['id']}", headers=_auth(org_id="org_2")).status_code == 404


def test_the_organization_always_comes_from_the_token(client):
    catalog, pricing, account_id = _http_context(client)
    client.post(
        PREFIX,
        headers=_auth(org_id="org_1"),
        json={
            "account_id": account_id,
            "catalog_version": catalog,
            "pricing_version": pricing,
            "currency": "INR",
            "org_id": "org_hacker",
        },
    )
    assert client.get(f"{PREFIX}/SUB-000001", headers=_auth(org_id="org_hacker")).status_code == 404
    assert client.get(f"{PREFIX}/SUB-000001", headers=_auth(org_id="org_1")).status_code == 200


def test_subscription_openapi_is_tagged_and_documents_errors(client):
    schema = client.get("/openapi.json").json()
    create = schema["paths"][PREFIX]["post"]
    assert create["tags"] == ["subscriptions"]
    assert {"401", "403", "404", "409", "422"} <= set(create["responses"])


def test_the_m9_5_subscription_surface_is_untouched(client):
    paths = set(client.get("/openapi.json").json()["paths"])
    assert "/v1/billing/subscriptions" in paths
    assert "/v1/billing/subscriptions/{subscription_id}" in paths
    assert PREFIX in paths
    assert f"{PREFIX}/{{identifier}}" in paths


# ── 11. Studio ──────────────────────────────────────────────────────────────────


STUDIO = BILLING / "customer" / "studio" / "subscriptions.html"


def test_subscription_studio_is_served(client):
    response = client.get("/studio/subscriptions.html")
    assert response.status_code == 200
    assert "Subscriptions" in response.text


def test_subscription_studio_is_self_contained():
    html = STUDIO.read_text()
    for forbidden in ("http://", "https://", "cdn.", "<script src", "@import"):
        assert forbidden not in html, forbidden


def test_subscription_studio_has_a_screen_for_every_required_area():
    tabs = dict(_studio_tabs(STUDIO.read_text()))
    for required in (
        "dashboard",
        "details",
        "items",
        "timeline",
        "versions",
        "schedule",
        "trial",
        "renewal",
        "lifecycle",
        "activity",
    ):
        assert required in tabs, required
    assert len(tabs) == 10


def test_subscription_studio_owns_no_state_and_shows_no_money():
    html = STUDIO.read_text()
    assert "localStorage" not in html and "sessionStorage" not in html
    assert "allowed_triggers" in html  # buttons come from the server
    for forbidden in ("function money", "minor / 100", "toFixed(2)"):
        assert forbidden not in html, forbidden


def _studio_tabs(html: str) -> list[tuple[str, str]]:
    import re

    start = html.index("const TABS = [")
    body = html[start : html.index("];", start)]
    return [(m.group(1), m.group(2)) for m in re.finditer(r'\["(\w+)",\s*"([^"]+)"\]', body)]


# ── 12. architecture compliance ─────────────────────────────────────────────────


SUBSCRIPTION_MODULES = (
    BILLING / "foundation" / "commercial_subscriptions.py",
    BILLING / "foundation" / "subscription_rules.py",
    BILLING / "foundation" / "subscription_factories.py",
    BILLING / "application" / "subscriptions_engine.py",
    BILLING / "persistence" / "subscription_models.py",
    BILLING / "persistence" / "subscription_repositories.py",
    BILLING / "customer" / "subscription_schemas.py",
    BILLING / "customer" / "routers" / "subscription_engine.py",
)


def _code_only(path: Path) -> str:
    """Source with docstrings stripped. These scans are about what the domain *is*, and prose
    explaining why it holds no money must not read as holding money."""
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


def test_subscriptions_own_no_money_and_compute_no_charge():
    """The boundary: a subscription records what was agreed; turning that into an amount is
    rating (M9.11)."""
    for path in SUBSCRIPTION_MODULES:
        code = _code_only(path)
        for forbidden in ("Money", "minor", ".times(", ".allocate(", ".scaled(", "PriceTier"):
            assert forbidden not in code, (path.name, forbidden)


def test_subscriptions_own_no_catalog_pricing_usage_ledger_or_payment():
    for path in SUBSCRIPTION_MODULES:
        code = _code_only(path)
        for forbidden in (
            "CatalogVersion",
            "PricingVersion",
            "PriceBookVersion",
            "ResolvedPrice",
            "Product(",
            "UsageRecord",
            "UsageAggregate",
            "LedgerEntry",
            "Invoice(",
            "Payment(",
            "Refund(",
            "TaxDetermination",
            "CreditGrant",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_subscriptions_reference_snapshots_by_version_number_only():
    """The pins are integers into frozen snapshots, never live objects — which is what stops a
    catalog or pricing change reaching backwards into an existing agreement."""
    from billing.foundation import CommercialSubscription as S

    hints = {f.name: f.type for f in S.__dataclass_fields__.values()}
    assert hints["catalog_version"] == "int"
    assert hints["pricing_version"] == "int"
    assert hints["account_id"] == "str"


def test_subscriptions_depend_on_no_runtime_or_deployment_plane():
    """An *import* check rather than a substring one: `from fastapi import APIRouter` contains
    the letters "import api" and means nothing of the sort. The rule is that no module from
    another plane is imported at all."""
    forbidden_roots = {"api", "runtime", "deployment", "knowledge", "observability", "product"}
    for path in SUBSCRIPTION_MODULES:
        tree = ast.parse(path.read_text())
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
        assert not (roots & forbidden_roots), (path.name, roots & forbidden_roots)


def test_there_is_no_scheduler_in_this_domain():
    """Intent is persisted; execution is a caller's explicit command. A timer here would make
    'what we agreed' and 'what we ran' the same record."""
    for path in SUBSCRIPTION_MODULES:
        code = _code_only(path)
        for forbidden in (
            "threading",
            "asyncio.sleep",
            "schedule.every",
            "cron",
            "BackgroundTasks",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_subscription_foundation_stays_pure():
    for path in (
        BILLING / "foundation" / "commercial_subscriptions.py",
        BILLING / "foundation" / "subscription_rules.py",
        BILLING / "foundation" / "subscription_factories.py",
    ):
        source = path.read_text().lower()
        for forbidden in ("sqlmodel", "sqlalchemy", "fastapi", "pydantic", "session.", "select("):
            assert forbidden not in source, (path.name, forbidden)


def test_the_subscription_foundation_reads_no_clock():
    """Every temporal decision takes `at` as an argument — the property that makes a renewal
    reproducible in a test and in a billing run alike."""
    source = (BILLING / "foundation" / "subscription_rules.py").read_text()
    for forbidden in ("time.time", "datetime.now", "self._clock", "SystemClock"):
        assert forbidden not in source, forbidden


def test_subscription_layers_import_only_downward():
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
        BILLING / "foundation" / "commercial_subscriptions.py",
        BILLING / "foundation" / "subscription_rules.py",
        BILLING / "foundation" / "subscription_factories.py",
    ):
        assert imports(path) <= allowed, (path.name, imports(path))

    app_imports = imports(BILLING / "application" / "subscriptions_engine.py")
    assert "sqlmodel" not in app_imports and "fastapi" not in app_imports


def test_subscription_use_cases_contain_no_rule():
    source = (BILLING / "application" / "subscriptions_engine.py").read_text()
    for forbidden in (
        "def validate_",
        "AGREEMENT_LIFECYCLE",
        "def next_period",
        "def is_expired",
        "INTERVAL_SECONDS",
        "def conflicts_with",
    ):
        assert forbidden not in source, forbidden


def test_subscription_controllers_stay_transport_only():
    tree = ast.parse((BILLING / "customer" / "routers" / "subscription_engine.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name not in ("_response", "_items"):
            # Strip the docstring: prose explaining that the *Foundation* validates must not
            # read as the controller validating.
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
            ):
                node.body = node.body[1:]
            body = ast.unparse(node)
            assert "commands.dispatch" in body or "queries.dispatch" in body, node.name
            for forbidden in ("repositories", "session", ".commit()", "uow.", "validate"):
                assert forbidden not in body, (node.name, forbidden)


def test_repositories_decide_no_lifecycle_and_advance_no_cycle():
    source = _code_only(BILLING / "persistence" / "subscription_repositories.py")
    for forbidden in (
        "AgreementLifecycleService",
        "BillingCycleService",
        "TrialService",
        "ScheduleService",
        "next_period",
        "is_due",
    ):
        assert forbidden not in source, forbidden


def test_command_and_query_coverage_is_complete():
    from billing.application import ALL_SUBSCRIPTION_COMMANDS, ALL_SUBSCRIPTION_QUERIES

    wired = build_persistent_billing_application(
        ids=SequentialIdGenerator(), clock=FixedClock(), authorizer=AllowAllAuthorizer()
    )
    for message in (*ALL_SUBSCRIPTION_COMMANDS, *ALL_SUBSCRIPTION_QUERIES):
        bus = (
            wired.application.commands
            if message in ALL_SUBSCRIPTION_COMMANDS
            else wired.application.queries
        )
        assert message in bus._handlers, message.__name__  # noqa: SLF001
    assert len(ALL_SUBSCRIPTION_COMMANDS) == 23
    assert len(ALL_SUBSCRIPTION_QUERIES) == 6
    wired.database.close()


def test_the_subscription_engine_is_additive_and_optional():
    from billing.application import BillingRepositories
    from billing.application.context import BillingContext

    fields = {f.name for f in BillingRepositories.__dataclass_fields__.values()}
    assert "subscriptions_engine" in fields
    assert BillingRepositories.__dataclass_fields__["subscriptions_engine"].default is None
    assert BillingContext.__dataclass_fields__["subscriptions_engine"].default is None


def test_frozen_lower_layers_kept_their_surfaces():
    from billing import application, foundation, persistence

    for name in (
        "BillingFactory",
        "Money",
        "Product",
        "CatalogVersion",
        "PricingBook",
        "CommercialAccount",
        "Subscription",
    ):
        assert name in foundation.__all__
    for name in (
        "BillingApplication",
        "CreateProduct",
        "CreatePriceBook",
        "CreateBillingAccount",
        "StartSubscription",
    ):
        assert name in application.__all__
    for name in ("BillingDatabase", "build_persistent_billing_application"):
        assert name in persistence.__all__
    assert len(foundation.__all__) > 323  # M9.8 left it at 323; growth only


def test_the_earlier_domain_boundaries_still_hold():
    """M9.6's, M9.7's, and M9.8's boundaries must survive the arrival of a fourth domain."""
    for path in (BILLING / "foundation" / "products.py", BILLING / "application" / "catalog.py"):
        assert "Money" not in _code_only(path), path.name
    for path in (BILLING / "foundation" / "price_books.py", BILLING / "application" / "pricing.py"):
        for forbidden in (".times(", ".allocate("):
            assert forbidden not in _code_only(path), (path.name, forbidden)
    for path in (
        BILLING / "foundation" / "commercial_accounts.py",
        BILLING / "application" / "billing_accounts.py",
    ):
        for forbidden in ("Organization", "Workspace", "CommercialSubscription"):
            assert forbidden not in _code_only(path), (path.name, forbidden)


def test_billing_is_still_absent_from_the_runtime_composition_root():
    assert "billing" not in (PLATFORM / "bootstrap.py").read_text().lower()
