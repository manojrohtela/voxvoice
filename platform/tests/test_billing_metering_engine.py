"""M9.11 — Metering Engine: tests.

Four layers, one domain: pure Foundation rules, then the Application over a real database, then
the HTTP surface, then architecture compliance.

Metering is the highest-risk correctness milestone of Epic 9 because its errors are **silent**: a
lost event under-bills and nobody complains, a duplicated event over-bills and the customer
leaves. Section 13 therefore demonstrates each load-bearing guarantee explicitly rather than
implying it — deterministic replay, duplicate suppression, late-event determinism, sealing,
restart, multi-instance correctness, concurrent idempotency, append-only history, one record per
source event, and independence from Runtime availability.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.customer.jwt import encode as epic8_encode
from billing.application import (
    ActivateBillingAccount,
    ActivateCommercialMeter,
    AllowAllAuthorizer,
    BeginUsagePeriodClose,
    BillingAuthorizationError,
    CreateBillingAccount,
    DefineCommercialMeter,
    DenyAllAuthorizer,
    DeprecateCommercialMeter,
    DiagnoseUsagePeriod,
    DiscardQuarantinedEvent,
    DrainMetricStream,
    ExportCommercialUsage,
    GetCommercialUsageSnapshot,
    GetUsagePeriodState,
    GetUsageReconciliationReport,
    InspectCommercialUsage,
    ListCommercialMeters,
    ListQuarantinedEvents,
    ListUsagePeriods,
    MeteringTimeline,
    OpenUsagePeriod,
    RebuildUsageAggregates,
    ReconcileUsagePeriod,
    RecordUsageCorrection,
    ReinstateCommercialMeter,
    ReplayUsagePeriod,
    ResolveQuarantinedEvent,
    RetireCommercialMeter,
    SealUsagePeriod,
    SearchCommercialUsage,
    VersionCommercialMeter,
)
from billing.customer import BillingApiSettings, BillingContainer, create_billing_app
from billing.foundation import (
    INGESTIBLE_METER_STATES,
    MAX_CLOCK_SKEW_SECONDS,
    METER_LIFECYCLE,
    QUARANTINE_BACKLOG_THRESHOLD,
    QUARANTINE_LIFECYCLE,
    USAGE_PERIOD_LIFECYCLE,
    AttributionService,
    BillingConflict,
    BillingPeriod,
    BillingValidationError,
    CommercialMeter,
    CommercialUsage,
    Currency,
    DefaultIdGenerator,
    FixedClock,
    IngestionOutcome,
    InvalidBillingTransition,
    LateArrivalDisposition,
    LateArrivalPolicy,
    MeterAggregation,
    MeteringFactory,
    MeteringValidator,
    MeterLifecycleService,
    MeterSource,
    MeterState,
    MeterTrigger,
    MetricSignal,
    Normalization,
    NullMetricStream,
    PeriodState,
    PeriodTrigger,
    Quantity,
    QuarantineLifecycleService,
    QuarantineReason,
    QuarantineState,
    QuarantineTrigger,
    RecordingEventEmitter,
    RecordingMetricStream,
    Rounding,
    Scope,
    SequentialIdGenerator,
    SourceEvent,
    UsageAggregator,
    UsageCorrectionService,
    UsageExportService,
    UsageIngestionService,
    UsageInspector,
    UsageNormalizer,
    UsagePeriod,
    UsagePeriodLifecycleService,
    UsageReconciliationService,
    UsageReplayService,
    UsageSealingService,
    UsageSearchQuery,
    UsageSearchService,
    UsageStatus,
    WindowGranularity,
    derive_usage_id,
)
from billing.persistence import BillingDatabase, build_persistent_billing_application

PLATFORM = Path(__file__).resolve().parents[1]
BILLING = PLATFORM / "billing"
PREFIX = "/v1/billing/usage"
SECRET = "test-billing-secret"
ORG = dict(actor_id="usr_1", org_id="org_1")
PS, PE = 1_000.0, 100_000.0
PERIOD = BillingPeriod(PS, PE)
SCOPE = Scope(org_id="org_1")


# ── fixtures ────────────────────────────────────────────────────────────────────


def _meter(code="voice.minutes", **kwargs) -> CommercialMeter:
    return CommercialMeter(
        id="cmt_1",
        org_id="org_1",
        code=code,
        unit=kwargs.pop("unit", "minute"),
        source=kwargs.pop("source", MeterSource.RUNTIME_SESSION),
        state=kwargs.pop("state", MeterState.ACTIVE),
        accepted_event_types=kwargs.pop("accepted_event_types", ("metric.session.duration_ms",)),
        normalization=kwargs.pop("normalization", Normalization(1, 60_000)),
        **kwargs,
    )


def _event(event_id="evt_1", *, observed_at=2_000.0, received_at=None, **kwargs) -> SourceEvent:
    return SourceEvent(
        event_id=event_id,
        event_type=kwargs.pop("event_type", "metric.session.duration_ms"),
        source=kwargs.pop("source", MeterSource.RUNTIME_SESSION),
        observed_at=observed_at,
        received_at=received_at if received_at is not None else observed_at + 10.0,
        **kwargs,
    )


def _signal(i, ms=60_000, at=2_000.0, **kwargs) -> MetricSignal:
    return MetricSignal(
        event_id=f"evt_{i}",
        event_type=kwargs.pop("event_type", "metric.session.duration_ms"),
        source=kwargs.pop("source", MeterSource.RUNTIME_SESSION),
        observed_at=at,
        raw_units=ms,
        raw_unit=kwargs.pop("raw_unit", "ms"),
        scope=kwargs.pop("scope", SCOPE),
        received_at=kwargs.pop("received_at", at + 10.0),
        **kwargs,
    )


def _factory() -> tuple[MeteringFactory, RecordingEventEmitter]:
    emitter = RecordingEventEmitter()
    return (
        MeteringFactory(ids=SequentialIdGenerator(), clock=FixedClock(50_000.0), emitter=emitter),
        emitter,
    )


@pytest.fixture()
def stream():
    return RecordingMetricStream()


@pytest.fixture()
def app(stream):
    wired = build_persistent_billing_application(
        ids=SequentialIdGenerator(),
        clock=FixedClock(50_000.0),
        authorizer=AllowAllAuthorizer(),
        metric_stream=stream,
    )
    yield wired.application
    wired.database.close()


def _client(authorizer=None, metric_stream=None, **overrides) -> TestClient:
    settings = BillingApiSettings(environment="testing", jwt_secret=SECRET, **overrides)
    container = BillingContainer.build(
        settings,
        authorizer=authorizer if authorizer is not None else AllowAllAuthorizer(),
        ids=SequentialIdGenerator(),
        clock=FixedClock(50_000.0),
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


# ── 1. Foundation: normalization is exact ───────────────────────────────────────


def test_identity_normalization_changes_nothing():
    assert UsageNormalizer().normalize(1_234, Normalization()) == 1_234


def test_normalization_is_exact_integer_arithmetic():
    """A rounding difference in the eighth decimal place is a revenue incident nobody can
    reproduce. There is no float in this path at all."""
    n = UsageNormalizer()
    ms_to_min = Normalization(1, 60_000)
    assert n.normalize(60_000, ms_to_min) == 1
    assert n.normalize(120_000, ms_to_min) == 2
    assert n.normalize(0, ms_to_min) == 0


def test_half_up_rounds_a_half_upward():
    assert UsageNormalizer().normalize(90_000, Normalization(1, 60_000, Rounding.HALF_UP)) == 2


def test_half_even_does_not_bias_every_meter_in_our_favour():
    """Rounding halves consistently upward biases every meter toward VoxVoice — a trust problem
    and, in some jurisdictions, a compliance one."""
    n = UsageNormalizer()
    half_even = Normalization(1, 60_000, Rounding.HALF_EVEN)
    assert n.normalize(90_000, half_even) == 2  # 1.5 → 2 (even)
    assert n.normalize(150_000, half_even) == 2  # 2.5 → 2 (even)


def test_floor_and_ceiling_round_as_declared():
    n = UsageNormalizer()
    assert n.normalize(119_999, Normalization(1, 60_000, Rounding.FLOOR)) == 1
    assert n.normalize(60_001, Normalization(1, 60_000, Rounding.CEILING)) == 2


def test_normalization_is_identical_across_repeated_calls():
    n = UsageNormalizer()
    norm = Normalization(7, 3, Rounding.HALF_EVEN)
    assert len({n.normalize(1_000_003, norm) for _ in range(50)}) == 1


def test_a_negative_raw_signal_is_refused():
    with pytest.raises(BillingConflict):
        UsageNormalizer().normalize(-1, Normalization())


def test_an_invalid_ratio_is_refused():
    with pytest.raises(BillingConflict):
        UsageNormalizer().normalize(10, Normalization(1, 0))


def test_no_float_appears_in_a_quantity():
    q = UsageNormalizer().quantity(185_000, _meter(normalization=Normalization(1, 60_000)))
    assert isinstance(q.units, int) and not isinstance(q.units, float)
    assert q == Quantity(3, "minute")


# ── 2. Foundation: source-event identity ────────────────────────────────────────


def test_a_usage_id_is_derived_from_its_source_event():
    """Derived, not minted, so replay produces byte-identical usage — ids included."""
    first = derive_usage_id("org_1", "evt_1", "voice.minutes")
    second = derive_usage_id("org_1", "evt_1", "voice.minutes")
    assert first == second
    assert first.startswith("usg_")


def test_the_derived_id_separates_organizations_meters_and_events():
    base = derive_usage_id("org_1", "evt_1", "voice.minutes")
    assert derive_usage_id("org_2", "evt_1", "voice.minutes") != base
    assert derive_usage_id("org_1", "evt_2", "voice.minutes") != base
    assert derive_usage_id("org_1", "evt_1", "tokens") != base


def test_the_idempotency_key_is_the_event_and_the_meter():
    assert _event("evt_9").identity("m") == ("evt_9", "m")


def test_lateness_is_never_negative():
    """A negative value would mean the stream delivered before it observed."""
    assert _event(observed_at=100.0, received_at=50.0).lateness == 0.0
    assert _event(observed_at=100.0, received_at=160.0).lateness == 60.0


# ── 3. Foundation: the ingestion decision ───────────────────────────────────────


def _decide(**kwargs):
    defaults = dict(
        org_id="org_1",
        meter=_meter(),
        raw_units=60_000,
        raw_unit="ms",
        scope=SCOPE,
        period=PERIOD,
        accounts_by_scope={"org_1": "cma_1"},
        ingested_at=50_000.0,
    )
    event = kwargs.pop("event", _event())
    defaults.update(kwargs)
    return UsageIngestionService().decide(event, **defaults)


def test_a_well_formed_event_becomes_commercial_usage():
    decision = _decide(meter=_meter(normalization=Normalization(1, 60_000)))
    assert decision.outcome is IngestionOutcome.RECORDED
    assert decision.usage is not None
    assert decision.usage.quantity == Quantity(1, "minute")
    assert decision.usage.account_id == "cma_1"


def test_the_ingestion_decision_is_a_pure_function():
    """The property everything else rests on: same inputs, same outcome, every time."""
    first = _decide()
    second = _decide()
    assert first.usage == second.usage
    assert first.outcome is second.outcome


def test_an_event_with_no_id_is_quarantined_as_malformed():
    decision = _decide(event=_event(""))
    assert decision.reason is QuarantineReason.MALFORMED_EVENT
    assert "counted exactly once" in decision.detail


def test_an_unknown_meter_quarantines_rather_than_dropping():
    decision = _decide(meter=None)
    assert decision.is_quarantined
    assert decision.reason is QuarantineReason.UNKNOWN_METER


def test_a_non_authoritative_source_is_quarantined_not_double_counted():
    """A meter has one source. If two planes emit an overlapping signal, exactly one is
    authoritative and the other is visible rather than silently added."""
    decision = _decide(event=_event(source=MeterSource.PROVIDER_LLM))
    assert decision.reason is QuarantineReason.NON_AUTHORITATIVE_SOURCE


def test_an_unaccepted_event_type_from_the_right_source_is_quarantined():
    decision = _decide(event=_event(event_type="metric.session.started"))
    assert decision.reason is QuarantineReason.NON_AUTHORITATIVE_SOURCE


def test_a_retired_meter_quarantines_its_events():
    decision = _decide(meter=_meter(state=MeterState.RETIRED))
    assert decision.reason is QuarantineReason.RETIRED_METER


def test_a_deprecated_meter_still_counts():
    """A deprecated meter stops being offered; it does not stop counting what customers already
    send."""
    decision = _decide(meter=_meter(state=MeterState.DEPRECATED))
    assert decision.is_recorded


def test_a_unit_mismatch_with_no_normalization_is_quarantined():
    decision = _decide(meter=_meter(unit="minute", normalization=Normalization()), raw_unit="ms")
    assert decision.reason is QuarantineReason.UNIT_MISMATCH


def test_unattributable_usage_is_quarantined_never_billed():
    """Attribution is total: every record resolves to exactly one billing account, or it is
    visible (ADR-0023 M6)."""
    decision = _decide(accounts_by_scope={})
    assert decision.reason is QuarantineReason.UNATTRIBUTABLE
    assert decision.usage is None


def test_an_event_with_no_scope_is_unattributable():
    assert _decide(scope=None).reason is QuarantineReason.UNATTRIBUTABLE


def test_a_far_future_event_is_quarantined():
    decision = _decide(event=_event(observed_at=50_000.0 + MAX_CLOCK_SKEW_SECONDS + 1_000.0))
    assert decision.reason is QuarantineReason.FUTURE_EVENT


def test_small_clock_skew_is_tolerated():
    assert _decide(event=_event(observed_at=50_000.0 + 10.0)).is_recorded


def test_a_negative_raw_signal_is_quarantined_not_raised():
    assert _decide(raw_units=-5).reason is QuarantineReason.INVALID_QUANTITY


def test_a_duplicate_is_a_success_not_an_error():
    """At-least-once delivery makes replays normal; treating one as a failure would make every
    retry look like an incident."""
    decision = _decide(already_recorded=True)
    assert decision.outcome is IngestionOutcome.DUPLICATE
    assert decision.usage is None
    assert not decision.is_quarantined


def test_a_duplicate_of_an_unattributable_event_reports_the_real_problem():
    """The exactly-once gate sits *after* attribution deliberately: otherwise the quarantine
    backlog would under-count the actual failure."""
    decision = _decide(already_recorded=True, accounts_by_scope={})
    assert decision.reason is QuarantineReason.UNATTRIBUTABLE


def test_attribution_resolves_narrowest_first():
    """A customer who attached billing at the project level meant it; falling back to the
    organization would bill the wrong entity in exactly the arrangements where it matters."""
    scope = Scope(
        org_id="o",
        workspace_id="w",
        project_id="p",
        kind=Scope.__dataclass_fields__["kind"].default,
    )
    attribution = AttributionService().attribute(
        scope, accounts_by_scope={"o": "acct_org", "p": "acct_project"}
    )
    assert attribution.account_id == "acct_project"


def test_provenance_records_what_it_would_take_to_re_derive_the_record():
    decision = _decide(meter=_meter(normalization=Normalization(1, 60_000)), ingest_run_id="irn_1")
    p = decision.usage.provenance
    assert p.meter_pin == "voice.minutes@1"
    assert p.raw_units == 60_000 and p.raw_unit == "ms"
    assert p.was_normalized
    assert p.ingest_run_id == "irn_1"


# ── 4. Foundation: late arrival ─────────────────────────────────────────────────


def test_an_in_period_event_lands_in_its_own_period():
    policy = LateArrivalPolicy()
    disposition = policy.disposition(
        _meter(),
        _event(observed_at=2_000.0, received_at=2_100.0),
        period=PERIOD,
        period_state=PeriodState.OPEN,
    )
    assert disposition is LateArrivalDisposition.IN_PERIOD


def test_a_late_event_inside_the_window_joins_its_original_period():
    policy = LateArrivalPolicy()
    disposition = policy.disposition(
        _meter(late_arrival_window_seconds=10_000.0),
        _event(observed_at=PE + 1.0, received_at=PE + 100.0),
        period=PERIOD,
        period_state=PeriodState.OPEN,
    )
    assert disposition is LateArrivalDisposition.LATE_IN_WINDOW


def test_a_late_event_outside_the_window_becomes_an_adjustment():
    policy = LateArrivalPolicy()
    disposition = policy.disposition(
        _meter(late_arrival_window_seconds=10.0),
        _event(observed_at=2_000.0, received_at=PE + 1_000.0),
        period=PERIOD,
        period_state=PeriodState.OPEN,
    )
    assert disposition is LateArrivalDisposition.LATE_OUT_OF_WINDOW
    assert policy.status_for(disposition) is UsageStatus.ADJUSTMENT


def test_any_event_for_a_sealed_period_becomes_an_adjustment():
    policy = LateArrivalPolicy()
    disposition = policy.disposition(
        _meter(), _event(), period=PERIOD, period_state=PeriodState.SEALED
    )
    assert disposition is LateArrivalDisposition.PERIOD_SEALED
    assert policy.requires_open_period(disposition)


def test_an_adjustment_never_lands_in_the_period_it_adjusts():
    """Writing into the sealed period would break its digest — the retroactive mutation
    ADR-0023 M7 forbids."""
    policy = LateArrivalPolicy()
    later = BillingPeriod(PE, PE + 1_000.0)
    assert (
        policy.landing_period(LateArrivalDisposition.PERIOD_SEALED, original=PERIOD, current=later)
        == later
    )
    # With no open period to receive it, the landing falls back — and the Application
    # quarantines rather than writing into the sealed original.
    assert (
        policy.landing_period(LateArrivalDisposition.PERIOD_SEALED, original=PERIOD, current=PERIOD)
        == PERIOD
    )


def test_late_arrival_is_deterministic_for_identical_inputs():
    policy = LateArrivalPolicy()
    meter, event = (
        _meter(late_arrival_window_seconds=50.0),
        _event(observed_at=2_000.0, received_at=PE + 900.0),
    )
    answers = {
        policy.disposition(meter, event, period=PERIOD, period_state=PeriodState.OPEN)
        for _ in range(20)
    }
    assert len(answers) == 1


# ── 5. Foundation: lifecycles ───────────────────────────────────────────────────


def test_the_meter_lifecycle_has_no_deletion_state():
    for state in MeterState:
        for trigger in METER_LIFECYCLE.triggers_from(state):
            assert "delet" not in METER_LIFECYCLE.get(state, trigger).target.value


def test_a_retired_meter_can_never_be_reactivated():
    """Its code may have been reused; resurrecting it would make `code@version` ambiguous, and
    that pin is what every usage record points at."""
    lifecycle = MeterLifecycleService()
    assert lifecycle.allowed(MeterState.RETIRED) == ()


def test_a_deprecated_meter_can_be_reinstated():
    assert MeterLifecycleService().can(MeterState.DEPRECATED, MeterTrigger.REINSTATE)


def test_ingestible_meter_states_are_active_and_deprecated():
    assert INGESTIBLE_METER_STATES == frozenset({MeterState.ACTIVE, MeterState.DEPRECATED})


def test_sealing_is_a_one_way_door():
    """Enumerated across the whole machine: there is no edge out of SEALED, to anything."""
    assert USAGE_PERIOD_LIFECYCLE.triggers_from(PeriodState.SEALED) == frozenset() or not list(
        USAGE_PERIOD_LIFECYCLE.triggers_from(PeriodState.SEALED)
    )
    assert UsagePeriodLifecycleService().allowed(PeriodState.SEALED) == ()
    for state in PeriodState:
        for trigger in USAGE_PERIOD_LIFECYCLE.triggers_from(state):
            target = USAGE_PERIOD_LIFECYCLE.get(state, trigger).target
            assert state is not PeriodState.SEALED, (state, trigger, target)


def test_a_period_cannot_be_sealed_without_a_digest():
    """Without it, a later replay cannot prove it reproduced the same usage."""
    period = UsagePeriod(id="upd_1", org_id="org_1", account_id="cma_1", period=PERIOD)
    with pytest.raises(BillingConflict):
        UsagePeriodLifecycleService().apply(period, PeriodTrigger.SEAL, at=1.0)


def test_an_illegal_period_transition_is_refused_not_coerced():
    with pytest.raises(InvalidBillingTransition):
        UsagePeriodLifecycleService().next_state(PeriodState.SEALED, PeriodTrigger.BEGIN_CLOSE)


def test_a_closing_period_refuses_in_period_usage():
    period = UsagePeriod(
        id="upd_1", org_id="o", account_id="a", period=PERIOD, state=PeriodState.CLOSING
    )
    with pytest.raises(BillingConflict) as excinfo:
        UsagePeriodLifecycleService().guard_open(period)
    assert "adjustments" in str(excinfo.value)


def test_quarantined_events_are_never_deleted_only_resolved_or_discarded():
    lifecycle = QuarantineLifecycleService()
    assert set(lifecycle.allowed(QuarantineState.PENDING)) == {
        QuarantineTrigger.RESOLVE,
        QuarantineTrigger.DISCARD,
    }
    assert lifecycle.allowed(QuarantineState.RESOLVED) == ()
    assert lifecycle.allowed(QuarantineState.DISCARDED) == ()
    for state in QuarantineState:
        for trigger in QUARANTINE_LIFECYCLE.triggers_from(state):
            assert "delet" not in QUARANTINE_LIFECYCLE.get(state, trigger).target.value


# ── 6. Foundation: validation ───────────────────────────────────────────────────


def test_two_live_meters_may_not_claim_the_same_signal():
    """The double-counting failure, caught at the only cheap moment: when the second meter is
    defined."""
    findings = MeteringValidator().validate_meters(
        [
            _meter("a", accepted_event_types=("metric.x",)),
            _meter("b", accepted_event_types=("metric.x",)),
        ]
    )
    assert any(f.code.value == "non_authoritative_source" for f in findings.errors)


def test_a_wildcard_meter_overlaps_with_everything_from_its_source():
    """An empty allow-list means "any type from this source" — the case a naive set intersection
    would miss entirely."""
    findings = MeteringValidator().validate_meters(
        [_meter("a", accepted_event_types=()), _meter("b", accepted_event_types=("metric.x",))]
    )
    assert findings.has_errors


def test_meters_on_different_sources_do_not_collide():
    findings = MeteringValidator().validate_meters(
        [
            _meter("a", source=MeterSource.RUNTIME_SESSION),
            _meter("b", source=MeterSource.PROVIDER_LLM),
        ]
    )
    assert not findings.has_errors


def test_a_meter_with_no_unit_is_rejected():
    assert MeteringValidator().validate_meter(_meter(unit="")).has_errors


def test_an_invalid_normalization_is_rejected():
    findings = MeteringValidator().validate_meter(_meter(normalization=Normalization(0, 1)))
    assert any(f.code.value == "invalid_normalization" for f in findings.errors)


def test_a_zero_late_window_is_a_warning_not_an_error():
    findings = MeteringValidator().validate_meter(_meter(late_arrival_window_seconds=0.0))
    assert not findings.has_errors
    assert findings.warnings


def test_an_event_with_no_id_fails_validation():
    findings = MeteringValidator().validate_event(_event(""))
    assert any(f.code.value == "empty_source_event" for f in findings.errors)


def test_two_usage_records_for_one_source_event_are_a_reported_incident():
    """The one invariant that cannot be recovered from: a double-billed event needs a credit note
    and a conversation, not a re-run."""
    usage = _decide().usage
    findings = MeteringValidator().validate_usage_set([usage, usage])
    assert any(f.code.value == "duplicate_usage_record" for f in findings.errors)
    assert "financial-integrity incident" in findings.errors[0].remediation


def test_usage_whose_id_is_not_derived_is_rejected():
    from dataclasses import replace

    tampered = replace(_decide().usage, id="usg_handwritten")
    findings = MeteringValidator().validate_usage(tampered)
    assert any(f.code.value == "metering_provenance_integrity" for f in findings.errors)


def test_usage_whose_provenance_pin_disagrees_is_rejected():
    from dataclasses import replace

    usage = _decide().usage
    tampered = replace(usage, provenance=replace(usage.provenance, meter_pin="other@9"))
    assert MeteringValidator().validate_usage(tampered).has_errors


def test_a_zero_correction_is_rejected():
    from billing.foundation import CorrectionKind, UsageCorrection

    findings = MeteringValidator().validate_correction(
        UsageCorrection(
            id="c",
            org_id="o",
            corrects_usage_id="u",
            kind=CorrectionKind.CORRECTION,
            delta_units=0,
            unit="minute",
            reason="x",
        )
    )
    assert findings.has_errors


def test_a_correction_with_no_reason_is_rejected():
    """An unexplained change to a billed figure is indistinguishable from an error."""
    from billing.foundation import CorrectionKind, UsageCorrection

    findings = MeteringValidator().validate_correction(
        UsageCorrection(
            id="c",
            org_id="o",
            corrects_usage_id="u",
            kind=CorrectionKind.CORRECTION,
            delta_units=5,
            unit="minute",
        )
    )
    assert findings.has_errors


def test_a_quarantine_backlog_is_itself_reported():
    factory, _ = _factory()
    entries = tuple(
        factory.quarantine(
            org_id="org_1",
            event=_event(f"evt_{i}"),
            meter_code="m",
            reason=QuarantineReason.UNATTRIBUTABLE,
        )
        for i in range(QUARANTINE_BACKLOG_THRESHOLD + 1)
    )
    findings = MeteringValidator().validate_quarantine_backlog(entries)
    assert any(f.code.value == "quarantine_backlog" for f in findings.warnings)


# ── 7. Foundation: aggregation ──────────────────────────────────────────────────


def _usage_set(n=3, units=60_000):
    out = []
    for i in range(n):
        decision = _decide(
            event=_event(f"evt_{i}", observed_at=2_000.0 + i * 3_600.0),
            meter=_meter(normalization=Normalization(1, 60_000)),
            raw_units=units,
        )
        out.append(decision.usage)
    return out


def test_aggregation_is_independent_of_order():
    aggregator = UsageAggregator()
    usage = _usage_set()
    window = aggregator.window_for(WindowGranularity.PERIOD, at=PS, period=PERIOD)
    kwargs = dict(
        aggregate_id="uag_1",
        org_id="org_1",
        account_id="cma_1",
        meter_code="voice.minutes",
        scope=SCOPE,
        period=PERIOD,
        window=window,
        aggregation=MeterAggregation.SUM,
        unit="minute",
    )
    first = aggregator.aggregate(usage, **kwargs)
    second = aggregator.aggregate(list(reversed(usage)), **kwargs)
    assert first == second
    assert first.quantity == Quantity(3, "minute")


def test_the_aggregate_digest_distinguishes_a_changed_total_from_a_changed_method():
    aggregator = UsageAggregator()
    window = aggregator.window_for(WindowGranularity.PERIOD, at=PS, period=PERIOD)
    kwargs = dict(
        aggregate_id="uag_1",
        org_id="org_1",
        account_id="cma_1",
        meter_code="voice.minutes",
        scope=SCOPE,
        period=PERIOD,
        window=window,
        aggregation=MeterAggregation.SUM,
        unit="minute",
    )
    assert (
        aggregator.aggregate(_usage_set(3), **kwargs).source_digest
        != aggregator.aggregate(_usage_set(2), **kwargs).source_digest
    )


def test_max_aggregation_reuses_the_m9_2_fold():
    aggregator = UsageAggregator()
    window = aggregator.window_for(WindowGranularity.PERIOD, at=PS, period=PERIOD)
    result = aggregator.aggregate(
        _usage_set(3),
        aggregate_id="uag_1",
        org_id="org_1",
        account_id="cma_1",
        meter_code="voice.minutes",
        scope=SCOPE,
        period=PERIOD,
        window=window,
        aggregation=MeterAggregation.MAX,
        unit="minute",
    )
    assert result.quantity == Quantity(1, "minute")


def test_windows_are_anchored_to_the_period_not_to_the_epoch():
    """A customer's daily buckets line up with their billing day rather than UTC midnight."""
    aggregator = UsageAggregator()
    window = aggregator.window_for(WindowGranularity.DAY, at=PS + 100.0, period=PERIOD)
    assert window.start == PS


