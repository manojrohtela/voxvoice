"""Billing Foundation tests (M9.2) — exhaustive, deterministic, offline.

Every test in this module runs with no socket, no database, no vendor call, and no wall
clock. It verifies the Foundation's *architecture* as strictly as its behaviour:
immutability, determinism, validation-returns-diagnostics, ownership (ADR-0023),
repository boundaries, dependency direction, and the ADR-0031 import rules IR1–IR9 as
executable AST assertions.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import pathlib
import pkgutil

import pytest

import billing
from billing.foundation import (
    ALL_LIFECYCLES,
    BILLING_RUN_ORDER,
    IMMUTABLE_INVOICE_STATES,
    AccountKind,
    BillingDiagnostics,
    BillingEventType,
    BillingFactory,
    BillingPeriod,
    BillingRun,
    BillingRunStage,
    BillingValidationError,
    BillingValidator,
    ChargeLine,
    ChargeLineKind,
    CommercialCatalog,
    Commitment,
    CommitmentKind,
    Contract,
    ContractService,
    ContractTerm,
    CreditBalance,
    CreditGrant,
    CreditKind,
    CreditMovement,
    Currency,
    CurrencyMismatch,
    DefineMeterRequest,
    DefinePlanRequest,
    DunningPolicy,
    EffectiveRange,
    Entitlement,
    EntitlementGate,
    EntitlementGrant,
    EntitlementResolver,
    EntitlementSnapshot,
    Feature,
    FeatureKind,
    FixedClock,
    FrozenClock,
    GateClassification,
    GrantSource,
    ImmutabilityViolation,
    InvalidBillingTransition,
    InvalidMoney,
    Invoice,
    InvoiceNumberAllocator,
    InvoiceNumberFormatter,
    InvoiceState,
    InvoiceTotalsService,
    IssueInvoiceRequest,
    LedgerBalance,
    LedgerDirection,
    LedgerEntry,
    LedgerEntryType,
    License,
    LicenseKind,
    MeterAggregation,
    MeteringService,
    MeterSource,
    Money,
    OpenAccountRequest,
    Payment,
    PaymentMethodKind,
    PeriodService,
    Plan,
    PlanFeature,
    Price,
    PriceBook,
    PriceShape,
    PriceTier,
    ProrationBehaviour,
    ProrationService,
    Quantity,
    QuotaService,
    RecordingEventEmitter,
    RecordUsageRequest,
    Refund,
    RefundPolicy,
    RefundReason,
    Rounding,
    Scope,
    SequentialIdGenerator,
    StartSubscriptionRequest,
    SubscriptionLifecycleService,
    SubscriptionState,
    SubscriptionTrigger,
    TaxpayerKind,
    TaxProfile,
    TaxRegime,
    TaxRegistration,
    Tier,
    UsageRecord,
    UsageSource,
    fold,
)
from billing.foundation.domain import BillingInterval
from billing.foundation.subscription import SubscriptionItem

INR = Currency.INR
USD = Currency.USD


def _factory() -> tuple[BillingFactory, RecordingEventEmitter]:
    emitter = RecordingEventEmitter()
    return (
        BillingFactory(ids=SequentialIdGenerator(), clock=FixedClock(), emitter=emitter),
        emitter,
    )


class _Allocator(InvoiceNumberAllocator):
    """A deterministic gapless allocator — the seam a real System of Record implements."""

    def __init__(self) -> None:
        self._n: dict[tuple[str, str], int] = {}

    def allocate(self, entity_code: str, jurisdiction: str) -> int:
        key = (entity_code, jurisdiction)
        self._n[key] = self._n.get(key, 0) + 1
        return self._n[key]


# ── money: the invariants that cannot be retrofitted ────────────────────────────


def test_money_is_integer_minor_units_only():
    with pytest.raises(InvalidMoney):
        Money(10.5, INR)  # type: ignore[arg-type]
    with pytest.raises(InvalidMoney):
        Money(True, INR)  # type: ignore[arg-type]
    assert Money.of(12, 50, INR).minor == 1250


def test_money_never_crosses_currencies():
    with pytest.raises(CurrencyMismatch):
        Money(100, INR) + Money(100, USD)
    with pytest.raises(CurrencyMismatch):
        Money(100, INR) - Money(1, USD)


def test_money_scaling_is_exact_and_rounding_is_declared():
    amount = Money(1000, INR)
    assert amount.scaled(1, 3, Rounding.FLOOR).minor == 333
    assert amount.scaled(1, 3, Rounding.CEILING).minor == 334
    assert amount.scaled(1, 2, Rounding.HALF_UP).minor == 500
    assert Money(5, INR).scaled(1, 2, Rounding.HALF_UP).minor == 3
    assert Money(5, INR).scaled(1, 2, Rounding.HALF_EVEN).minor == 2


def test_money_allocation_loses_nothing():
    parts = Money(1000, INR).allocate((1, 1, 1))
    assert sum(p.minor for p in parts) == 1000  # no rounding leakage
    assert [p.minor for p in parts] == [334, 333, 333]


def test_money_times_rejects_non_integer_factors():
    with pytest.raises(InvalidMoney):
        Money(100, INR).times(1.5)  # type: ignore[arg-type]


# ── immutability ────────────────────────────────────────────────────────────────


def _public_dataclasses():
    for name in billing.__all__:
        obj = getattr(billing, name)
        if inspect.isclass(obj) and dataclasses.is_dataclass(obj):
            yield name, obj


def test_every_public_aggregate_is_frozen_and_slotted():
    checked = 0
    for name, cls in _public_dataclasses():
        params = cls.__dataclass_params__
        assert params.frozen, f"{name} is a mutable dataclass — aggregates must be immutable"
        assert "__slots__" in cls.__dict__, f"{name} is not slotted"
        checked += 1
    assert checked >= 40


def test_aggregates_reject_mutation():
    factory, _ = _factory()
    account = factory.open_account(
        OpenAccountRequest(org_id="org_1", display_name="Acme", currency=INR)
    ).account
    with pytest.raises(dataclasses.FrozenInstanceError):
        account.display_name = "Other"  # type: ignore[misc]


def test_transitions_return_new_aggregates_and_never_mutate_the_input():
    factory, _ = _factory()
    sub = factory.start_subscription(
        StartSubscriptionRequest(
            org_id="org_1",
            account_id="bac_1",
            plan_code="pro",
            plan_version=1,
            price_book_id="pbk_1",
            price_book_version=1,
            currency=INR,
            interval=BillingInterval.MONTHLY,
            items=(SubscriptionItem("pro_monthly", 1),),
        )
    ).subscription
    lifecycle = SubscriptionLifecycleService(ALL_LIFECYCLES["subscription"])
    activated = lifecycle.apply(sub, SubscriptionTrigger.ACTIVATE, at=100.0)
    assert sub.state is SubscriptionState.PENDING  # untouched
    assert activated.state is SubscriptionState.ACTIVE
    assert activated.started_at == 100.0
    assert activated is not sub


# ── determinism ─────────────────────────────────────────────────────────────────


def test_identical_inputs_produce_identical_aggregates():
    def build():
        factory = BillingFactory(ids=SequentialIdGenerator(), clock=FrozenClock(1000.0))
        return factory.start_subscription(
            StartSubscriptionRequest(
                org_id="org_1",
                account_id="bac_1",
                plan_code="pro",
                plan_version=2,
                price_book_id="pbk_1",
                price_book_version=1,
                currency=INR,
                interval=BillingInterval.MONTHLY,
                items=(SubscriptionItem("pro_monthly", 3),),
            )
        ).subscription

    assert build() == build()


def test_validation_is_deterministically_ordered():
    validator = BillingValidator()
    bad = Plan(id="pln_1", org_id="", code="Bad Code!", display_name="", tier=Tier.PRO)
    first = validator.validate_plan(bad)
    second = validator.validate_plan(bad)
    assert first == second
    assert list(first.findings) == sorted(first.findings)


def test_period_enumeration_is_deterministic():
    periods = PeriodService().periods_between(BillingInterval.MONTHLY, start=0.0, until=100 * 86400)
    assert len(periods) == 4
    assert periods[0].start == 0.0
    assert all(periods[i].end == periods[i + 1].start for i in range(len(periods) - 1))


def test_metering_deduplication_is_order_independent():
    def record(rid: str, event_id: str, units: int) -> UsageRecord:
        return UsageRecord(
            id=rid,
            org_id="org_1",
            account_id="bac_1",
            meter_code="voice_minutes",
            quantity=Quantity(units, "minute"),
            scope=Scope("org_1"),
            period=BillingPeriod(0.0, 100.0),
            source=UsageSource(event_id, "metric.session.completed", 10.0),
        )

    a, b, dup = (
        record("usg_1", "evt_1", 5),
        record("usg_2", "evt_2", 7),
        record("usg_3", "evt_1", 5),
    )
    service = MeteringService()
    forward = service.total((a, b, dup), MeterAggregation.SUM, unit="minute")
    reverse = service.total((dup, b, a), MeterAggregation.SUM, unit="minute")
    assert forward == reverse == Quantity(12, "minute")  # exactly-once from at-least-once


def test_fold_honours_declared_aggregation():
    q = (Quantity(3, "session"), Quantity(9, "session"), Quantity(5, "session"))
    assert fold(MeterAggregation.SUM, q) == Quantity(17, "session")
    assert fold(MeterAggregation.MAX, q) == Quantity(9, "session")
    with pytest.raises(ValueError):
        fold(MeterAggregation.SUM, (Quantity(1, "a"), Quantity(1, "b")))


# ── no side effects, no I/O ─────────────────────────────────────────────────────


def test_foundation_performs_no_io_and_reads_no_ambient_clock():
    forbidden = {
        "socket",
        "http",
        "httpx",
        "requests",
        "urllib",
        "sqlite3",
        "sqlalchemy",
        "sqlmodel",
        "fastapi",
        "starlette",
        "pydantic",
        "random",
        "datetime",
        "os",
    }
    root = pathlib.Path("billing")
    for path in root.rglob("*.py"):
        # M9.4 (ADR-0031 IR1a): `billing/persistence/**` is the one sub-package permitted a
        # storage dependency — that is its entire job. The rule it must still obey is the
        # *direction* one, asserted here and again in the M9.4 suite: the domain layers below
        # it never gain that dependency, so the Foundation stays pure and unaware.
        # M9.4 (IR1a) / M9.5 (IR1b): `persistence` may depend on storage and `customer` on
        # the HTTP framework — that is each one's entire job. The rule they must still obey is
        # the *direction* one, asserted here and again in their own suites: no layer below
        # them ever gains those dependencies, so the domain stays pure and unaware.
        if path.parts[1:2] in (("persistence",), ("customer",)):
            continue
        tree = ast.parse(path.read_text())
        tops: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                tops.add(node.module.split(".")[0])
            elif isinstance(node, ast.Import):
                tops.update(a.name.split(".")[0] for a in node.names)
        leaked = tops & forbidden
        assert not leaked, f"{path} imports forbidden module(s): {sorted(leaked)}"


def test_time_is_only_read_through_the_clock_seam():
    """Inside the domain, `time` may be imported by exactly one module — the seam that
    isolates it, which is what makes every billing decision reproducible.

    The transport layer is scoped out deliberately: request timing and token-expiry checks
    are not domain clock reads, and routing them through an injectable `Clock` would make a
    stopwatch look like a billing decision. `test_transport_reads_time_only_for_transport`
    pins that boundary."""
    users = []
    for path in pathlib.Path("billing/foundation").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(a.name == "time" for a in node.names):
                users.append(path.name)
    assert users == ["seams.py"], f"time imported outside the seam: {users}"


def test_domain_layers_read_no_ambient_clock():
    """The rule above, extended to every layer that makes a billing decision."""
    for layer in ("billing/application", "billing/persistence"):
        for path in pathlib.Path(layer).rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                assert not (
                    isinstance(node, ast.Import) and any(a.name == "time" for a in node.names)
                ), f"{path} reads the ambient clock"


def test_transport_reads_time_only_for_transport():
    """M9.5: the two permitted transport uses, and nothing else — a stopwatch and an expiry
    check. No billing amount, period, or lifecycle instant is ever derived from them."""
    permitted = {"middleware.py", "jwt.py"}
    for path in pathlib.Path("billing/customer").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(a.name == "time" for a in node.names):
                assert path.name in permitted, f"{path} reads the ambient clock"


def test_validation_never_raises():
    validator = BillingValidator()
    nonsense = Plan(id="", org_id="", code="", display_name="", tier=Tier.FREE)
    result = validator.validate_plan(nonsense)
    assert isinstance(result, BillingDiagnostics)
    assert result.has_errors and not result.is_valid


def test_factories_fail_fast_on_error_diagnostics():
    factory, emitter = _factory()
    with pytest.raises(BillingValidationError) as excinfo:
        factory.define_meter(
            DefineMeterRequest(
                code="BAD CODE",
                display_name="",
                unit="",
                aggregation=MeterAggregation.SUM,
                source=MeterSource.RUNTIME_SESSION,
            )
        )
    assert excinfo.value.diagnostics.has_errors
    assert emitter.events == ()  # nothing emitted on a rejected input


# ── ownership (ADR-0023) ────────────────────────────────────────────────────────


def test_billing_never_emits_metric_events():
    """ADR-0023 M8: Billing consumes `metric.*` read-only and produces `billing.*` only."""
    for member in BillingEventType:
        assert member.value.startswith("billing."), member
        assert not member.value.startswith("metric."), member


def test_entitlement_snapshot_performs_no_permission_check():
    """ADR-0023 §11.3: entitlement is commercial capability, never authorization. No
    snapshot method may accept a principal, role, scope, or request."""
    banned = {"principal", "actor", "user", "role", "permission", "request", "subject", "token"}
    for name, member in inspect.getmembers(EntitlementSnapshot, inspect.isfunction):
        if name.startswith("__"):
            continue
        params = set(inspect.signature(member).parameters) - {"self"}
        assert not (params & banned), f"EntitlementSnapshot.{name} takes {params & banned}"


def test_entitlement_resolver_decides_but_never_enforces():
    banned = {"authorize", "enforce", "check_permission", "allow", "deny", "can"}
    names = {n for n, _ in inspect.getmembers(EntitlementResolver, inspect.isfunction)}
    assert not (names & banned), names & banned


def test_usage_record_is_a_projection_keyed_by_its_source_event():
    record = UsageRecord(
        id="usg_1",
        org_id="org_1",
        account_id="bac_1",
        meter_code="tokens",
        quantity=Quantity(100, "token"),
        scope=Scope("org_1"),
        period=BillingPeriod(0.0, 10.0),
        source=UsageSource("evt_9", "metric.model.completed", 1.0),
    )
    assert record.idempotency_key == ("evt_9", "tokens")
    assert record.is_billable


def test_org_isolation_is_structural_in_validation():
    diagnostics = BillingValidator().validate_scope(Scope("org_other"), org_id="org_1")
    assert diagnostics.has_errors
    assert any(d.code.value == "cross_org_reference" for d in diagnostics.errors)


def test_ownership_facts_are_references_not_copies():
    """A billing account references an organization; it never carries organization state."""
    fields = {f.name for f in dataclasses.fields(billing.BillingAccount)}
    assert "org_id" in fields
    for leaked in ("members", "workspaces", "projects", "agents", "roles", "permissions"):
        assert leaked not in fields, f"BillingAccount must not own {leaked}"


# ── lifecycles ──────────────────────────────────────────────────────────────────


def test_every_lifecycle_is_validated_and_has_reachable_terminals():
    for name, definition in ALL_LIFECYCLES.items():
        assert definition.states, f"{name} lifecycle is empty"
        assert definition.terminal_states(), f"{name} lifecycle has no terminal state"


def test_illegal_subscription_transitions_are_rejected_not_coerced():
    lifecycle = SubscriptionLifecycleService(ALL_LIFECYCLES["subscription"])
    assert (
        lifecycle.next_state(SubscriptionState.PENDING, SubscriptionTrigger.ACTIVATE)
        is SubscriptionState.ACTIVE
    )
    with pytest.raises(InvalidBillingTransition):
        lifecycle.next_state(SubscriptionState.EXPIRED, SubscriptionTrigger.ACTIVATE)
    with pytest.raises(InvalidBillingTransition):
        lifecycle.next_state(SubscriptionState.DRAFT, SubscriptionTrigger.PAYMENT_RECOVERED)


def test_terminal_subscription_state_is_immutable():
    lifecycle = SubscriptionLifecycleService(ALL_LIFECYCLES["subscription"])
    assert not lifecycle._definition.triggers_from(SubscriptionState.EXPIRED)  # noqa: SLF001


def test_billing_run_advances_forward_one_stage_at_a_time():
    run = BillingRun(
        id="run_1", org_id="org_1", account_id="bac_1", period=BillingPeriod(0.0, 10.0)
    )
    assert run.can_advance_to(BillingRunStage.PERIOD_CLOSING)
    assert run.can_advance_to(BillingRunStage.PERIOD_OPEN)  # idempotent resume
    assert not run.can_advance_to(BillingRunStage.RATED)  # never skipped
    assert BILLING_RUN_ORDER[-1] is BillingRunStage.PERIOD_CLOSED


def test_dunning_backoff_is_deterministic_and_bounded():
    policy = DunningPolicy()
    assert policy.backoff_for(1) == 86400
    assert policy.backoff_for(3) == 604800
    assert policy.backoff_for(99) == 604800  # last interval repeats, never unbounded
    with pytest.raises(ValueError):
        DunningPolicy(max_attempts=0)


# ── entitlements ────────────────────────────────────────────────────────────────


def _gates() -> tuple[EntitlementGate, ...]:
    return (
        EntitlementGate("sso", FeatureKind.FLAG, GateClassification.FAIL_CLOSED),
        EntitlementGate("workspaces", FeatureKind.LIMIT, GateClassification.FAIL_CLOSED),
        EntitlementGate(
            "voice_minutes",
            FeatureKind.QUOTA,
            GateClassification.FAIL_OPEN,
            meter_code="voice_minutes",
        ),
    )


def test_snapshot_requires_every_gate_to_be_classified():
    snapshot = EntitlementSnapshot(
        id="esn_1",
        org_id="org_1",
        account_id="bac_1",
        version=1,
        issued_at=0.0,
        entitlements=(
            Entitlement("sso", FeatureKind.FLAG),
            Entitlement("unknown_cap", FeatureKind.FLAG),
        ),
        gates=(_gates()[0],),
    )
    diagnostics = BillingValidator().validate_snapshot(snapshot)
    assert diagnostics.has_errors
    assert any(d.code.value == "unclassified_gate" for d in diagnostics.errors)
    assert snapshot.unclassified_gates == ("unknown_cap",)


def test_live_paths_are_fail_open_and_expansion_paths_fail_closed():
    snapshot = EntitlementSnapshot(
        id="esn_1",
        org_id="org_1",
        account_id="bac_1",
        version=1,
        issued_at=0.0,
        entitlements=(Entitlement("voice_minutes", FeatureKind.QUOTA, allowance=100),),
        gates=_gates(),
    )
    assert snapshot.classification("voice_minutes") is GateClassification.FAIL_OPEN
    assert snapshot.classification("workspaces") is GateClassification.FAIL_CLOSED


def test_snapshot_staleness_is_explicit():
    snapshot = EntitlementSnapshot(
        id="esn_1",
        org_id="org_1",
        account_id="bac_1",
        version=1,
        issued_at=0.0,
        entitlements=(),
        gates=(),
        max_staleness_seconds=60.0,
    )
    assert not snapshot.is_stale_at(59.0)
    assert snapshot.is_stale_at(61.0)


def test_grant_composition_precedence_is_declared_not_incidental():
    resolver = EntitlementResolver(_gates())
    plan_grant = EntitlementGrant(
        id="ent_1",
        org_id="org_1",
        account_id="bac_1",
        entitlement=Entitlement("workspaces", FeatureKind.LIMIT, allowance=3),
        source=GrantSource.PLAN,
    )
    contract_grant = EntitlementGrant(
        id="ent_2",
        org_id="org_1",
        account_id="bac_1",
        entitlement=Entitlement("workspaces", FeatureKind.LIMIT, allowance=50),
        source=GrantSource.CONTRACT,
    )
    composed = resolver.compose((plan_grant, contract_grant))
    assert len(composed) == 1
    assert composed[0].allowance == 50
    assert composed[0].source is GrantSource.CONTRACT
    # order-independent
    assert resolver.compose((contract_grant, plan_grant)) == composed


def test_revoked_grants_are_excluded():
    resolver = EntitlementResolver(_gates())
    revoked = EntitlementGrant(
        id="ent_1",
        org_id="org_1",
        account_id="bac_1",
        entitlement=Entitlement("sso", FeatureKind.FLAG),
        source=GrantSource.PLAN,
        revoked_at=10.0,
    )
    assert resolver.compose((revoked,)) == ()


def test_entitlement_contraction_reduces_capability_without_destroying_data():
    resolver = EntitlementResolver(_gates())
    entitlements = (
        Entitlement("sso", FeatureKind.FLAG, enabled=True),
        Entitlement("workspaces", FeatureKind.LIMIT, allowance=10),
    )
    contracted = resolver.contract_for_state(entitlements, SubscriptionState.SUSPENDED)
    assert contracted[0].enabled is False
    assert contracted[1].allowance == 0
    # unchanged in a healthy state
    assert resolver.contract_for_state(entitlements, SubscriptionState.ACTIVE) == entitlements


def test_quota_verdict_informs_but_does_not_authorize():
    snapshot = EntitlementSnapshot(
        id="esn_1",
        org_id="org_1",
        account_id="bac_1",
        version=1,
        issued_at=0.0,
        entitlements=(Entitlement("voice_minutes", FeatureKind.QUOTA, allowance=100),),
        gates=_gates(),
    )
    service = QuotaService()
    ok = service.verdict(snapshot, "voice_minutes", consumed=50, at=1.0)
    assert ok.within_allowance and not ok.would_exceed and not service.should_warn(ok)
    warn = service.verdict(snapshot, "voice_minutes", consumed=85, at=1.0)
    assert service.should_warn(warn)  # warn before the wall
    over = service.verdict(snapshot, "voice_minutes", consumed=120, at=1.0)
    assert over.would_exceed and over.classification is GateClassification.FAIL_OPEN


# ── pricing validation ──────────────────────────────────────────────────────────


def _price(**kw) -> Price:
    base = dict(
        id="prc_1",
        price_book_id="pbk_1",
        code="pro_monthly",
        shape=PriceShape.FLAT_RECURRING,
        currency=INR,
        interval=BillingInterval.MONTHLY,
        amount=Money(99900, INR),
    )
    base.update(kw)
    return Price(**base)  # type: ignore[arg-type]


def test_price_shape_requirements_are_enforced():
    validator = BillingValidator()
    assert validator.validate_price(_price()).is_valid
    missing_amount = validator.validate_price(_price(amount=None))
    assert any(d.code.value == "invalid_price_shape" for d in missing_amount.errors)
    metered_without_meter = validator.validate_price(
        _price(shape=PriceShape.PER_UNIT, amount=None, unit_amount=Money(10, INR), meter_code="")
    )
    assert any(d.code.value == "invalid_price_shape" for d in metered_without_meter.errors)


def test_tiered_prices_must_be_contiguous_and_closed():
    validator = BillingValidator()
    good = _price(
        shape=PriceShape.TIERED_GRADUATED,
        amount=None,
        meter_code="tokens",
        interval=None,
        tiers=(PriceTier(1000, Money(2, INR)), PriceTier(None, Money(1, INR))),
    )
    assert validator.validate_price(good).is_valid
    non_contiguous = _price(
        shape=PriceShape.TIERED_GRADUATED,
        amount=None,
        meter_code="tokens",
        interval=None,
        tiers=(PriceTier(1000, Money(2, INR)), PriceTier(500, Money(1, INR))),
    )
    assert any(
        d.code.value == "non_contiguous_price_tiers"
        for d in validator.validate_price(non_contiguous).errors
    )
    open_ended_not_last = _price(
        shape=PriceShape.VOLUME,
        amount=None,
        meter_code="tokens",
        interval=None,
        tiers=(PriceTier(None, Money(1, INR)), PriceTier(100, Money(2, INR))),
    )
    assert any(
        d.code.value == "overlapping_price_tiers"
        for d in validator.validate_price(open_ended_not_last).errors
    )


def test_price_book_is_single_currency():
    book = PriceBook(
        id="pbk_1",
        org_id="",
        code="public",
        display_name="Public",
        currency=INR,
        prices=(_price(currency=USD),),
    )
    diagnostics = BillingValidator().validate_price_book(book)
    assert any(d.code.value == "currency_mismatch" for d in diagnostics.errors)


def test_price_resolution_is_deterministic_across_versions():
    v1 = _price(id="prc_1", version=1, effective=EffectiveRange(0.0, 100.0))
    v2 = _price(id="prc_2", version=2, effective=EffectiveRange(0.0))
    book = PriceBook(
        id="pbk_1", org_id="", code="public", display_name="Public", currency=INR, prices=(v1, v2)
    )
    assert book.price("pro_monthly", at=50.0).version == 2
    assert book.price("pro_monthly", at=150.0).version == 2
    assert book.price("missing") is None


# ── proration ───────────────────────────────────────────────────────────────────


def test_upgrade_is_immediate_and_charged_for_the_remainder():
    period = BillingPeriod(0.0, 1000.0)
    outcome = ProrationService().decide(period=period, at=250.0, is_expansion=True)
    assert outcome.behaviour is ProrationBehaviour.CHARGE_REMAINDER
    amount = ProrationService().amount(Money(1000, INR), outcome)
    assert amount == Money(750, INR)  # 75% of the period remains


def test_downgrade_defers_to_period_end_by_default():
    outcome = ProrationService().decide(
        period=BillingPeriod(0.0, 1000.0), at=250.0, is_expansion=False
    )
    assert outcome.behaviour is ProrationBehaviour.NONE
    assert ProrationService().amount(Money(1000, INR), outcome) == Money.zero(INR)


def test_immediate_downgrade_credits_and_never_silently_refunds():
    outcome = ProrationService().decide(
        period=BillingPeriod(0.0, 1000.0), at=250.0, is_expansion=False, immediate_downgrade=True
    )
    assert outcome.behaviour is ProrationBehaviour.CREDIT_REMAINDER
    assert ProrationService().amount(Money(1000, INR), outcome) == Money(-750, INR)


# ── ledger + credits: append-only, derived balances ─────────────────────────────


def test_ledger_balance_is_derived_never_stored():
    entries = (
        LedgerEntry(
            "led_1",
            "org_1",
            "bac_1",
            LedgerEntryType.CHARGE,
            LedgerDirection.DEBIT,
            Money(1000, INR),
            1.0,
        ),
        LedgerEntry(
            "led_2",
            "org_1",
            "bac_1",
            LedgerEntryType.PAYMENT,
            LedgerDirection.CREDIT,
            Money(400, INR),
            2.0,
        ),
    )
    balance = LedgerBalance.of("bac_1", INR, entries)
    assert balance.outstanding == Money(600, INR)
    assert balance.entry_count == 2


def test_ledger_rejects_mixed_currencies():
    entries = (
        LedgerEntry(
            "led_1",
            "org_1",
            "bac_1",
            LedgerEntryType.CHARGE,
            LedgerDirection.DEBIT,
            Money(1000, USD),
            1.0,
        ),
    )
    with pytest.raises(ValueError):
        LedgerBalance.of("bac_1", INR, entries)


def test_credit_balance_equals_sum_of_movements():
    movements = (
        CreditMovement("crm_1", "org_1", "bac_1", "crd_1", Money(5000, INR), 1.0),
        CreditMovement("crm_2", "org_1", "bac_1", "crd_1", Money(-1500, INR), 2.0),
    )
    assert CreditBalance.of("bac_1", INR, movements).available == Money(3500, INR)


def test_credit_grant_expiry_is_explicit():
    grant = CreditGrant(
        "crd_1", "org_1", "bac_1", CreditKind.PROMOTIONAL, Money(100, INR), 0.0, expires_at=10.0
    )
    assert grant.is_active_at(5.0) and not grant.is_active_at(15.0)


# ── invoicing: immutability, numbering, totals order ────────────────────────────


def _line(kind: ChargeLineKind, minor: int, code: str = "pro_monthly") -> ChargeLine:
    return ChargeLine(
        id=f"chl_{code}_{kind.value}_{minor}",
        org_id="org_1",
        account_id="bac_1",
        kind=kind,
        description=f"{kind.value} line",
        amount=Money(minor, INR),
        period=BillingPeriod(0.0, 100.0),
        price_code=code,
        price_version=1,
    )


def test_invoice_totals_apply_the_declared_order():
    lines = (
        _line(ChargeLineKind.RECURRING, 100000),
        _line(ChargeLineKind.USAGE, 25000),
        _line(ChargeLineKind.DISCOUNT, -20000),
        _line(ChargeLineKind.TAX, 18900),
    )
    totals = InvoiceTotalsService().totals(lines, currency=INR, credit_applied=Money(5000, INR))
    assert totals.subtotal == Money(125000, INR)
    assert totals.discount_total == Money(-20000, INR)
    assert totals.tax_total == Money(18900, INR)
    assert totals.total == Money(123900, INR)
    assert totals.amount_due == Money(118900, INR)


def test_invoice_totals_reject_a_foreign_currency_line():
    foreign = dataclasses.replace(_line(ChargeLineKind.USAGE, 100), amount=Money(100, USD))
    with pytest.raises(ValueError):
        InvoiceTotalsService().totals((foreign,), currency=INR)


def test_issued_invoice_is_immutable_and_cannot_be_reissued():
    factory, emitter = _factory()
    draft = factory.draft_invoice(
        IssueInvoiceRequest(
            org_id="org_1",
            account_id="bac_1",
            currency=INR,
            period=BillingPeriod(0.0, 100.0),
            lines=(_line(ChargeLineKind.RECURRING, 99900),),
        )
    ).invoice
    allocator = _Allocator()
    issued = factory.issue_invoice(draft, allocator)
    assert issued.state is InvoiceState.ISSUED
    assert issued.is_immutable
    assert issued.number is not None and issued.number.formatted == "VOXVOICE-000001"
    with pytest.raises(ImmutabilityViolation):
        factory.issue_invoice(issued, allocator)
    assert emitter.of_type(BillingEventType.INVOICE_ISSUED)


def test_invoice_numbering_is_gapless_and_per_entity():
    formatter = InvoiceNumberFormatter()
    allocator = _Allocator()
    numbers = [
        formatter.format(
            entity_code="voxvoice", jurisdiction="IN", sequence=allocator.allocate("voxvoice", "IN")
        ).sequence
        for _ in range(5)
    ]
    assert numbers == [1, 2, 3, 4, 5]  # gapless
    assert allocator.allocate("voxvoice", "EU") == 1  # independent per jurisdiction


def test_an_issued_invoice_must_carry_a_number():
    invoice = Invoice(
        id="inv_1",
        org_id="org_1",
        account_id="bac_1",
        currency=INR,
        period=BillingPeriod(0.0, 100.0),
        state=InvoiceState.ISSUED,
        issued_at=1.0,
    )
    diagnostics = BillingValidator().validate_invoice(invoice)
    assert any(d.code.value == "invalid_invoice_number" for d in diagnostics.errors)


def test_issued_states_are_all_immutable():
    assert InvoiceState.DRAFT not in IMMUTABLE_INVOICE_STATES
    assert InvoiceState.ISSUED in IMMUTABLE_INVOICE_STATES
    assert InvoiceState.PAID in IMMUTABLE_INVOICE_STATES


def test_invoice_carries_self_contained_evidence():
    factory, _ = _factory()
    invoice = factory.draft_invoice(
        IssueInvoiceRequest(
            org_id="org_1",
            account_id="bac_1",
            currency=INR,
            period=BillingPeriod(0.0, 100.0),
            lines=(_line(ChargeLineKind.RECURRING, 99900),),
            subscription_id="sub_1",
        )
    ).invoice
    assert any(e.startswith("price=pro_monthly@v1") for e in invoice.evidence)
    assert "subscription=sub_1" in invoice.evidence


# ── payments + refunds (domain only) ────────────────────────────────────────────


def test_payment_operation_identity_makes_retries_idempotent():
    payment = Payment(
        id="pay_1",
        org_id="org_1",
        account_id="bac_1",
        invoice_id="inv_1",
        amount=Money(99900, INR),
        attempt=2,
    )
    identity = payment.operation_identity()
    assert identity.key == "pay_1:2"
    assert payment.operation_identity() == identity  # stable


def test_offline_settlement_is_first_class():
    payment = Payment(
        id="pay_1",
        org_id="org_1",
        account_id="bac_1",
        invoice_id="inv_1",
        amount=Money(1, INR),
        method=PaymentMethodKind.OFFLINE_TRANSFER,
    )
    assert payment.is_offline
    assert payment.provider_code == ""  # no provider involved at all


def test_payment_stores_no_instrument_data():
    fields = {f.name for f in dataclasses.fields(billing.PaymentInstrument)}
    for banned in ("pan", "card_number", "cvv", "expiry", "iban", "account_number"):
        assert banned not in fields, f"PaymentInstrument must never carry {banned}"
    assert "token_ref" in fields


def test_refund_cannot_exceed_the_captured_amount():
    refund = Refund(
        id="rfd_1",
        org_id="org_1",
        account_id="bac_1",
        payment_id="pay_1",
        invoice_id="inv_1",
        amount=Money(60000, INR),
        reason=RefundReason.CUSTOMER_REQUEST,
        requester_id="usr_1",
        request_key="req_1",
    )
    diagnostics = BillingValidator().validate_refund(
        refund,
        captured=Money(99900, INR),
        already_refunded=Money(50000, INR),
        policy=RefundPolicy(),
    )
    assert any(d.code.value == "refund_exceeds_captured" for d in diagnostics.errors)


def test_refund_requires_an_idempotency_key_and_separation_of_duties():
    from billing.foundation.refunds import RefundApproval

    refund = Refund(
        id="rfd_1",
        org_id="org_1",
        account_id="bac_1",
        payment_id="pay_1",
        invoice_id="inv_1",
        amount=Money(100, INR),
        reason=RefundReason.BILLING_ERROR,
        requester_id="usr_1",
        request_key="",
        approval=RefundApproval("usr_1", 5.0),
    )
    diagnostics = BillingValidator().validate_refund(
        refund, captured=Money(1000, INR), already_refunded=Money.zero(INR), policy=RefundPolicy()
    )
    codes = {d.code.value for d in diagnostics.errors}
    assert "empty_identity" in codes  # missing request key
    assert "cross_org_reference" in codes  # self-approval rejected


# ── tax ─────────────────────────────────────────────────────────────────────────


def test_gst_profile_requires_a_place_of_supply():
    profile = TaxProfile(
        id="txp_1", org_id="org_1", account_id="bac_1", regime=TaxRegime.INDIA_GST, country="IN"
    )
    diagnostics = BillingValidator().validate_tax_profile(profile)
    assert any(d.code.value == "missing_tax_profile" for d in diagnostics.errors)


def test_malformed_registrations_are_rejected_and_unvalidated_ones_warned():
    profile = TaxProfile(
        id="txp_1",
        org_id="org_1",
        account_id="bac_1",
        regime=TaxRegime.INDIA_GST,
        taxpayer_kind=TaxpayerKind.BUSINESS,
        country="IN",
        place_of_supply="HR",
        registrations=(TaxRegistration(TaxRegime.INDIA_GST, "NOTAGSTIN"),),
    )
    diagnostics = BillingValidator().validate_tax_profile(profile)
    assert any(d.code.value == "invalid_tax_registration" for d in diagnostics.errors)
    assert diagnostics.warnings  # unvalidated registration is surfaced, never assumed valid


def test_no_tax_rate_is_encoded_in_the_foundation():
    """ADR-0023 TX1: determination is a rented edge. The Foundation records evidence and
    must contain no rate table."""
    source = pathlib.Path("billing/foundation/tax.py").read_text()
    for token in ("0.18", "18%", "RATE_TABLE", "GST_RATES", "VAT_RATES"):
        assert token not in source, f"tax.py encodes a rate ({token}) — determination is rented"


# ── contracts + licensing ───────────────────────────────────────────────────────


def test_commitment_true_up_is_a_ledger_fact_not_a_spreadsheet():
    contract = Contract(
        id="ctr_1",
        org_id="org_1",
        account_id="bac_1",
        code="acme-2026",
        term=ContractTerm(0.0, 1000.0),
        commitment=Commitment(
            CommitmentKind.MINIMUM_SPEND, Money(1000000, INR), Money(400000, INR)
        ),
    )
    assert ContractService().true_up(contract) == Money(600000, INR)
    exhausted = dataclasses.replace(
        contract,
        commitment=Commitment(
            CommitmentKind.MINIMUM_SPEND, Money(1000000, INR), Money(1200000, INR)
        ),
    )
    assert ContractService().true_up(exhausted) == Money.zero(INR)  # never negative


def test_renewal_uplift_uses_exact_basis_points():
    contract = Contract(
        id="ctr_1",
        org_id="org_1",
        account_id="bac_1",
        code="acme",
        term=ContractTerm(0.0, 1000.0, uplift_basis_points=500),
    )
    assert ContractService().renewal_amount(Money(100000, INR), contract) == Money(105000, INR)


def test_a_license_is_an_entitlement_grant_with_no_payment_edge():
    license_ = License(
        id="lic_1",
        org_id="org_1",
        account_id="bac_1",
        kind=LicenseKind.AIR_GAPPED,
        valid_from=0.0,
        valid_until=100.0,
        entitlement_codes=("sso", "white_label"),
    )
    assert license_.is_valid_at(50.0) and not license_.is_valid_at(150.0)
    fields = {f.name for f in dataclasses.fields(License)}
    for banned in ("payment_id", "instrument_id", "provider_code", "amount"):
        assert banned not in fields


def test_contract_term_must_be_coherent():
    with pytest.raises(ValueError):
        ContractTerm(1000.0, 500.0)


# ── accounts + hierarchy ────────────────────────────────────────────────────────


def test_account_hierarchy_is_separate_from_org_tenancy():
    factory, _ = _factory()
    parent = factory.open_account(
        OpenAccountRequest(
            org_id="org_partner", display_name="Partner", currency=INR, kind=AccountKind.PARTNER
        )
    ).account
    child = factory.open_account(
        OpenAccountRequest(
            org_id="org_customer",
            display_name="Customer",
            currency=INR,
            kind=AccountKind.CHILD,
            parent_account_id=parent.id,
        )
    ).account
    hierarchy = billing.AccountHierarchy(parent, (child,))
    assert child.org_id != parent.org_id  # different tenants, one billing relationship
    assert hierarchy.invoiced_account is parent  # invoicing rolls up
    assert child.is_child and not child.is_billable  # usage attributes down


def test_closed_accounts_are_retained_not_deleted():
    account = billing.BillingAccount(
        id="bac_1",
        org_id="org_1",
        display_name="Gone",
        currency=INR,
        status=billing.AccountStatus.CLOSED,
        closed_at=10.0,
    )
    assert account.is_retained_only and not account.is_billable


# ── repository boundaries ───────────────────────────────────────────────────────


_APPEND_ONLY_REPOS = (
    "LedgerRepository",
    "UsageRecordRepository",
    "CreditNoteRepository",
    "TaxDeterminationRepository",
    "BillingEventRepository",
    "ReconciliationRepository",
    "UsageQuarantineRepository",
)


def test_every_repository_is_an_interface_with_no_implementation():
    from abc import ABC

    for name in billing.__all__:
        if not name.endswith(("Repository", "Allocator")):
            continue
        cls = getattr(billing, name)
        assert issubclass(cls, ABC), f"{name} must be an interface"
        assert getattr(cls, "__abstractmethods__", None), f"{name} has no abstract methods"
        with pytest.raises(TypeError):
            cls()  # cannot be instantiated: contracts only, no persistence at M9.2


def test_append_only_repositories_declare_no_update_or_delete():
    for name in _APPEND_ONLY_REPOS:
        cls = getattr(billing, name)
        methods = {
            n for n, _ in inspect.getmembers(cls, inspect.isfunction) if not n.startswith("_")
        }
        for banned in ("update", "delete", "remove", "replace", "set"):
            assert banned not in methods, f"{name}.{banned} breaks the append-only guarantee"


def test_org_scoped_repositories_take_an_org_id():
    """§A11.2: isolation is enforced by the query shape, never by convention."""
    for name in (
        "SubscriptionRepository",
        "InvoiceRepository",
        "LedgerRepository",
        "PaymentRepository",
        "RefundRepository",
        "ContractRepository",
    ):
        cls = getattr(billing, name)
        readers = [
            m
            for n, m in inspect.getmembers(cls, inspect.isfunction)
            if n in ("get", "for_account", "for_invoice", "for_payment")
        ]
        assert readers
        for method in readers:
            assert "org_id" in inspect.signature(method).parameters, f"{name}.{method.__name__}"


# ── dependency direction + ADR-0031 import rules (IR1–IR9) ─────────────────────


_ALLOWED_TOP_LEVEL = {"core", "billing", "__future__"}
_FORBIDDEN_PLANES = {
    "api",
    "actuator",
    "runtime",
    "providers",
    "provider",
    "agent",
    "tool",
    "skill",
    "workflow",
    "mcp",
    "loop",
    "hooks",
    "integration",
    "bridge",
    "memory",
    "knowledge",
    "context",
    "prompt",
    "reasoning",
    "pipeline",
    "events",
    "gateway",
    "replay",
    "observability",
    "telemetry",
    "tenant",
    "studio",
    "voicebot",
}


def _imported_tops(pkg_path: str) -> set[str]:
    tops: set[str] = set()
    for path in pathlib.Path(pkg_path).rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                tops.add(node.module.split(".")[0])
            elif isinstance(node, ast.Import):
                tops.update(a.name.split(".")[0] for a in node.names)
    return tops


def test_ir1_billing_imports_core_and_stdlib_only():
    """IR1 for the domain layers. The Foundation and the Application import `core`, `billing`,
    and the standard library — nothing else, ever.

    IR1a (M9.4): `billing/persistence/**` additionally imports `sqlmodel`/`sqlalchemy`. That
    is the single, named exception, confined to the layer whose whole purpose is storage, and
    checked by its own suite. It is scoped rather than global precisely so this assertion
    keeps its teeth for the layers that must stay pure."""
    # `hashlib` joins the set at M9.11 and only for the Foundation: derived usage ids and seal
    # digests are the mechanism that makes deterministic replay *verifiable*, and both must be
    # computed in the domain layer. It is stdlib, pure, and has no I/O — the property this
    # assertion actually protects.
    stdlib = {
        "abc",
        "dataclasses",
        "enum",
        "typing",
        "re",
        "time",
        "collections",
        "hashlib",
        "__future__",
    }
    for layer in ("billing/foundation", "billing/application"):
        used = _imported_tops(layer)
        unexpected = used - _ALLOWED_TOP_LEVEL - stdlib
        assert not unexpected, f"{layer} imports unexpected top-level modules: {sorted(unexpected)}"

    persistence_allowed = _ALLOWED_TOP_LEVEL | stdlib | {"sqlmodel", "sqlalchemy", "types", "json"}
    unexpected = _imported_tops("billing/persistence") - persistence_allowed
    assert not unexpected, f"billing/persistence imports unexpected modules: {sorted(unexpected)}"


def test_ir2_only_refs_may_import_product():
    offenders = []
    for path in pathlib.Path("billing").rglob("*.py"):
        if "product" in _imported_tops(str(path.parent)) and path.name != "refs.py":
            pass  # per-file check below is the precise one
        tops: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                tops.add(node.module.split(".")[0])
            elif isinstance(node, ast.Import):
                tops.update(a.name.split(".")[0] for a in node.names)
        if "product" in tops and path.name != "refs.py":
            offenders.append(str(path))
    assert not offenders, f"only billing/refs.py may import product; offenders: {offenders}"


def test_ir3_billing_imports_no_other_plane():
    used = _imported_tops("billing")
    leaked = used & _FORBIDDEN_PLANES
    assert not leaked, f"billing imports forbidden plane(s): {sorted(leaked)}"


def test_ir4_no_frozen_epic_imports_billing():
    for plane in (
        "core",
        "runtime",
        "product",
        "actuator",
        "api",
        "events",
        "gateway",
        "replay",
        "observability",
        "agent",
        "tool",
        "workflow",
        "knowledge",
    ):
        if not pathlib.Path(plane).is_dir():
            continue
        assert "billing" not in _imported_tops(plane), (
            f"{plane} imports billing — freeze is one-way"
        )


def test_ir5_no_vendor_sdk_anywhere_in_the_foundation():
    vendors = {
        "stripe",
        "razorpay",
        "paddle",
        "chargebee",
        "recurly",
        "avalara",
        "braintree",
        "adyen",
        "lemonsqueezy",
        "boto3",
        "google",
        "twilio",
    }
    used = _imported_tops("billing")
    assert not (used & vendors), f"vendor SDK in the billing core: {sorted(used & vendors)}"


def test_ir7_adapters_and_domain_layering_holds():
    """No adapter package exists at M9.2 (adapters land at M9.14); the rule is asserted so
    it cannot be violated silently when they arrive."""
    adapters = pathlib.Path("billing/foundation/adapters")
    assert not adapters.exists(), "adapters are out of scope at M9.2 (Foundation only)"


def test_ir8_public_surface_is_the_package_init():
    assert len(billing.__all__) > 150
    for name in billing.__all__:
        assert hasattr(billing, name), f"{name} is exported but missing"


def test_ir9_billing_declares_no_metric_events():
    """Billing consumes `metric.*`; it never produces one (ADR-0023 M8).

    Scoped to the layers that can emit. The transport layer holds `metric.*` strings only as
    OpenAPI examples of an event a caller *supplies* — documenting consumption, which is the
    opposite of the violation. `test_transport_emits_no_events` proves it cannot emit."""
    for layer in ("billing/foundation", "billing/application", "billing/persistence"):
        for path in pathlib.Path(layer).rglob("*.py"):
            text = path.read_text()
            assert '"metric.' not in text, (
                f"{path} declares a metric.* event — Billing consumes, never emits"
            )


def test_transport_emits_no_events():
    """The API has no emitter and stages nothing: every billing fact and every application
    event originates below it, inside a transaction."""
    for path in pathlib.Path("billing/customer").rglob("*.py"):
        text = path.read_text()
        for forbidden in ("EventEmitter", ".emit(", "uow.stage", "BillingEventType("):
            assert forbidden not in text, (path.name, forbidden)


def test_no_persistence_http_or_ui_landed_in_the_foundation():
    """M9.2 scope: Foundation only. No application layer, no persistence implementation,
    no API, no Studio, no adapters, no providers."""
    banned_names = {
        "router",
        "routers",
        "app",
        "main",
        "migrations",
        "persistence",
        "studio",
        "schemas",
        "endpoints",
        "sql",
        "orm",
    }
    for path in pathlib.Path("billing/foundation").rglob("*.py"):
        assert path.stem not in banned_names, f"{path} is out of M9.2 scope"


def test_billing_package_layout_is_the_declared_one():
    modules = {m.name for m in pkgutil.iter_modules(["billing/foundation"])}
    expected = {
        "accounts",
        "catalog",
        # M9.6 — the Commercial Catalog arrived as its own modules rather than by reshaping
        # any of the ones around them (§A12: extension is addition).
        "catalog_factories",
        "catalog_rules",
        "products",
        # M9.7 — the Pricing Engine, likewise: new modules beside `pricing`, never a reshape
        # of it. `pricing.py` (M9.2) still declares what a price *is*; these add books,
        # versions, discounts, and resolution around it.
        "price_books",
        "pricing_factories",
        "pricing_rules",
        # M9.8 — Billing Accounts, likewise: new modules beside `accounts`, never a reshape of
        # it. `accounts.py` (M9.2) still declares the minimal account the revenue spine
        # attributes to; these add the full commercial record around it.
        "account_factories",
        "account_rules",
        "commercial_accounts",
        # M9.9 — the Subscription Engine, likewise: new modules beside `subscription`, never a
        # reshape of it. `subscription.py` (M9.2) still declares the aggregate the revenue spine
        # rates against; these add the full commercial agreement around it.
        "commercial_subscriptions",
        "subscription_factories",
        "subscription_rules",
        # M9.10 — the Entitlement Engine, likewise: new modules beside `entitlement`, never a
        # reshape of it. `entitlement.py` (M9.2) still declares the enforcement contract the
        # owning platform reads; these add the governed gate registry, grants, precedence
        # resolution, and versioned publishing around it.
        "commercial_entitlements",
        "entitlement_rules",
        "entitlement_factories",
        # M9.11 — the Metering Engine, likewise: new modules beside `metering`, never a reshape
        # of it. `metering.py` (M9.2) still declares the usage record the revenue spine reads;
        # these add the governed meter registry, exactly-once ingestion, windowed aggregation,
        # sealing, replay, and reconciliation around it.
        "commercial_metering",
        "metering_rules",
        "metering_factories",
        # M9.12 — the Rating Engine, likewise: new modules beside `pricing` and `invoicing`,
        # never a reshape of either. `pricing.py` (M9.2) still declares what a price *is* and
        # `invoicing.py` still owns the `ChargeLine` an invoice carries; these add charge
        # calculation, breakdown, provenance, rating runs, rerating, and comparison around them.
        "commercial_rating",
        "rating_rules",
        "rating_factories",
        # M9.13 — the Credit & Revenue Ledger, likewise: new modules beside `ledger.py`, never a
        # reshape of it. `ledger.py` (M9.2) still declares `LedgerEntry`, `LedgerBalance`,
        # `CreditGrant` and the `LedgerEntryType`/`LedgerDirection`/`CreditKind` vocabulary these
        # reuse; these add journal entries, accounting periods, derived balances with
        # checkpoints, credits with derived positions, replay, and reconciliation around them.
        "commercial_ledger",
        "ledger_rules",
        "ledger_factories",
        # M9.14 — the Invoice Engine, likewise: new modules beside `invoicing.py`, never a
        # reshape of it. `invoicing.py` (M9.2) still declares `Invoice`, `CreditNote`,
        # `ChargeLine`, `InvoiceNumber`, `InvoiceTotals` and the `InvoiceState`/`InvoiceTrigger`
        # vocabulary these reuse; these add the document snapshot, gapless numbering, corrections
        # by credit and debit note, rendering models, and document relationships around them.
        "commercial_invoicing",
        "invoice_rules",
        "invoice_factories",
        # M9.15 — the Tax Determination Engine, likewise: new modules beside `tax.py`, never a
        # reshape of it. `tax.py` (M9.2) still declares `TaxRegime`, `TaxpayerKind`,
        # `TaxTreatment`, `GstSplit`, `TaxRegistration`, `TaxProfile`, `TaxComponent` and the
        # `TaxDetermination` this milestone *projects onto*; these add jurisdictions, nexus,
        # classifications, exemptions, effective-dated rate evidence behind an adapter port, and
        # the self-explaining assessment around them.
        "commercial_tax",
        "tax_rules",
        "tax_factories",
        # M9.16 — the Payment Engine, likewise: new modules beside `payments` and `refunds`,
        # never a reshape of either. `payments.py` (M9.2) still declares the lifecycle, the
        # instrument token reference, the operation identity and the reconciliation verdict;
        # these add *execution* around them — the intent/attempt split M9.2's single mutable
        # row could not express, the provider port, and the durable operation claim.
        "commercial_payments",
        "payment_rules",
        "payment_factories",
        # M9.17 — Dunning & Collections, likewise: new modules beside `payments`, never a
        # reshape of it. `payments.py` (M9.2) still declares `DunningProcess`, its six-state
        # ladder and its policy *value*; these add the case/action/attempt split that one
        # mutable row could not express, the **versioned** strategy a historical decision can
        # be replayed against, and the payment-signal reading that keeps an ambiguous outcome
        # from ever becoming a retry.
        "commercial_collections",
        "collection_rules",
        "collection_factories",
        # M9.18 — Revenue Recognition, likewise: new modules beside `ledger`, never a reshape of
        # it. `ledger.py` (M9.2) still declares `RecognitionState` — the *posture tag* on an entry
        # that ADR-0023 RS6 ratified — and this milestone **reuses it unchanged**; these add the
        # thing it could not express: an immutable basis, a dated schedule, and a decision that
        # replays after the policy is rewritten.
        "commercial_revenue",
        "revenue_rules",
        "revenue_factories",
        # M9.19 — Reconciliation & Financial Integrity. Added the way every module before it was:
        # by extension, never by renaming or removing one.
        "commercial_reconciliation",
        "reconciliation_rules",
        "reconciliation_factories",
        # M9.20 — Financial Integrity Correlation. Added the way every module before it was: by
        # extension, never by renaming or removing one.
        "commercial_incidents",
        "incident_rules",
        # M9.21 — the Financial Investigation & Audit Evidence Engine, likewise: new modules
        # beside `commercial_incidents`, never a reshape of it. M9.20 still owns whether an
        # incident exists and what caused it; these add the investigation *of* one — evidence
        # capture, chain of custody, conclusions and the immutable audit package.
        "commercial_investigations",
        "investigation_factories",
        "investigation_rules",
        # M9.22 — the Compliance & Regulatory Reporting Engine, likewise: new modules beside the
        # nine domains it reads, never a reshape of any of them. Every financial fact stays owned
        # where it was decided; these add the *derived* regulatory artifact — versioned
        # definitions, pinned input manifests, immutable filings and submission packages.
        "commercial_reporting",
        "reporting_factories",
        "reporting_rules",
        # M9.23 — the Period Scope & Consolidation Engine, the same way: new modules beside the
        # domains it reads. M9.8 keeps the account hierarchy and every financial domain keeps its
        # facts; these add only *scope* — which accounts a consolidated view covers, resolved once
        # into an immutable membership, and what could not be included and why.
        "commercial_consolidation",
        "consolidation_factories",
        "consolidation_rules",
        "incident_factories",
        "contracts",
        "diagnostics",
        "domain",
        "dto",
        "entitlement",
        "errors",
        "events",
        "factories",
        "invoicing",
        "ledger",
        "lifecycle",
        "metering",
        "money",
        "payments",
        "pricing",
        "refunds",
        "repositories",
        "seams",
        "services",
        "subscription",
        "tax",
        "validation",
    }
    assert modules == expected, f"unexpected layout: {modules ^ expected}"


# ── end-to-end: the Foundation's Definition of Done ────────────────────────────


def test_definition_of_done_end_to_end():
    """A commercial lifecycle expressed entirely in pure domain code: open an account,
    define a meter and a plan, start a subscription, publish an entitlement snapshot,
    record usage, price it, and issue an immutable invoice — with no I/O anywhere."""
    factory, emitter = _factory()

    account = factory.open_account(
        OpenAccountRequest(org_id="org_1", display_name="Acme", currency=INR)
    ).account

    meter = factory.define_meter(
        DefineMeterRequest(
            code="voice_minutes",
            display_name="Voice minutes",
            unit="minute",
            aggregation=MeterAggregation.SUM,
            source=MeterSource.RUNTIME_SESSION,
        )
    )
    plan = factory.publish_plan(
        factory.define_plan(
            DefinePlanRequest(
                code="pro",
                display_name="Pro",
                tier=Tier.PRO,
                features=(PlanFeature("voice_minutes", allowance=1000, allows_overage=True),),
                metered_codes=("voice_minutes",),
            )
        )
    )
    catalog = CommercialCatalog(
        plans=(plan,),
        features=(
            Feature(
                "ftr_1",
                "voice_minutes",
                "Voice minutes",
                FeatureKind.QUOTA,
                meter_code="voice_minutes",
            ),
        ),
        meters=(meter,),
    )

    subscription = factory.start_subscription(
        StartSubscriptionRequest(
            org_id="org_1",
            account_id=account.id,
            plan_code=plan.code,
            plan_version=plan.version,
            price_book_id="pbk_1",
            price_book_version=1,
            currency=INR,
            interval=BillingInterval.MONTHLY,
            items=(
                SubscriptionItem("pro_monthly", 1),
                SubscriptionItem("voice_overage", 1, meter_code="voice_minutes"),
            ),
        )
    ).subscription
    assert subscription.metered_codes() == ("voice_minutes",)

    resolver = EntitlementResolver(_gates())
    entitlements = resolver.from_plan(plan.features, catalog)
    snapshot = factory.publish_snapshot(
        org_id="org_1",
        account_id=account.id,
        version=1,
        entitlements=entitlements,
        gates=_gates(),
        subscription_id=subscription.id,
    ).snapshot
    assert snapshot.allowance("voice_minutes") == 1000

    period = subscription.current_period
    assert period is not None
    usage = tuple(
        factory.record_usage(
            RecordUsageRequest(
                org_id="org_1",
                account_id=account.id,
                meter_code="voice_minutes",
                units=600,
                unit="minute",
                scope=Scope("org_1"),
                period=period,
                event_id=f"evt_{i}",
                event_type="metric.session.completed",
                observed_at=float(i),
                subscription_id=subscription.id,
            )
        )
        for i in range(2)
    )
    total = MeteringService().total(usage, MeterAggregation.SUM, unit="minute")
    assert total == Quantity(1200, "minute")

    verdict = QuotaService().verdict(snapshot, "voice_minutes", consumed=total.units, at=10.0)
    assert verdict.would_exceed is False  # the plan permits overage — billed, not blocked
    assert verdict.within_allowance is False

    overage_units = total.units - (snapshot.allowance("voice_minutes") or 0)
    lines = (
        _line(ChargeLineKind.RECURRING, 99900),
        dataclasses.replace(
            _line(ChargeLineKind.OVERAGE, 200 * overage_units, "voice_overage"),
            meter_code="voice_minutes",
            quantity=Quantity(overage_units, "minute"),
            unit_amount=Money(200, INR),
            usage_record_ids=tuple(u.id for u in usage),
        ),
    )
    result = factory.draft_invoice(
        IssueInvoiceRequest(
            org_id="org_1",
            account_id=account.id,
            currency=INR,
            period=period,
            lines=lines,
            subscription_id=subscription.id,
        )
    )
    assert result.totals.total == Money(99900 + 200 * 200, INR)

    issued = factory.issue_invoice(result.invoice, _Allocator(), jurisdiction="IN")
    assert issued.is_immutable
    assert issued.number is not None and issued.number.formatted == "VOXVOICE-IN-000001"

    emitted = {e.type for e in emitter.events}
    assert BillingEventType.ACCOUNT_CREATED in emitted
    assert BillingEventType.SUBSCRIPTION_CREATED in emitted
    assert BillingEventType.ENTITLEMENT_SNAPSHOT_PUBLISHED in emitted
    assert BillingEventType.USAGE_RECORDED in emitted
    assert BillingEventType.INVOICE_ISSUED in emitted
    # Every *customer* fact is org-scoped (§A11.2); platform-wide catalog facts (a public
    # meter/plan) carry no org by design — they belong to no tenant.
    customer_facts = {
        BillingEventType.ACCOUNT_CREATED,
        BillingEventType.SUBSCRIPTION_CREATED,
        BillingEventType.ENTITLEMENT_SNAPSHOT_PUBLISHED,
        BillingEventType.USAGE_RECORDED,
        BillingEventType.INVOICE_DRAFTED,
        BillingEventType.INVOICE_ISSUED,
    }
    assert all(e.org_id == "org_1" for e in emitter.events if e.type in customer_facts)
    assert all(e.org_id == "" for e in emitter.events if e.type not in customer_facts)