def test_windows_are_half_open():
    aggregator = UsageAggregator()
    window = aggregator.window_for(WindowGranularity.HOUR, at=PS, period=PERIOD)
    assert window.contains(window.start)
    assert not window.contains(window.end)


def test_aggregation_windows_on_observed_time_not_arrival_time():
    """A late event counts in the hour it happened, which is what makes a usage chart comparable
    across a metering outage."""
    aggregator = UsageAggregator()
    late = _decide(
        event=_event("evt_late", observed_at=PS + 100.0, received_at=PS + 90_000.0),
        meter=_meter(normalization=Normalization(1, 60_000), late_arrival_window_seconds=1e9),
    ).usage
    window = aggregator.window_for(WindowGranularity.HOUR, at=PS + 100.0, period=PERIOD)
    result = aggregator.aggregate(
        [late],
        aggregate_id="uag_1",
        org_id="org_1",
        account_id="cma_1",
        meter_code="voice.minutes",
        scope=SCOPE,
        period=PERIOD,
        window=window,
        aggregation=MeterAggregation.SUM,
        unit="minute",
    )
    assert result.record_count == 1


def test_an_empty_window_folds_to_zero_not_an_error():
    aggregator = UsageAggregator()
    window = aggregator.window_for(WindowGranularity.PERIOD, at=PS, period=PERIOD)
    result = aggregator.aggregate(
        [],
        aggregate_id="uag_1",
        org_id="org_1",
        account_id="cma_1",
        meter_code="voice.minutes",
        scope=SCOPE,
        period=PERIOD,
        window=window,
        aggregation=MeterAggregation.SUM,
        unit="minute",
    )
    assert result.quantity == Quantity(0, "minute")


# ── 8. Foundation: sealing and digests ──────────────────────────────────────────


def test_the_seal_digest_ignores_ingestion_timestamps():
    """Too much in the digest and every replay looks like a mismatch; too little and a real
    difference hides."""
    from dataclasses import replace

    sealing = UsageSealingService()
    usage = _usage_set(3)
    replayed = [
        replace(
            u,
            recorded_at=u.recorded_at + 9_999.0,
            provenance=replace(u.provenance, ingested_at=1e9, ingest_run_id="other", replayed=True),
        )
        for u in usage
    ]
    assert sealing.seal_digest(usage) == sealing.seal_digest(replayed)


def test_the_seal_digest_notices_a_changed_quantity():
    from dataclasses import replace

    sealing = UsageSealingService()
    usage = _usage_set(2)
    tampered = [replace(usage[0], quantity=Quantity(99, "minute")), usage[1]]
    assert sealing.seal_digest(usage) != sealing.seal_digest(tampered)


def test_the_seal_digest_ignores_record_order():
    sealing = UsageSealingService()
    usage = _usage_set(4)
    assert sealing.seal_digest(usage) == sealing.seal_digest(list(reversed(usage)))


def test_verifying_a_sealed_period_detects_tampering():

    sealing = UsageSealingService()
    usage = _usage_set(2)
    period = UsagePeriod(
        id="upd_1",
        org_id="org_1",
        account_id="cma_1",
        period=PERIOD,
        state=PeriodState.SEALED,
        seal_digest=sealing.seal_digest(usage),
    )
    assert sealing.verify(period, usage)
    assert not sealing.verify(period, usage[:1])


def test_an_unsealed_period_always_verifies():
    period = UsagePeriod(id="u", org_id="o", account_id="a", period=PERIOD)
    assert UsageSealingService().verify(period, _usage_set(2))


# ── 9. Foundation: replay ───────────────────────────────────────────────────────


def _replay_events(n=3):
    return tuple(
        (_event(f"evt_{i}", observed_at=2_000.0 + i * 10.0), 60_000, "ms", SCOPE) for i in range(n)
    )


def _meter_for(event, meters):
    return next((m for m in meters.values() if m.accepts(event.source, event.event_type)), None)


def test_replaying_an_empty_store_produces_the_expected_usage():
    meter = _meter(normalization=Normalization(1, 60_000))
    result = UsageReplayService().replay(
        _replay_events(3),
        org_id="org_1",
        account_id="cma_1",
        period=PERIOD,
        meters_by_code={meter.code: meter},
        meter_for_event=_meter_for,
        accounts_by_scope={"org_1": "cma_1"},
        at=60_000.0,
    )
    assert result.usage_produced == 3
    assert result.duplicates_skipped == 0


def test_replaying_an_already_ingested_period_produces_nothing_new():
    meter = _meter(normalization=Normalization(1, 60_000))
    events = _replay_events(3)
    first = UsageReplayService().replay(
        events,
        org_id="org_1",
        account_id="cma_1",
        period=PERIOD,
        meters_by_code={meter.code: meter},
        meter_for_event=_meter_for,
        accounts_by_scope={"org_1": "cma_1"},
        at=60_000.0,
    )
    second = UsageReplayService().replay(
        events,
        org_id="org_1",
        account_id="cma_1",
        period=PERIOD,
        meters_by_code={meter.code: meter},
        meter_for_event=_meter_for,
        accounts_by_scope={"org_1": "cma_1"},
        existing=first.usage,
        seal_digest=first.replay_digest,
        at=90_000.0,
    )
    assert second.usage_produced == 0
    assert second.duplicates_skipped == 3
    assert second.matches_seal


def test_replay_is_independent_of_event_order():
    meter = _meter(normalization=Normalization(1, 60_000))
    events = _replay_events(4)
    kwargs = dict(
        org_id="org_1",
        account_id="cma_1",
        period=PERIOD,
        meters_by_code={meter.code: meter},
        meter_for_event=_meter_for,
        accounts_by_scope={"org_1": "cma_1"},
        at=60_000.0,
    )
    forward = UsageReplayService().replay(events, **kwargs)
    backward = UsageReplayService().replay(tuple(reversed(events)), **kwargs)
    assert forward.replay_digest == backward.replay_digest


def test_replay_uses_the_same_ingestion_decision_as_the_live_path():
    """Not a second implementation that could drift from it — a replay using different code
    would prove nothing about the original."""
    import inspect

    source = inspect.getsource(UsageReplayService.replay)
    assert "self._ingestion.decide(" in source


# ── 10. Foundation: reconciliation ──────────────────────────────────────────────


def test_reconciliation_is_clean_when_the_log_and_usage_agree():
    usage = _usage_set(2)
    report = UsageReconciliationService().reconcile(
        org_id="org_1",
        account_id="cma_1",
        period=PERIOD,
        expected=tuple((u.source.event_id, u.meter_code, u.quantity.units) for u in usage),
        recorded=usage,
    )
    assert report.is_clean
    assert report.duplicate_count == 0


def test_reconciliation_finds_missing_usage():
    from billing.foundation import DiscrepancyKind

    report = UsageReconciliationService().reconcile(
        org_id="org_1",
        account_id="cma_1",
        period=PERIOD,
        expected=(("evt_missing", "voice.minutes", 5),),
        recorded=(),
    )
    assert report.of_kind(DiscrepancyKind.MISSING_USAGE)


def test_reconciliation_finds_orphan_usage():
    from billing.foundation import DiscrepancyKind

    report = UsageReconciliationService().reconcile(
        org_id="org_1", account_id="cma_1", period=PERIOD, expected=(), recorded=_usage_set(1)
    )
    assert report.of_kind(DiscrepancyKind.ORPHAN_USAGE)


def test_reconciliation_finds_a_quantity_mismatch():
    from billing.foundation import DiscrepancyKind

    usage = _usage_set(1)
    report = UsageReconciliationService().reconcile(
        org_id="org_1",
        account_id="cma_1",
        period=PERIOD,
        expected=((usage[0].source.event_id, usage[0].meter_code, 999),),
        recorded=usage,
    )
    assert report.of_kind(DiscrepancyKind.QUANTITY_MISMATCH)


def test_reconciliation_calls_out_duplicates_separately():
    """The only discrepancy that re-running cannot fix."""
    usage = _usage_set(1)
    report = UsageReconciliationService().reconcile(
        org_id="org_1",
        account_id="cma_1",
        period=PERIOD,
        expected=((usage[0].source.event_id, usage[0].meter_code, usage[0].quantity.units),),
        recorded=usage * 2,
    )
    assert report.duplicate_count == 1
    assert not report.is_clean


def test_reconciliation_is_deterministically_ordered():
    report = UsageReconciliationService().reconcile(
        org_id="org_1",
        account_id="cma_1",
        period=PERIOD,
        expected=(("z", "m", 1), ("a", "m", 1)),
        recorded=(),
    )
    assert [d.event_id for d in report.discrepancies] == ["a", "z"]


# ── 11. Foundation: corrections, inspection, search, export ─────────────────────


def test_a_correction_produces_a_new_record_and_never_edits_the_original():
    from billing.foundation import CorrectionKind, UsageCorrection

    original = _usage_set(1)[0]
    correction = UsageCorrection(
        id="ucr_1",
        org_id="org_1",
        corrects_usage_id=original.id,
        kind=CorrectionKind.REVERSAL,
        delta_units=-1,
        unit="minute",
        reason="duplicate session",
    )
    adjustment = UsageCorrectionService().apply(
        original, correction, usage_id="usg_adj", at=90_000.0
    )
    assert adjustment.status is UsageStatus.ADJUSTMENT
    assert adjustment.corrects_usage_id == original.id
    assert original.quantity == Quantity(1, "minute")  # untouched


def test_a_correction_cannot_produce_negative_usage():
    from billing.foundation import CorrectionKind, UsageCorrection

    original = _usage_set(1)[0]
    with pytest.raises(BillingConflict):
        UsageCorrectionService().apply(
            original,
            UsageCorrection(
                id="ucr_1",
                org_id="org_1",
                corrects_usage_id=original.id,
                kind=CorrectionKind.REVERSAL,
                delta_units=-999,
                unit="minute",
                reason="x",
            ),
            usage_id="usg_adj",
            at=1.0,
        )


def test_a_correction_in_the_wrong_unit_is_refused():
    from billing.foundation import CorrectionKind, UsageCorrection

    original = _usage_set(1)[0]
    with pytest.raises(BillingConflict):
        UsageCorrectionService().apply(
            original,
            UsageCorrection(
                id="ucr_1",
                org_id="org_1",
                corrects_usage_id=original.id,
                kind=CorrectionKind.CORRECTION,
                delta_units=1,
                unit="token",
                reason="x",
            ),
            usage_id="usg_adj",
            at=1.0,
        )


def test_correction_kinds_distinguish_what_an_auditor_needs():
    from billing.foundation import CorrectionKind

    service = UsageCorrectionService()
    assert service.kind_for(5, period_changed=False) is CorrectionKind.CORRECTION
    assert service.kind_for(-5, period_changed=False) is CorrectionKind.REVERSAL
    assert service.kind_for(5, period_changed=True) is CorrectionKind.ADJUSTMENT


def test_inspection_explains_how_a_record_became_what_it_is():
    usage = _decide(meter=_meter(normalization=Normalization(1, 60_000)), raw_units=185_000).usage
    lines = " | ".join(UsageInspector().explain(usage))
    assert "voice.minutes@1" in lines
    assert "normalized 185000 ms" in lines
    assert "attributed to account cma_1" in lines


def test_search_filters_compose_and_page_deterministically():
    service = UsageSearchService()
    usage = _usage_set(10)
    assert service.search(usage, UsageSearchQuery(billable_only=True)).total == 10
    page = service.search(usage, UsageSearchQuery(limit=3, offset=3))
    assert len(page.usage) == 3 and page.total == 10
    assert service.search(usage, UsageSearchQuery(event_id="evt_5")).total == 1


def test_an_export_of_unchanged_usage_is_byte_identical():
    exporter = UsageExportService()
    usage = _usage_set(4)
    first = exporter.export(usage, org_id="org_1", account_id="cma_1", period=PERIOD)
    second = exporter.export(
        list(reversed(usage)), org_id="org_1", account_id="cma_1", period=PERIOD
    )
    assert first.digest == second.digest
    assert first.rows == second.rows


def test_an_export_row_is_self_describing():
    export = UsageExportService().export(
        _usage_set(1), org_id="org_1", account_id="cma_1", period=PERIOD
    )
    row = export.rows[0]
    assert row.meter_pin and row.event_id and row.digest and row.unit


# ── 12. Foundation: factory discipline + the metric-stream seam ─────────────────


def test_a_rejected_meter_emits_no_business_event():
    factory, emitter = _factory()
    before = len(emitter.events)
    with pytest.raises(BillingValidationError):
        factory.define_meter(org_id="org_1", code="x", unit="")
    assert len(emitter.events) == before


def test_a_successful_meter_emits_exactly_one_business_event():
    from billing.foundation import BillingEventType

    factory, emitter = _factory()
    factory.define_meter(org_id="org_1", code="x", unit="unit")
    assert len(emitter.of_type(BillingEventType.METER_CREATED)) == 1


def test_a_duplicate_ingestion_emits_its_own_fact():
    """ "We saw this event again and did nothing" is a distinct business fact — and the evidence
    that idempotency worked."""
    from billing.foundation import BillingEventType

    factory, emitter = _factory()
    factory.emit_duplicate_ignored(org_id="org_1", event_id="evt_1", meter_code="m")
    assert len(emitter.of_type(BillingEventType.USAGE_DUPLICATE_IGNORED)) == 1
    assert not emitter.of_type(BillingEventType.USAGE_RECORDED)


def test_versioning_a_meter_increments_its_version():
    factory, _ = _factory()
    meter = factory.define_meter(org_id="org_1", code="x", unit="unit")
    assert factory.version_meter(meter, unit="minute").version == 2


def test_the_metric_stream_port_has_no_way_to_emit():
    """Billing is structurally incapable of producing an operational metric (ADR-0023 §6)."""
    from billing.foundation import MetricStreamPort

    methods = {m for m in dir(MetricStreamPort) if not m.startswith("_")}
    assert methods == {"drain", "replay"}
    for forbidden in ("emit", "publish", "write", "send", "produce"):
        assert forbidden not in methods


def test_an_unwired_stream_ingests_nothing_rather_than_inventing_usage():
    assert NullMetricStream().drain("org_1") == ()
    assert NullMetricStream().replay("org_1", 0.0, 1e9) == ()


def test_draining_is_non_destructive_because_redelivery_is_normal():
    """A test stream that could not redeliver would hide exactly the bug idempotency prevents."""
    stream = RecordingMetricStream([_signal(1)])
    assert len(stream.drain("org_1")) == 1
    assert len(stream.drain("org_1")) == 1


# ── 13. The load-bearing correctness demonstrations ─────────────────────────────


def _account(app, name="acct") -> str:
    account = app.commands.dispatch(
        CreateBillingAccount(**ORG, display_name=name, currency=Currency.INR)
    )
    app.commands.dispatch(ActivateBillingAccount(**ORG, account_id=account.id))
    return account.id


def _seed(app, *, minutes=True, open_period=True) -> str:
    account_id = _account(app)
    app.commands.dispatch(
        DefineCommercialMeter(
            **ORG,
            code="voice.minutes",
            unit="minute",
            source=MeterSource.RUNTIME_SESSION,
            normalization=Normalization(1, 60_000) if minutes else Normalization(),
            accepted_event_types=("metric.session.duration_ms",),
        )
    )
    app.commands.dispatch(ActivateCommercialMeter(**ORG, code="voice.minutes"))
    if open_period:
        app.commands.dispatch(
            OpenUsagePeriod(**ORG, account_id=account_id, period_start=PS, period_end=PE)
        )
    return account_id


def test_demonstration_one_source_event_produces_exactly_one_usage_record(app, stream):
    account_id = _seed(app)
    stream.offer(_signal(1, 60_000, 2_000.0))
    app.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    snapshot = app.queries.dispatch(
        GetCommercialUsageSnapshot(**ORG, account_id=account_id, period_start=PS, period_end=PE)
    )
    assert snapshot.record_count == 1


def test_demonstration_duplicate_source_events_never_create_duplicate_usage(app, stream):
    """At-least-once delivery, exactly-once billing."""
    account_id = _seed(app)
    stream.offer(_signal(1, 60_000, 2_000.0))
    for _ in range(5):
        receipt = app.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    assert receipt.duplicates == 1 and receipt.recorded == 0
    snapshot = app.queries.dispatch(
        GetCommercialUsageSnapshot(**ORG, account_id=account_id, period_start=PS, period_end=PE)
    )
    assert snapshot.record_count == 1


def test_demonstration_idempotency_under_concurrent_ingestion():
    """Two Applications over one database, both ingesting the same event. One wins the insert;
    the other's `IntegrityError` is caught inside a SAVEPOINT so its own transaction survives,
    and both callers see the same billable fact."""
    database = BillingDatabase("sqlite://")
    stream = RecordingMetricStream([_signal(1, 60_000, 2_000.0)])
    first = build_persistent_billing_application(
        database=database,
        ids=SequentialIdGenerator(),
        clock=FixedClock(50_000.0),
        authorizer=AllowAllAuthorizer(),
        metric_stream=stream,
    )
    account_id = _seed(first.application)
    second = build_persistent_billing_application(
        database=database,
        ids=DefaultIdGenerator(),
        clock=FixedClock(50_000.0),
        create_all=False,
        authorizer=AllowAllAuthorizer(),
        metric_stream=stream,
    )

    a = first.application.commands.dispatch(
        DrainMetricStream(**ORG, period_start=PS, period_end=PE)
    )
    b = second.application.commands.dispatch(
        DrainMetricStream(**ORG, period_start=PS, period_end=PE)
    )
    assert a.recorded + b.recorded == 1
    assert a.duplicates + b.duplicates == 1

    snapshot = second.application.queries.dispatch(
        GetCommercialUsageSnapshot(**ORG, account_id=account_id, period_start=PS, period_end=PE)
    )
    assert snapshot.record_count == 1
    database.close()


def test_demonstration_a_racing_append_returns_the_stored_record_not_an_error():
    """The repository contract directly: appending the same usage twice never raises and never
    writes a second row."""
    database = BillingDatabase("sqlite://")
    wired = build_persistent_billing_application(
        database=database,
        ids=SequentialIdGenerator(),
        clock=FixedClock(50_000.0),
        authorizer=AllowAllAuthorizer(),
    )
    repo = wired.application.context.metering_engine.usage
    usage = _decide(meter=_meter(normalization=Normalization(1, 60_000))).usage
    first = repo.append(usage)
    second = repo.append(usage)
    assert first.id == second.id
    assert len(repo.all("org_1")) == 1
    database.close()


def test_demonstration_deterministic_replay_produces_identical_commercial_usage(app, stream):
    account_id = _seed(app)
    for i, (ms, at) in enumerate([(90_000, 2_000.0), (120_000, 3_000.0), (30_000, 4_000.0)]):
        stream.offer(_signal(i, ms, at))
    app.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    sealed = app.commands.dispatch(
        SealUsagePeriod(**ORG, account_id=account_id, period_start=PS, period_end=PE)
    )
    assert sealed.seal_digest

    replay = app.commands.dispatch(
        ReplayUsagePeriod(**ORG, account_id=account_id, period_start=PS, period_end=PE)
    )
    assert replay.events_replayed == 3
    assert replay.usage_produced == 0
    assert replay.duplicates_skipped == 3
    assert replay.matches_seal, "replay must reproduce exactly what the period sealed"


def test_demonstration_replay_after_restart(stream):
    """A second Application over the same storage, with a *different* id generator and clock,
    still reproduces the sealed digest — because usage ids are derived, not minted."""
    database = BillingDatabase("sqlite://")
    first = build_persistent_billing_application(
        database=database,
        ids=SequentialIdGenerator(),
        clock=FixedClock(50_000.0),
        authorizer=AllowAllAuthorizer(),
        metric_stream=stream,
    )
    account_id = _seed(first.application)
    stream.offer(_signal(1, 90_000, 2_000.0))
    stream.offer(_signal(2, 30_000, 3_000.0))
    first.application.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    sealed = first.application.commands.dispatch(
        SealUsagePeriod(**ORG, account_id=account_id, period_start=PS, period_end=PE)
    )

    restarted = build_persistent_billing_application(
        database=database,
        ids=DefaultIdGenerator(),
        clock=FixedClock(999_999.0),
        create_all=False,
        authorizer=AllowAllAuthorizer(),
        metric_stream=stream,
    )
    replay = restarted.application.commands.dispatch(
        ReplayUsagePeriod(**ORG, account_id=account_id, period_start=PS, period_end=PE)
    )
    assert replay.seal_digest == sealed.seal_digest
    assert replay.matches_seal
    database.close()


def test_demonstration_late_events_remain_deterministic(app, stream):
    """The same late event, ingested repeatedly, always lands in the same place."""
    account_id = _seed(app)
    stream.offer(_signal(1, 60_000, 2_000.0, received_at=2_100.0))
    app.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    records = app.queries.dispatch(
        SearchCommercialUsage(**ORG, query=UsageSearchQuery(account_id=account_id))
    )
    landed = records.usage[0]
    for _ in range(3):
        app.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    again = app.queries.dispatch(
        SearchCommercialUsage(**ORG, query=UsageSearchQuery(account_id=account_id))
    )
    assert again.total == 1
    assert again.usage[0].id == landed.id
    assert again.usage[0].period_start == landed.period_start


def test_demonstration_usage_sealing_rules_reject_new_in_period_usage(app, stream):
    """Once a period is sealed, an event for it can never become in-period usage. With no open
    period to receive the adjustment, it is quarantined — never billed into a frozen period and
    never dropped."""
    account_id = _seed(app)
    stream.offer(_signal(1, 60_000, 2_000.0))
    app.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    sealed = app.commands.dispatch(
        SealUsagePeriod(**ORG, account_id=account_id, period_start=PS, period_end=PE)
    )

    stream.offer(_signal(99, 60_000, 5_000.0))
    receipt = app.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    assert receipt.recorded == 0
    assert receipt.quarantined == 1

    unchanged = app.queries.dispatch(
        GetUsagePeriodState(**ORG, account_id=account_id, period_start=PS, period_end=PE)
    )
    assert unchanged.seal_digest == sealed.seal_digest
    assert unchanged.state == "sealed"


def test_demonstration_a_sealed_period_can_never_be_reopened(app, stream):
    account_id = _seed(app)
    stream.offer(_signal(1, 60_000, 2_000.0))
    app.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    app.commands.dispatch(
        SealUsagePeriod(**ORG, account_id=account_id, period_start=PS, period_end=PE)
    )
    with pytest.raises(InvalidBillingTransition):
        app.commands.dispatch(
            BeginUsagePeriodClose(**ORG, account_id=account_id, period_start=PS, period_end=PE)
        )


def test_demonstration_a_late_event_lands_as_an_adjustment_in_an_open_period(app, stream):
    """The correct outcome when a later period exists: an explicitly labelled adjustment, in a
    period that is not frozen."""
    account_id = _seed(app)
    stream.offer(_signal(1, 60_000, 2_000.0))
    app.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    app.commands.dispatch(
        SealUsagePeriod(**ORG, account_id=account_id, period_start=PS, period_end=PE)
    )
    later_start, later_end = PE, PE + 100_000.0
    app.commands.dispatch(
        OpenUsagePeriod(
            **ORG, account_id=account_id, period_start=later_start, period_end=later_end
        )
    )

    stream.offer(_signal(99, 60_000, 5_000.0))
    receipt = app.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    assert receipt.adjusted == 1

    adjustments = app.queries.dispatch(
        SearchCommercialUsage(**ORG, query=UsageSearchQuery(adjustments_only=True))
    )
    assert adjustments.total == 1
    assert adjustments.usage[0].period_start == later_start


def test_demonstration_append_only_commercial_history(app, stream):
    """Neither the usage repository nor the activity repository exposes a way to change or remove
    a recorded fact."""
    from billing.foundation import CommercialUsageRepository, MeteringActivityRepository

    for contract in (CommercialUsageRepository, MeteringActivityRepository):
        methods = set(dir(contract))
        assert "delete" not in methods
        assert "update" not in methods
        assert "replace" not in methods

    account_id = _seed(app)
    stream.offer(_signal(1, 60_000, 2_000.0))
    app.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    timeline = app.queries.dispatch(MeteringTimeline(**ORG, account_id=account_id))
    assert any(a.action == "metering.usage_recorded" for a in timeline)


def test_demonstration_a_duplicate_is_recorded_in_history_as_a_fact(app, stream):
    _seed(app)
    stream.offer(_signal(1, 60_000, 2_000.0))
    app.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    app.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    timeline = app.queries.dispatch(MeteringTimeline(**ORG))
    assert any(a.action == "metering.usage_duplicate_ignored" for a in timeline)


def test_demonstration_no_dependency_on_runtime_availability(app):
    """With no metric stream wired at all, the engine still answers every read and refuses to
    invent usage. A metering outage delays billing; it never degrades a conversation."""
    wired = build_persistent_billing_application(
        ids=SequentialIdGenerator(),
        clock=FixedClock(50_000.0),
        authorizer=AllowAllAuthorizer(),
        metric_stream=None,  # nothing wired
    )
    account_id = _seed(wired.application)
    receipt = wired.application.commands.dispatch(
        DrainMetricStream(**ORG, period_start=PS, period_end=PE)
    )
    assert receipt.signals_seen == 0 and receipt.recorded == 0
    snapshot = wired.application.queries.dispatch(
        GetCommercialUsageSnapshot(**ORG, account_id=account_id, period_start=PS, period_end=PE)
    )
    assert snapshot.record_count == 0
    wired.database.close()


def test_demonstration_multi_instance_aggregate_rebuild_converges(stream):
    """An aggregate rebuilt on a second instance equals the one the first maintained."""
    database = BillingDatabase("sqlite://")
    first = build_persistent_billing_application(
        database=database,
        ids=SequentialIdGenerator(),
        clock=FixedClock(50_000.0),
        authorizer=AllowAllAuthorizer(),
        metric_stream=stream,
    )
    account_id = _seed(first.application)
    for i, ms in enumerate([60_000, 120_000, 30_000]):
        stream.offer(_signal(i, ms, 2_000.0 + i * 100))
    first.application.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    first.application.commands.dispatch(
        RebuildUsageAggregates(**ORG, account_id=account_id, period_start=PS, period_end=PE)
    )
    before = first.application.queries.dispatch(
        GetCommercialUsageSnapshot(**ORG, account_id=account_id, period_start=PS, period_end=PE)
    )

    second = build_persistent_billing_application(
        database=database,
        ids=DefaultIdGenerator(),
        clock=FixedClock(70_000.0),
        create_all=False,
        authorizer=AllowAllAuthorizer(),
        metric_stream=stream,
    )
    second.application.commands.dispatch(
        RebuildUsageAggregates(**ORG, account_id=account_id, period_start=PS, period_end=PE)
    )
    after = second.application.queries.dispatch(
        GetCommercialUsageSnapshot(**ORG, account_id=account_id, period_start=PS, period_end=PE)
    )
    assert [(a.meter_code, a.units) for a in before.aggregates] == [
        (a.meter_code, a.units) for a in after.aggregates
    ]
    assert before.aggregates[0].source_digest == after.aggregates[0].source_digest
    database.close()


# ── 14. Application over a real database ────────────────────────────────────────


def test_normalization_end_to_end(app, stream):
    account_id = _seed(app)
    stream.offer(_signal(1, 185_000, 2_000.0))  # 3.083 min → 3
    app.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    app.commands.dispatch(
        RebuildUsageAggregates(**ORG, account_id=account_id, period_start=PS, period_end=PE)
    )
    snapshot = app.queries.dispatch(
        GetCommercialUsageSnapshot(**ORG, account_id=account_id, period_start=PS, period_end=PE)
    )
    assert snapshot.aggregates[0].units == 3
    assert snapshot.aggregates[0].unit == "minute"


def test_a_second_meter_claiming_the_same_signal_is_rejected(app):
    _seed(app)
    with pytest.raises(BillingValidationError):
        app.commands.dispatch(
            DefineCommercialMeter(
                **ORG,
                code="voice.minutes.copy",
                unit="minute",
                source=MeterSource.RUNTIME_SESSION,
                accepted_event_types=("metric.session.duration_ms",),
            )
        )


def test_the_meter_lifecycle_through_the_application(app):
    _seed(app)
    for command, expected in (
        (DeprecateCommercialMeter(**ORG, code="voice.minutes"), "deprecated"),
        (ReinstateCommercialMeter(**ORG, code="voice.minutes"), "active"),
        (RetireCommercialMeter(**ORG, code="voice.minutes"), "retired"),
    ):
        meter = app.commands.dispatch(command)
        assert meter.state.value == expected


def test_a_retired_meter_quarantines_new_events(app, stream):
    _seed(app)
    app.commands.dispatch(RetireCommercialMeter(**ORG, code="voice.minutes"))
    stream.offer(_signal(1, 60_000, 2_000.0))
    receipt = app.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    assert receipt.quarantined == 1
    quarantined = app.queries.dispatch(ListQuarantinedEvents(**ORG))
    assert quarantined[0].reason == "retired_meter"


def test_versioning_a_meter_does_not_change_existing_usage(app, stream):
    """Every record pins the `code@version` it was counted under, which is what makes an old
    invoice explainable after the meter moves on."""
    account_id = _seed(app)
    stream.offer(_signal(1, 60_000, 2_000.0))
    app.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    before = app.queries.dispatch(
        SearchCommercialUsage(**ORG, query=UsageSearchQuery(account_id=account_id))
    ).usage[0]

    app.commands.dispatch(
        VersionCommercialMeter(**ORG, code="voice.minutes", normalization=Normalization(1, 1))
    )
    after = app.queries.dispatch(
        SearchCommercialUsage(**ORG, query=UsageSearchQuery(account_id=account_id))
    ).usage[0]
    assert before == after
    assert after.meter_pin == "voice.minutes@1"


def test_opening_a_period_twice_is_idempotent(app):
    account_id = _seed(app)
    app.commands.dispatch(
        OpenUsagePeriod(**ORG, account_id=account_id, period_start=PS, period_end=PE)
    )
    periods = app.queries.dispatch(ListUsagePeriods(**ORG, account_id=account_id))
    assert len(periods) == 1


def test_unattributable_usage_is_quarantined_end_to_end(app, stream):
    """No billing account for the scope means quarantine, not a guess."""
    app.commands.dispatch(
        DefineCommercialMeter(
            **ORG,
            code="voice.minutes",
            unit="minute",
            source=MeterSource.RUNTIME_SESSION,
            accepted_event_types=("metric.session.duration_ms",),
        )
    )
    app.commands.dispatch(ActivateCommercialMeter(**ORG, code="voice.minutes"))
    stream.offer(_signal(1, 5, 2_000.0, raw_unit="minute"))
    receipt = app.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    assert receipt.quarantined == 1
    assert app.queries.dispatch(ListQuarantinedEvents(**ORG))[0].reason == "unattributable"


def test_an_ambiguous_organization_quarantines_rather_than_guessing(app, stream):
    """Two active accounts and no scope→account attachment: there is no honest way to choose, so
    the event stays visible instead of being billed to whichever was read last."""
    _seed(app)
    _account(app, "second")
    stream.offer(_signal(1, 60_000, 2_000.0))
    receipt = app.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    assert receipt.quarantined == 1


def test_a_quarantined_event_can_be_resolved_into_the_record_it_should_have_been(app, stream):
    account_id = _seed(app)
    app.commands.dispatch(RetireCommercialMeter(**ORG, code="voice.minutes"))
    stream.offer(_signal(1, 60_000, 2_000.0))
    app.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    entry = app.queries.dispatch(ListQuarantinedEvents(**ORG))[0]

    app.commands.dispatch(
        DefineCommercialMeter(
            **ORG,
            code="voice.minutes.v2",
            unit="minute",
            source=MeterSource.PROVIDER_LLM,
            normalization=Normalization(1, 60_000),
            accepted_event_types=("metric.session.duration_ms",),
        )
    )
    app.commands.dispatch(ActivateCommercialMeter(**ORG, code="voice.minutes.v2"))
    resolved = app.commands.dispatch(
        ResolveQuarantinedEvent(
            **ORG,
            entry_id=entry.id,
            meter_code="voice.minutes.v2",
            account_id=account_id,
            note="meter replaced",
        )
    )
    assert resolved.state is QuarantineState.RESOLVED
    assert resolved.resolution_note == "meter replaced"


def test_a_quarantined_event_can_be_discarded_with_a_recorded_reason(app, stream):
    _seed(app)
    app.commands.dispatch(RetireCommercialMeter(**ORG, code="voice.minutes"))
    stream.offer(_signal(1, 60_000, 2_000.0))
    app.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    entry = app.queries.dispatch(ListQuarantinedEvents(**ORG))[0]
    discarded = app.commands.dispatch(
        DiscardQuarantinedEvent(**ORG, entry_id=entry.id, reason="test traffic")
    )
    assert discarded.state is QuarantineState.DISCARDED
    assert app.queries.dispatch(ListQuarantinedEvents(**ORG, pending_only=True)) == ()


def test_a_correction_through_the_application_appends_never_edits(app, stream):
    account_id = _seed(app)
    stream.offer(_signal(1, 120_000, 2_000.0))
    app.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    original = app.queries.dispatch(
        SearchCommercialUsage(**ORG, query=UsageSearchQuery(account_id=account_id))
    ).usage[0]

    app.commands.dispatch(
        RecordUsageCorrection(
            **ORG, usage_id=original.id, delta_units=-1, reason="duplicate session"
        )
    )
    records = app.queries.dispatch(
        SearchCommercialUsage(**ORG, query=UsageSearchQuery(account_id=account_id))
    )
    assert records.total == 2
    kept = next(u for u in records.usage if u.id == original.id)
    assert kept.units == original.units  # the original is untouched
    assert any(u.corrects_usage_id == original.id for u in records.usage)


def test_inspection_end_to_end(app, stream):
    account_id = _seed(app)
    stream.offer(_signal(1, 185_000, 2_000.0))
    app.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    usage = app.queries.dispatch(
        SearchCommercialUsage(**ORG, query=UsageSearchQuery(account_id=account_id))
    ).usage[0]
    inspection = app.queries.dispatch(InspectCommercialUsage(**ORG, usage_id=usage.id))
    assert "normalized 185000 ms" in " ".join(inspection.explanation)


def test_reconciliation_end_to_end_is_clean(app, stream):
    account_id = _seed(app)
    for i in range(3):
        stream.offer(_signal(i, 60_000, 2_000.0 + i))
    app.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    report = app.commands.dispatch(
        ReconcileUsagePeriod(**ORG, account_id=account_id, period_start=PS, period_end=PE)
    )
    assert report.is_clean
    assert report.duplicate_count == 0
    assert report.events_examined == 3


def test_reconciliation_finds_an_event_the_pipeline_missed(app, stream):
    account_id = _seed(app)
    stream.offer(_signal(1, 60_000, 2_000.0))
    app.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    stream.offer(_signal(2, 60_000, 3_000.0))  # arrives in the log but is never drained
    report = app.queries.dispatch(
        GetUsageReconciliationReport(**ORG, account_id=account_id, period_start=PS, period_end=PE)
    )
    assert not report.is_clean
    assert any(d.kind == "missing_usage" for d in report.discrepancies)


def test_diagnostics_end_to_end(app, stream):
    account_id = _seed(app)
    stream.offer(_signal(1, 60_000, 2_000.0))
    app.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    report = app.queries.dispatch(
        DiagnoseUsagePeriod(**ORG, account_id=account_id, period_start=PS, period_end=PE)
    )
    assert report.is_valid


def test_export_end_to_end_is_deterministic(app, stream):
    account_id = _seed(app)
    for i in range(4):
        stream.offer(_signal(i, 60_000, 2_000.0 + i))
    app.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    first = app.queries.dispatch(
        ExportCommercialUsage(**ORG, account_id=account_id, period_start=PS, period_end=PE)
    )
    second = app.queries.dispatch(
        ExportCommercialUsage(**ORG, account_id=account_id, period_start=PS, period_end=PE)
    )
    assert first == second
    assert first.row_count == 4 and first.total_units == 4


def test_cross_org_isolation(app, stream):
    account_id = _seed(app)
    stream.offer(_signal(1, 60_000, 2_000.0))
    app.commands.dispatch(DrainMetricStream(**ORG, period_start=PS, period_end=PE))
    other = dict(actor_id="usr_2", org_id="org_2")
    assert app.queries.dispatch(ListCommercialMeters(**other)) == ()
    assert app.queries.dispatch(ListQuarantinedEvents(**other)) == ()
    assert (
        app.queries.dispatch(
            GetCommercialUsageSnapshot(
                **other, account_id=account_id, period_start=PS, period_end=PE
            )
        ).record_count
        == 0
    )


def test_every_metering_command_and_query_is_authorized():
    from billing.application import ALL_METERING_COMMANDS, ALL_METERING_QUERIES

    wired = build_persistent_billing_application(
        ids=SequentialIdGenerator(), clock=FixedClock(), authorizer=DenyAllAuthorizer()
    )
    for message in (*ALL_METERING_COMMANDS, *ALL_METERING_QUERIES):
        bus = (
            wired.application.commands
            if message in ALL_METERING_COMMANDS
            else wired.application.queries
        )
        with pytest.raises(BillingAuthorizationError):
            bus.dispatch(message(**ORG))
    wired.database.close()


def test_ingestion_requires_the_usage_ingest_permission_not_billing_manage():
    """The caller is a system actor on a pipeline, not a human changing a commercial term."""
    source = (BILLING / "application" / "metering_engine.py").read_text()
    assert "P.BILLING_USAGE_INGEST" in source


# ── 15. Persistence ─────────────────────────────────────────────────────────────


def test_metering_tables_are_created_by_the_migration_chain():
    from sqlalchemy import inspect

    from billing.persistence.metering_models import METERING_TABLE_NAMES
    from billing.persistence.migrations import MIGRATIONS, upgrade_all

    database = BillingDatabase("sqlite://")
    applied = upgrade_all(database)
    # Extended at M9.12, the same way the entitlement suite's equivalent was extended at M9.11:
    # the metering migration is pinned by its *position and ordering* rather than by being the
    # tail of the chain, so a later milestone appending 0022 does not read as a metering
    # regression. What must hold is that 0021 is applied, and that the chain stays ordered.
    assert "0021" in applied
    assert applied.index("0021") == applied.index("0020") + 1
    assert [m.VERSION for m in MIGRATIONS] == sorted(m.VERSION for m in MIGRATIONS)
    tables = set(inspect(database.engine).get_table_names())
    for name in METERING_TABLE_NAMES:
        assert name in tables
    database.close()


def test_the_metering_migration_alters_nothing_and_is_idempotent():
    from sqlalchemy import inspect

    from billing.persistence.migrations import upgrade_all, v0021_billing_metering

    database = BillingDatabase("sqlite://")
    upgrade_all(database)
    before = {
        t: {c["name"] for c in inspect(database.engine).get_columns(t)}
        for t in inspect(database.engine).get_table_names()
    }
    v0021_billing_metering.upgrade(database)
    v0021_billing_metering.upgrade(database)
    after = {
        t: {c["name"] for c in inspect(database.engine).get_columns(t)}
        for t in inspect(database.engine).get_table_names()
    }
    assert before == after
    # M9.2's usage tables are untouched.
    for legacy in (
        "cp_billing_usage_record",
        "cp_billing_usage_aggregate",
        "cp_billing_usage_quarantine",
    ):
        assert legacy in before
    database.close()


def test_the_exactly_once_constraint_exists_on_the_table():
    from billing.persistence.metering_models import BillingCommercialUsageRow

    constraints = {
        c.name for c in BillingCommercialUsageRow.__table__.constraints if c.name is not None
    }
    assert "uq_cp_billing_commercial_usage_source" in constraints


def test_round_trip_fidelity_for_commercial_usage():
    from billing.persistence.codec import decode, encode

    usage = _decide(
        meter=_meter(normalization=Normalization(7, 3, Rounding.HALF_EVEN)),
        ingest_run_id="irn_9",
        replayed=True,
    ).usage
    assert decode(CommercialUsage, encode(usage)) == usage


def test_round_trip_fidelity_for_a_meter_and_a_period():
    from billing.persistence.codec import decode, encode

    meter = _meter(normalization=Normalization(1, 60_000, Rounding.FLOOR))
    assert decode(CommercialMeter, encode(meter)) == meter
    period = UsagePeriod(
        id="upd_1",
        org_id="org_1",
        account_id="cma_1",
        period=PERIOD,
        state=PeriodState.SEALED,
        seal_digest="abc",
        sealed_at=1.0,
    )
    assert decode(UsagePeriod, encode(period)) == period


# ── 16. HTTP ────────────────────────────────────────────────────────────────────


def _http_seed(client) -> str:
    account = client.post(
        "/v1/billing/billing-accounts",
        headers=_auth(),
        json={"display_name": "Acme", "currency": "INR"},
    ).json()
    client.post(f"/v1/billing/billing-accounts/{account['id']}/activate", headers=_auth())
    client.post(
        f"{PREFIX}/meters",
        headers=_auth(),
        json={
            "code": "voice.minutes",
            "unit": "minute",
            "source": "runtime.session",
            "normalization": {"numerator": 1, "denominator": 60000, "rounding": "half_up"},
            "accepted_event_types": ["metric.session.duration_ms"],
        },
    )
    client.post(f"{PREFIX}/meters/voice.minutes/activate", headers=_auth())
    client.post(
        f"{PREFIX}/periods",
        headers=_auth(),
        json={"account_id": account["id"], "period_start": PS, "period_end": PE},
    )
    return account["id"]


def _post_event(client, event_id="evt_1", ms=60_000, at=2_000.0):
    return client.post(
        f"{PREFIX}/events",
        headers=_auth(),
        json={
            "signal": {
                "event_id": event_id,
                "event_type": "metric.session.duration_ms",
                "source": "runtime.session",
                "observed_at": at,
                "raw_units": ms,
                "raw_unit": "ms",
            },
            "period_start": PS,
            "period_end": PE,
        },
    )


def test_ingestion_over_http_returns_201_then_200(client):
    """201 for a new record, 200 for a replay — a caller can tell the difference without either
    being an error."""
    _http_seed(client)
    first = _post_event(client)
    assert first.status_code == 201, first.text
    assert first.json()["duplicate"] is False
    assert first.json()["units"] == 1

    second = _post_event(client)
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["usage_id"] == ""


def test_a_quarantined_event_over_http_is_202(client):
    _http_seed(client)
    response = client.post(
        f"{PREFIX}/events",
        headers=_auth(),
        json={
            "signal": {
                "event_id": "evt_odd",
                "event_type": "metric.unknown",
                "source": "runtime.session",
                "observed_at": 2_000.0,
                "raw_units": 5,
            },
            "period_start": PS,
            "period_end": PE,
        },
    )
    assert response.status_code == 202
    assert response.json()["quarantined"] is True


def test_an_event_with_no_id_over_http_is_422(client):
    _http_seed(client)
    response = client.post(
        f"{PREFIX}/events",
        headers=_auth(),
        json={
            "signal": {
                "event_id": "",
                "event_type": "metric.session.duration_ms",
                "source": "runtime.session",
                "observed_at": 2_000.0,
                "raw_units": 5,
            },
            "period_start": PS,
            "period_end": PE,
        },
    )
    assert response.status_code == 422


def test_a_second_meter_on_the_same_signal_over_http_is_422(client):
    _http_seed(client)
    response = client.post(
        f"{PREFIX}/meters",
        headers=_auth(),
        json={
            "code": "copy",
            "unit": "minute",
            "source": "runtime.session",
            "accepted_event_types": ["metric.session.duration_ms"],
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["diagnostics"]


def test_the_meter_lifecycle_over_http(client):
    _http_seed(client)
    for path, expected in (
        ("deprecate", "deprecated"),
        ("reinstate", "active"),
        ("retire", "retired"),
    ):
        response = client.post(
            f"{PREFIX}/meters/voice.minutes/{path}", headers=_auth(), json={"reason": ""}
        )
        assert response.status_code == 200, response.text
        assert response.json()["state"] == expected


def test_sealing_and_replay_over_http(client):
    account_id = _http_seed(client)
    _post_event(client, "evt_1", 90_000, 2_000.0)
    _post_event(client, "evt_2", 30_000, 3_000.0)
    body = {"account_id": account_id, "period_start": PS, "period_end": PE}

    sealed = client.post(f"{PREFIX}/periods/seal", headers=_auth(), json=body)
    assert sealed.status_code == 200
    assert sealed.json()["state"] == "sealed"
    assert sealed.json()["seal_digest"]
    assert sealed.json()["allowed_triggers"] == []

    replay = client.post(f"{PREFIX}/replay", headers=_auth(), json=body)
    assert replay.status_code == 200
    assert replay.json()["seal_digest"] == sealed.json()["seal_digest"]
    # No stream is wired on this app, so the replay reads an empty log: it produces nothing and
    # the period's stored content still matches what it sealed. Replay answers "has this period
    # changed?"; noticing that the *log* is missing events is reconciliation's job.
    assert replay.json()["events_replayed"] == 0
    assert replay.json()["usage_produced"] == 0
    assert replay.json()["matches_seal"] is True


def test_resealing_over_http_is_409(client):
    account_id = _http_seed(client)
    _post_event(client)
    body = {"account_id": account_id, "period_start": PS, "period_end": PE}
    client.post(f"{PREFIX}/periods/seal", headers=_auth(), json=body)
    again = client.post(f"{PREFIX}/periods/seal", headers=_auth(), json=body)
    assert again.status_code == 409


def test_there_is_no_way_to_edit_or_delete_usage_over_http(client):
    account_id = _http_seed(client)
    _post_event(client)
    usage_id = client.get(f"{PREFIX}/records", headers=_auth()).json()["usage"][0]["id"]
    for verb in (client.put, client.patch, client.delete):
        assert verb(f"{PREFIX}/records/{usage_id}", headers=_auth()).status_code == 405
    assert client.delete(f"{PREFIX}/periods/{account_id}", headers=_auth()).status_code == 405


def test_the_metering_surface_has_no_delete_at_all(client):
    schema = client.get("/openapi.json").json()
    deletions = [
        path
        for path, operations in schema["paths"].items()
        if path.startswith(f"{PREFIX}/") and "delete" in operations
    ]
    assert deletions == []


def test_corrections_over_http(client):
    _http_seed(client)
    _post_event(client, "evt_1", 120_000, 2_000.0)
    usage_id = client.get(f"{PREFIX}/records", headers=_auth()).json()["usage"][0]["id"]
    response = client.post(
        f"{PREFIX}/records/{usage_id}/corrections",
        headers=_auth(),
        json={"delta_units": -1, "reason": "duplicate session"},
    )
    assert response.status_code == 201
    assert response.json()["status"] == "adjustment"
    assert response.json()["corrects_usage_id"] == usage_id


def test_a_correction_with_no_reason_over_http_is_422(client):
    _http_seed(client)
    _post_event(client)
    usage_id = client.get(f"{PREFIX}/records", headers=_auth()).json()["usage"][0]["id"]
    response = client.post(
        f"{PREFIX}/records/{usage_id}/corrections", headers=_auth(), json={"delta_units": -1}
    )
    assert response.status_code == 422


def test_snapshot_and_aggregates_over_http(client):
    account_id = _http_seed(client)
    _post_event(client, "evt_1", 90_000, 2_000.0)
    _post_event(client, "evt_2", 90_000, 3_000.0)
    rebuilt = client.post(
        f"{PREFIX}/aggregates/rebuild",
        headers=_auth(),
        json={"account_id": account_id, "period_start": PS, "period_end": PE},
    )
    assert rebuilt.status_code == 200
    body = rebuilt.json()
    assert body["record_count"] == 2
    assert body["aggregates"][0]["units"] == 4  # 2 + 2 minutes


def test_provenance_over_http(client):
    _http_seed(client)
    _post_event(client, "evt_1", 185_000, 2_000.0)
    usage_id = client.get(f"{PREFIX}/records", headers=_auth()).json()["usage"][0]["id"]
    detail = client.get(f"{PREFIX}/records/{usage_id}", headers=_auth()).json()
    assert "normalized 185000 ms" in " ".join(detail["explanation"])
    assert detail["usage"]["provenance"]["meter_pin"] == "voice.minutes@1"
    assert detail["usage"]["digest"]


def test_quarantine_over_http(client):
    _http_seed(client)
    client.post(
        f"{PREFIX}/events",
        headers=_auth(),
        json={
            "signal": {
                "event_id": "evt_bad",
                "event_type": "metric.unknown",
                "source": "runtime.session",
                "observed_at": 2_000.0,
                "raw_units": 5,
            },
            "period_start": PS,
            "period_end": PE,
        },
    )
    entries = client.get(f"{PREFIX}/quarantine", headers=_auth()).json()
    assert len(entries) == 1
    discarded = client.post(
        f"{PREFIX}/quarantine/{entries[0]['id']}/discard",
        headers=_auth(),
        json={"reason": "unknown meter"},
    )
    assert discarded.status_code == 200
    assert discarded.json()["state"] == "discarded"
    assert client.get(f"{PREFIX}/quarantine", headers=_auth()).json() == []


def test_export_over_http(client):
    account_id = _http_seed(client)
    _post_event(client, "evt_1", 60_000, 2_000.0)
    export = client.get(
        f"{PREFIX}/export/{account_id}?period_start={PS}&period_end={PE}", headers=_auth()
    ).json()
    assert export["row_count"] == 1
    assert export["digest"]
    assert export["lines"][0]["meter_pin"] == "voice.minutes@1"


def test_timeline_over_http_shows_the_duplicate(client):
    _http_seed(client)
    _post_event(client)
    _post_event(client)
    timeline = client.get(f"{PREFIX}/timeline", headers=_auth()).json()
    actions = {a["action"] for a in timeline}
    assert "metering.usage_recorded" in actions
    assert "metering.usage_duplicate_ignored" in actions


def test_no_amount_appears_anywhere_on_the_wire(client):
    """A quantity is a count in a meter's unit; what it costs is rating's."""
    account_id = _http_seed(client)
    _post_event(client)
    for path in (
        f"{PREFIX}/meters",
        f"{PREFIX}/records",
        f"{PREFIX}/snapshot/{account_id}?period_start={PS}&period_end={PE}",
        f"{PREFIX}/export/{account_id}?period_start={PS}&period_end={PE}",
    ):
        body = client.get(path, headers=_auth()).text
        for forbidden in ('"minor"', '"currency"', '"amount"', '"price"'):
            assert forbidden not in body, (path, forbidden)


def test_metering_routes_require_identity_and_an_org(client):
    assert client.get(f"{PREFIX}/meters").status_code == 401
    assert client.get(f"{PREFIX}/meters", headers=_auth(org_id="")).status_code == 401


def test_metering_authorization_denial_is_403():
    with _client(authorizer=DenyAllAuthorizer()) as denied:
        assert denied.get(f"{PREFIX}/meters", headers=_auth()).status_code == 403


def test_the_organization_always_comes_from_the_token(client):
    _http_seed(client)
    client.post(
        f"{PREFIX}/meters",
        headers=_auth(),
        json={
            "code": "smuggled",
            "unit": "u",
            "source": "tool",
            "org_id": "org_evil",
            "organization": "org_evil",
        },
    )
    assert "smuggled" in {m["code"] for m in client.get(f"{PREFIX}/meters", headers=_auth()).json()}
    other = _auth(user_id="usr_2", org_id="org_evil")
    assert client.get(f"{PREFIX}/meters", headers=other).json() == []


def test_the_scope_organization_cannot_be_supplied_by_the_body(client):
    """`ScopeBody` has no org field at all — the organization is taken from the token."""
    from billing.customer.metering_schemas import ScopeBody

    assert "org_id" not in ScopeBody.model_fields


def test_cross_org_isolation_over_http(client):
    account_id = _http_seed(client)
    _post_event(client)
    other = _auth(user_id="usr_2", org_id="org_2")
    assert client.get(f"{PREFIX}/records", headers=other).json()["total"] == 0
    assert client.get(f"{PREFIX}/periods/{account_id}", headers=other).json() == []


def test_metering_openapi_is_tagged_and_documents_errors(client):
    schema = client.get("/openapi.json").json()
    for path, operations in schema["paths"].items():
        if not path.startswith(f"{PREFIX}/"):
            continue
        for operation in operations.values():
            assert operation["tags"] == ["metering"]
            assert "422" in operation["responses"] or "401" in operation["responses"]


def test_the_m9_5_metering_surface_is_untouched(client):
    schema = client.get("/openapi.json").json()
    for path in (
        "/v1/billing/metering/usage",
        "/v1/billing/metering/quarantine",
        "/v1/billing/metering/usage/{account_id}/{meter_code}",
    ):
        assert path in schema["paths"], path


def test_no_metering_path_collides(client):
    paths = list(client.get("/openapi.json").json()["paths"])
    assert len(paths) == len(set(paths))


# ── 17. Studio ──────────────────────────────────────────────────────────────────

STUDIO = BILLING / "customer" / "studio" / "metering.html"


def test_metering_studio_is_served(client):
    response = client.get("/studio/metering.html")
    assert response.status_code == 200
    assert "Metering" in response.text


def test_metering_studio_is_self_contained():
    source = STUDIO.read_text()
    for forbidden in ("https://", "http://", "cdn.", "<script src", "@import"):
        assert forbidden not in source, forbidden


def test_metering_studio_has_a_screen_for_every_required_area():
    source = STUDIO.read_text()
    for screen in (
        "Meters",
        "Ingest",
        "Usage Records",
        "Snapshot",
        "Periods",
        "Quarantine",
        "Replay",
        "Reconciliation",
        "Diagnostics",
        "Export",
        "Timeline",
    ):
        assert screen in source, screen


def test_metering_studio_owns_no_state_no_rule_and_no_money():
    source = STUDIO.read_text()
    for forbidden in (
        "localStorage",
        "sessionStorage",
        "function normalize",
        "function aggregate",
        "sha256",
        "toFixed(2)",
        '"minor"',
        "currency",
    ):
        assert forbidden not in source, forbidden


def test_metering_studio_takes_its_lifecycle_buttons_from_the_server():
    assert "allowed_triggers" in STUDIO.read_text()


# ── 18. Architecture compliance ─────────────────────────────────────────────────

METERING_MODULES = (
    BILLING / "foundation" / "commercial_metering.py",
    BILLING / "foundation" / "metering_rules.py",
    BILLING / "foundation" / "metering_factories.py",
    BILLING / "application" / "metering_engine.py",
    BILLING / "persistence" / "metering_models.py",
    BILLING / "persistence" / "metering_repositories.py",
    BILLING / "customer" / "metering_schemas.py",
    BILLING / "customer" / "routers" / "metering_engine.py",
)


def _code_only(path: Path) -> str:
    """Source with docstrings stripped. These scans are about what the domain *is*, and prose
    explaining that Billing never measures must not read as measuring."""
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


def test_billing_never_measures():
    """No clock read, no socket, no sampler, no timer anywhere in the domain."""
    for path in METERING_MODULES:
        code = _code_only(path)
        for forbidden in (
            "time.time",
            "time.monotonic",
            "perf_counter",
            "socket",
            "psutil",
            "resource.getrusage",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_billing_never_becomes_a_metric_producer():
    """It emits `billing.*` facts about commercial usage and never `metric.*` (ADR-0023 §6)."""
    for path in METERING_MODULES:
        code = _code_only(path)
        assert 'emit("metric' not in code
        assert '"metric.' not in code or path.name in {
            "metering_schemas.py",
            "metering_engine.py",
        }, path.name
    for path in METERING_MODULES:
        code = _code_only(path)
        for forbidden in ("MetricEvent", "metric_bus", "publish_metric", "EventBus"):
            assert forbidden not in code, (path.name, forbidden)


def test_billing_never_polls_runtime_or_reaches_into_runtime_state():
    """There is no outbound call of any kind in this plane."""
    for path in METERING_MODULES:
        code = _code_only(path)
        for forbidden in ("httpx", "requests.", "urllib", "websocket", "grpc", "subprocess"):
            assert forbidden not in code, (path.name, forbidden)


def test_metering_imports_no_frozen_epic():
    forbidden_roots = {"api", "runtime", "deployment", "knowledge", "observability", "product"}
    for path in METERING_MODULES:
        tree = ast.parse(path.read_text())
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
        assert not (roots & forbidden_roots), (path.name, roots & forbidden_roots)


def test_the_metric_stream_is_the_only_inbound_path():
    """One seam, declared inside this plane and implemented outside it."""
    factories = _code_only(BILLING / "foundation" / "metering_factories.py")
    assert "class MetricStreamPort(ABC)" in factories
    application = _code_only(BILLING / "application" / "metering_engine.py")
    assert "self._stream.drain(" in application
    assert "self._stream.replay(" in application


def test_metering_owns_no_money_and_computes_no_charge():
    for path in METERING_MODULES:
        code = _code_only(path)
        for forbidden in ("Money", "minor", ".times(", ".allocate(", ".scaled(", "PriceTier"):
            assert forbidden not in code, (path.name, forbidden)


def test_metering_owns_no_ledger_invoice_payment_or_entitlement():
    for path in METERING_MODULES:
        code = _code_only(path)
        for forbidden in (
            "LedgerEntry",
            "Invoice",
            "Payment",
            "Refund",
            "EntitlementSnapshot",
            "CommercialGrant",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_the_metering_foundation_stays_pure():
    for path in (
        BILLING / "foundation" / "commercial_metering.py",
        BILLING / "foundation" / "metering_rules.py",
        BILLING / "foundation" / "metering_factories.py",
    ):
        source = path.read_text().lower()
        for forbidden in ("sqlmodel", "sqlalchemy", "fastapi", "pydantic", "session.", "select("):
            assert forbidden not in source, (path.name, forbidden)


def test_the_metering_rules_read_no_clock():
    """Every temporal decision takes `at` as an argument — what makes replaying a period in 2027
    produce what it produced in 2026."""
    source = (BILLING / "foundation" / "metering_rules.py").read_text()
    for forbidden in ("time.time", "datetime.now", "self._clock", "SystemClock"):
        assert forbidden not in source, forbidden


def test_metering_layers_import_only_downward():
    def imports(path: Path) -> set[str]:
        tree = ast.parse(path.read_text())
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
        return found

    # `hashlib` is admitted deliberately: derived usage ids and seal digests are the mechanism
    # that makes replay verifiable, and they must be computed in the Foundation.
    allowed = {
        "core",
        "billing",
        "__future__",
        "abc",
        "dataclasses",
        "enum",
        "typing",
        "collections",
        "hashlib",
    }
    for path in (
        BILLING / "foundation" / "commercial_metering.py",
        BILLING / "foundation" / "metering_rules.py",
        BILLING / "foundation" / "metering_factories.py",
    ):
        assert imports(path) <= allowed, (path.name, imports(path))

    app_imports = imports(BILLING / "application" / "metering_engine.py")
    assert "sqlmodel" not in app_imports and "fastapi" not in app_imports


def test_metering_use_cases_contain_no_rule():
    source = _code_only(BILLING / "application" / "metering_engine.py")
    for forbidden in (
        "def validate_",
        "def normalize",
        "METER_LIFECYCLE",
        "USAGE_PERIOD_LIFECYCLE",
        "def disposition",
        "def seal_digest",
        "hashlib",
        "def fold",
    ):
        assert forbidden not in source, forbidden


def test_metering_controllers_stay_transport_only():
    """Routes are identified structurally by their `@router.<verb>` decorator.

    Sharpened at M9.11: a route may delegate to a module-level private helper, so the check
    follows one level of delegation and requires the *helper* to dispatch. Demanding an inline
    dispatch would have pushed duplicated serialization into every route to satisfy a test."""
    source = (BILLING / "customer" / "routers" / "metering_engine.py").read_text()
    tree = ast.parse(source)
    helpers = {
        n.name: ast.unparse(n)
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name.startswith("_")
    }
    routes = 0
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        decorated = any(
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and isinstance(d.func.value, ast.Name)
            and d.func.value.id == "router"
            for d in node.decorator_list
        )
        if not decorated:
            continue
        routes += 1
        if (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        ):
            node.body = node.body[1:]
        body = ast.unparse(node)
        expanded = body + "".join(helper for name, helper in helpers.items() if f"{name}(" in body)
        assert "commands.dispatch" in expanded or "queries.dispatch" in expanded, node.name
        for forbidden in ("repositories", "session", ".commit()", "uow.", "validate", "normalize"):
            assert forbidden not in body, (node.name, forbidden)
    assert routes >= 24


def test_repositories_decide_nothing():
    source = _code_only(BILLING / "persistence" / "metering_repositories.py")
    for forbidden in (
        "UsageNormalizer",
        "LateArrivalPolicy",
        "UsageAggregator",
        "UsageSealingService",
        "UsageIngestionService",
        "fold(",
        ".disposition(",  # the decision call, not the projected column of the same name
        ".normalize(",
        ".seal_digest(",
    ):
        assert forbidden not in source, forbidden


def test_m9_2_metering_vocabulary_is_reused_not_duplicated():
    """Two answers to "how does this aggregation reduce?" is the drift §A2.5 prevents."""
    domain = (BILLING / "foundation" / "commercial_metering.py").read_text()
    assert "from .metering import UsageStatus, fold" in domain
    assert "class UsageStatus" not in domain
    rules = (BILLING / "foundation" / "metering_rules.py").read_text()
    assert "from .metering import UsageStatus, fold" in rules
    assert "def fold(" not in rules


def test_command_and_query_coverage_is_complete():
    from billing.application import ALL_METERING_COMMANDS, ALL_METERING_QUERIES

    wired = build_persistent_billing_application(
        ids=SequentialIdGenerator(), clock=FixedClock(), authorizer=AllowAllAuthorizer()
    )
    for message in (*ALL_METERING_COMMANDS, *ALL_METERING_QUERIES):
        bus = (
            wired.application.commands
            if message in ALL_METERING_COMMANDS
            else wired.application.queries
        )
        assert message in bus._handlers, message.__name__  # noqa: SLF001
    assert len(ALL_METERING_COMMANDS) == 17
    assert len(ALL_METERING_QUERIES) == 12
    wired.database.close()


def test_the_metering_engine_is_additive_and_optional():
    from billing.application import BillingRepositories
    from billing.application.context import BillingContext

    fields = {f.name for f in BillingRepositories.__dataclass_fields__.values()}
    assert "metering_engine" in fields
    assert BillingRepositories.__dataclass_fields__["metering_engine"].default is None
    assert BillingContext.__dataclass_fields__["metering_engine"].default is None


def test_frozen_lower_layers_kept_their_surfaces():
    from billing import application, foundation, persistence

    for name in (
        "BillingFactory",
        "Money",
        "Product",
        "CatalogVersion",
        "PricingBook",
        "CommercialAccount",
        "CommercialSubscription",
        "EntitlementSnapshot",
        "UsageRecord",
        "UsageAggregate",
        "UsageQuarantine",
        "MeteringService",
        "fold",
    ):
        assert name in foundation.__all__
    for name in (
        "BillingApplication",
        "RecordUsage",
        "QuarantineUsage",
        "GetUsageSummary",
        "GrantCommercialEntitlement",
    ):
        assert name in application.__all__
    for name in ("BillingDatabase", "build_persistent_billing_application"):
        assert name in persistence.__all__
    assert len(foundation.__all__) > 429  # M9.10 left it at 429; growth only


def test_the_earlier_domain_boundaries_still_hold():
    for path in (BILLING / "foundation" / "products.py", BILLING / "application" / "catalog.py"):
        assert "Money" not in _code_only(path), path.name
    for path in (
        BILLING / "foundation" / "commercial_subscriptions.py",
        BILLING / "application" / "subscriptions_engine.py",
    ):
        for forbidden in ("Money", "PriceTier"):
            assert forbidden not in _code_only(path), (path.name, forbidden)
    for path in (
        BILLING / "foundation" / "commercial_entitlements.py",
        BILLING / "foundation" / "entitlement_rules.py",
    ):
        for forbidden in ("principal", "Permission", "has_role"):
            assert forbidden not in _code_only(path), (path.name, forbidden)


def test_the_billing_package_layout_grew_by_addition():
    import pkgutil

    modules = {m.name for m in pkgutil.iter_modules([str(BILLING / "foundation")])}
    for name in ("commercial_metering", "metering_rules", "metering_factories"):
        assert name in modules, name
    assert "metering" in modules  # the M9.2 module is still there, unreshaped


def test_billing_is_still_absent_from_the_runtime_composition_root():
    assert "billing" not in (PLATFORM / "bootstrap.py").read_text().lower()
