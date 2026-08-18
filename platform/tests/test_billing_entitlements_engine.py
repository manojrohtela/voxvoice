"""M9.10 — Entitlement Engine: tests.

Four layers, one domain: pure Foundation rules, then the Application over a real database, then
the HTTP surface, then architecture compliance. The heaviest coverage sits on the load-bearing
Epic 9 invariant — **Billing decides, Billing publishes immutable snapshots, the owning platform
enforces locally, and Billing never performs authorization** — and on the four properties an
entitlement system cannot be retrofitted with: deterministic precedence, effective dating that
never mutates state, versions that roll forward rather than being rewritten, and a gate
classification on every capability so an enforcing plane knows what to do during an outage.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.customer.jwt import encode as epic8_encode
from billing.application import (
    ActivateBillingAccount,
    ActivateEntitlementGrant,
    AdjustEntitlementGrant,
    AllowAllAuthorizer,
    ArchiveEntitlementGrant,
    BillingAuthorizationError,
    CancelScheduledEntitlementChange,
    CreateBillingAccount,
    CreateEntitlementBundle,
    DefineEntitlement,
    DenyAllAuthorizer,
    DiagnoseEntitlements,
    EntitlementTimeline,
    EntitlementVersionHistory,
    ExpireEntitlementGrant,
    GetCommercialEntitlementSnapshot,
    GetEntitlementProfile,
    GetEntitlementStaleness,
    GrantCommercialEntitlement,
    GrantEntitlementBundleToAccount,
    InspectEntitlement,
    ListEntitlementBundles,
    ListEntitlementDefinitions,
    ListScheduledEntitlementChanges,
    OpenEntitlementProfile,
    PreviewEntitlementSnapshot,
    PublishCommercialEntitlementSnapshot,
    ResumeEntitlementGrant,
    RetireEntitlementDefinition,
    RevokeCommercialEntitlement,
    RollbackEntitlementVersion,
    ScheduleEntitlementChange,
    SearchEntitlementGrants,
    SuspendEntitlementGrant,
    UpdateEntitlementBundle,
    UpdateEntitlementDefinition,
)
from billing.customer import BillingApiSettings, BillingContainer, create_billing_app
from billing.foundation import (
    CLOSED_GRANT_STATES,
    DEFAULT_MAX_STALENESS_SECONDS,
    GRANT_LIFECYCLE,
    GRANT_PRECEDENCE,
    LIVE_GRANT_STATES,
    MAX_BUNDLE_DEPTH,
    MAX_PENDING_ENTITLEMENT_CHANGES,
    MAX_PRECEDENCE,
    MIN_PRECEDENCE,
    STALE_DIRECTIVES,
    BillingConflict,
    BillingValidationError,
    BundleEntry,
    CommercialGrant,
    DefaultIdGenerator,
    Entitlement,
    EntitlementAssembler,
    EntitlementBundle,
    EntitlementBundleExpander,
    EntitlementChangeKind,
    EntitlementDefinition,
    EntitlementFactory,
    EntitlementInspector,
    EntitlementPrecedenceResolver,
    EntitlementProfile,
    EntitlementScheduleService,
    EntitlementSearchQuery,
    EntitlementSearchService,
    EntitlementSet,
    EntitlementSnapshot,
    EntitlementStalenessService,
    EntitlementValidator,
    EntitlementValue,
    EntitlementVersion,
    EntitlementWindow,
    FeatureKind,
    FixedClock,
    GateClassification,
    GrantLifecycleService,
    GrantSource,
    GrantState,
    GrantTrigger,
    ImmutabilityViolation,
    InvalidBillingTransition,
    RecordingEventEmitter,
    SequentialIdGenerator,
    StaleDirective,
)
from billing.persistence import BillingDatabase, build_persistent_billing_application

PLATFORM = Path(__file__).resolve().parents[1]
BILLING = PLATFORM / "billing"
PREFIX = "/v1/billing/capabilities"
SECRET = "test-billing-secret"
ORG = dict(actor_id="usr_1", org_id="org_1")


# ── fixtures ────────────────────────────────────────────────────────────────────


def _factory() -> tuple[EntitlementFactory, RecordingEventEmitter]:
    emitter = RecordingEventEmitter()
    return (
        EntitlementFactory(ids=SequentialIdGenerator(), clock=FixedClock(1_000.0), emitter=emitter),
        emitter,
    )


def _definition(code="sso", **kwargs) -> EntitlementDefinition:
    return EntitlementDefinition(
        id="edf_" + code,
        org_id="org_1",
        code=code,
        kind=kwargs.pop("kind", FeatureKind.FLAG),
        classification=kwargs.pop("classification", GateClassification.FAIL_OPEN),
        **kwargs,
    )


def _grant(
    code="sso",
    *,
    id="egr_1",
    source=GrantSource.PLAN,
    state=GrantState.ACTIVE,
    value=None,
    window=None,
    **kwargs,
) -> CommercialGrant:
    return CommercialGrant(
        id=id,
        org_id="org_1",
        account_id="cma_1",
        entitlement_code=code,
        value=value if value is not None else EntitlementValue(),
        source=source,
        state=state,
        window=window if window is not None else EntitlementWindow(),
        **kwargs,
    )


def _profile(*grants, **kwargs) -> EntitlementProfile:
    return EntitlementProfile(
        id="epf_1", org_id="org_1", account_id="cma_1", grants=tuple(grants), **kwargs
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


# ── 1. Foundation: the grant lifecycle ──────────────────────────────────────────


def test_every_grant_starts_as_a_draft():
    factory, _ = _factory()
    profile = factory.open_profile(org_id="org_1", account_id="cma_1")
    _, grant = factory.grant(profile, entitlement_code="sso")
    assert grant.state is GrantState.DRAFT
    assert not grant.is_live


def test_a_draft_grant_confers_nothing():
    """Approval is what makes a capability real. A draft that already conferred would make the
    approval step decorative."""
    resolver = EntitlementPrecedenceResolver()
    resolved = resolver.resolve([_grant(state=GrantState.DRAFT)], [_definition()], at=1_000.0)
    assert resolved == ()


def test_the_grant_lifecycle_has_no_deletion_state_at_all():
    """Stronger than the catalog, pricing, and account lifecycles — and deliberately so. What a
    customer was given, and when it was taken away, is evidence."""
    reachable = {s.value for s in GrantState}
    assert not any("delet" in s for s in reachable)
    for state in GrantState:
        for trigger in GRANT_LIFECYCLE.triggers_from(state):
            edge = GRANT_LIFECYCLE.get(state, trigger)
            assert "delet" not in edge.target.value


def test_a_revoked_grant_can_never_become_active_again():
    """Re-granting is a *new* grant. Collapsing the two makes "was this ever revoked?"
    unanswerable — and that is precisely the question a disputed entitlement turns on."""
    lifecycle = GrantLifecycleService()
    assert not lifecycle.can(GrantState.REVOKED, GrantTrigger.ACTIVATE)
    assert not lifecycle.can(GrantState.REVOKED, GrantTrigger.RESUME)
    assert lifecycle.can(GrantState.REVOKED, GrantTrigger.ARCHIVE)


def test_archived_is_terminal():
    assert GrantLifecycleService().allowed(GrantState.ARCHIVED) == ()


def test_a_draft_grant_cannot_be_suspended():
    """Suspending something that was never active is a distinction without a difference, and
    offering it would produce two ways to say "not yet"."""
    assert not GrantLifecycleService().can(GrantState.DRAFT, GrantTrigger.SUSPEND)


def test_the_lifecycle_contains_no_self_loops():
    """Every edge must change the state. A self-loop would be a computation hiding inside a
    state machine."""
    for state in GrantState:
        for trigger in GRANT_LIFECYCLE.triggers_from(state):
            assert GRANT_LIFECYCLE.get(state, trigger).target is not state


def test_an_illegal_transition_is_refused_not_coerced():
    lifecycle = GrantLifecycleService()
    with pytest.raises(InvalidBillingTransition) as excinfo:
        lifecycle.next_state(GrantState.DRAFT, GrantTrigger.RESUME)
    assert "legal triggers" in str(excinfo.value)


def test_activation_anchors_an_unbounded_window_to_now():
    """A grant that quietly applied to last month is a different promise from the one made."""
    moved = GrantLifecycleService().apply(
        _grant(state=GrantState.DRAFT), GrantTrigger.ACTIVATE, at=500.0
    )
    assert moved.window.starts_at == 500.0
    assert moved.window.is_open_ended


def test_activation_respects_an_explicit_start():
    grant = _grant(state=GrantState.DRAFT, window=EntitlementWindow(starts_at=100.0))
    moved = GrantLifecycleService().apply(grant, GrantTrigger.ACTIVATE, at=500.0)
    assert moved.window.starts_at == 100.0


def test_revocation_closes_the_window_as_well_as_the_state():
    """Otherwise a snapshot composed for a later instant would re-admit the grant through
    effective dating — the state says gone, the window says present."""
    moved = GrantLifecycleService().apply(_grant(), GrantTrigger.REVOKE, at=900.0, reason="fraud")
    assert moved.state is GrantState.REVOKED
    assert moved.window.ends_at == 900.0
    assert moved.revoked_at == 900.0
    assert moved.revoked_reason == "fraud"
    assert not moved.is_effective_at(1_000.0)


def test_revocation_does_not_extend_an_earlier_window_end():
    grant = _grant(window=EntitlementWindow(ends_at=100.0))
    moved = GrantLifecycleService().apply(grant, GrantTrigger.REVOKE, at=900.0)
    assert moved.window.ends_at == 100.0


def test_suspension_is_reversible_and_confers_nothing_meanwhile():
    lifecycle = GrantLifecycleService()
    suspended = lifecycle.apply(_grant(), GrantTrigger.SUSPEND, at=10.0)
    assert not suspended.is_effective_at(11.0)
    resumed = lifecycle.apply(suspended, GrantTrigger.RESUME, at=20.0)
    assert resumed.is_effective_at(21.0)
    assert resumed.suspended_at is None


def test_a_closed_grant_refuses_edits():
    lifecycle = GrantLifecycleService()
    revoked = lifecycle.apply(_grant(), GrantTrigger.REVOKE, at=1.0)
    with pytest.raises(BillingConflict) as excinfo:
        lifecycle.guard_editable(revoked)
    assert "retained record" in str(excinfo.value)


def test_live_and_closed_state_sets_partition_correctly():
    assert LIVE_GRANT_STATES == frozenset({GrantState.ACTIVE})
    assert GrantState.SUSPENDED not in LIVE_GRANT_STATES
    assert GrantState.SUSPENDED not in CLOSED_GRANT_STATES
    assert LIVE_GRANT_STATES.isdisjoint(CLOSED_GRANT_STATES)


# ── 2. Foundation: effective dating ─────────────────────────────────────────────


def test_windows_are_half_open():
    """With closed intervals, one grant's end and the next one's start are both in force for an
    instant, and a snapshot composed exactly then would be ambiguous."""
    window = EntitlementWindow(starts_at=100.0, ends_at=200.0)
    assert window.covers(100.0)
    assert window.covers(199.999)
    assert not window.covers(200.0)
    assert not window.covers(99.999)


def test_an_unbounded_window_covers_everything():
    window = EntitlementWindow()
    assert window.covers(0.0) and window.covers(1e12)
    assert window.is_open_ended


def test_an_inverted_window_is_invalid():
    assert not EntitlementWindow(starts_at=200.0, ends_at=100.0).is_valid
    findings = EntitlementValidator().validate_window(
        EntitlementWindow(starts_at=200.0, ends_at=100.0)
    )
    assert findings.has_errors


def test_adjacent_windows_do_not_overlap():
    first = EntitlementWindow(starts_at=0.0, ends_at=100.0)
    second = EntitlementWindow(starts_at=100.0, ends_at=200.0)
    assert not first.overlaps(second)
    assert first.overlaps(EntitlementWindow(starts_at=99.0, ends_at=200.0))


def test_effective_dating_never_mutates_state():
    """The clock closes a window; only an administrative act closes a state. Keeping them
    separate is what lets this domain contain no scheduler."""
    grant = _grant(window=EntitlementWindow(ends_at=100.0))
    assert not grant.is_effective_at(200.0)
    assert grant.state is GrantState.ACTIVE  # unchanged by the passage of time
    assert grant.is_live


def test_a_future_grant_is_not_yet_effective():
    grant = _grant(window=EntitlementWindow(starts_at=500.0))
    assert not grant.is_effective_at(100.0)
    assert grant.is_effective_at(500.0)


# ── 3. Foundation: values ───────────────────────────────────────────────────────


def test_unlimited_and_none_are_opposite_answers():
    """A nullable integer that collapsed them would make an enterprise contract
    indistinguishable from a suspension."""
    unlimited = EntitlementValue(enabled=True, allowance=None)
    none = EntitlementValue(enabled=True, allowance=0)
    assert unlimited.is_unlimited and not unlimited.is_denial
    assert none.is_denial and not none.is_unlimited
    assert unlimited.generosity > none.generosity


def test_a_disabled_value_is_a_denial():
    assert EntitlementValue(enabled=False).is_denial
    assert EntitlementValue(enabled=False).generosity == -1


def test_a_negative_allowance_is_rejected():
    findings = EntitlementValidator().validate_value(EntitlementValue(allowance=-1))
    assert findings.has_errors
    assert any(f.code.value == "invalid_allowance" for f in findings.errors)


def test_overage_on_a_disabled_capability_is_rejected():
    findings = EntitlementValidator().validate_value(
        EntitlementValue(enabled=False, allows_overage=True)
    )
    assert findings.has_errors


def test_overage_on_an_unlimited_capability_is_a_warning_not_an_error():
    findings = EntitlementValidator().validate_value(
        EntitlementValue(enabled=True, allowance=None, allows_overage=True)
    )
    assert not findings.has_errors
    assert findings.warnings


# ── 4. Foundation: definitions and gates ────────────────────────────────────────


def test_every_definition_carries_a_classification():
    """ADR-0023 E3: a gate without one is an incomplete design."""
    for definition in (
        _definition(classification=GateClassification.FAIL_OPEN),
        _definition(classification=GateClassification.FAIL_CLOSED),
        _definition(classification=GateClassification.DEGRADE),
    ):
        assert definition.classification in GateClassification


def test_a_quota_without_a_meter_is_rejected():
    """A quota is an allowance of something measured. Without a meter the gate is undecidable
    rather than generous."""
    findings = EntitlementValidator().validate_definition(
        _definition("minutes", kind=FeatureKind.QUOTA)
    )
    assert findings.has_errors
    assert any(f.code.value == "missing_quota_meter" for f in findings.errors)


def test_a_quota_with_a_meter_validates():
    findings = EntitlementValidator().validate_definition(
        _definition("minutes", kind=FeatureKind.QUOTA, meter_code="m1")
    )
    assert not findings.has_errors


def test_a_zero_staleness_budget_is_rejected():
    """Zero would make every snapshot stale on arrival."""
    findings = EntitlementValidator().validate_definition(_definition(max_staleness_seconds=0.0))
    assert findings.has_errors


def test_duplicate_definition_codes_are_rejected():
    findings = EntitlementValidator().validate_definitions([_definition("sso"), _definition("sso")])
    assert any(f.code.value == "duplicate_entitlement_code" for f in findings.errors)


def test_a_definition_projects_onto_the_m9_2_gate():
    """The published contract stays the shape enforcing planes already read."""
    gate = _definition("sso", meter_code="m1", description="d").as_gate()
    assert (gate.code, gate.classification, gate.meter_code) == (
        "sso",
        GateClassification.FAIL_OPEN,
        "m1",
    )


def test_retiring_a_definition_does_not_revoke_anything():
    """Withdrawing a capability from customers who hold it is a revocation; conflating the two
    would let a registry cleanup cancel a contract."""
    factory, _ = _factory()
    definition = factory.define_entitlement(org_id="org_1", code="sso")
    retired = factory.retire_definition(definition)
    assert retired.is_retired
    resolved = EntitlementPrecedenceResolver().resolve([_grant()], [retired], at=1_000.0)
    assert len(resolved) == 1 and resolved[0].enabled


# ── 5. Foundation: precedence resolution ────────────────────────────────────────


def test_contract_outranks_plan():
    resolved = EntitlementPrecedenceResolver().resolve(
        [
            _grant(id="egr_a", source=GrantSource.PLAN, value=EntitlementValue(allowance=100)),
            _grant(id="egr_b", source=GrantSource.CONTRACT, value=EntitlementValue(allowance=5)),
        ],
        [_definition()],
        at=1_000.0,
    )
    assert len(resolved) == 1
    assert resolved[0].source is GrantSource.CONTRACT
    assert resolved[0].allowance == 5  # precedence beats generosity


def test_partner_bounds_everything_beneath_it():
    assert GRANT_PRECEDENCE[GrantSource.PARTNER] == max(GRANT_PRECEDENCE.values())


def test_generosity_breaks_a_same_source_tie():
    resolved = EntitlementPrecedenceResolver().resolve(
        [
            _grant(id="egr_a", value=EntitlementValue(allowance=10)),
            _grant(id="egr_b", value=EntitlementValue(allowance=50)),
        ],
        [_definition()],
        at=1_000.0,
    )
    assert resolved[0].allowance == 50


def test_unlimited_is_the_most_generous():
    resolved = EntitlementPrecedenceResolver().resolve(
        [
            _grant(id="egr_a", value=EntitlementValue(allowance=1_000_000)),
            _grant(id="egr_b", value=EntitlementValue(allowance=None)),
        ],
        [_definition()],
        at=1_000.0,
    )
    assert resolved[0].is_unlimited


def test_a_denial_loses_to_any_grant_at_the_same_precedence():
    resolved = EntitlementPrecedenceResolver().resolve(
        [
            _grant(id="egr_a", value=EntitlementValue(enabled=False)),
            _grant(id="egr_b", value=EntitlementValue(allowance=1)),
        ],
        [_definition()],
        at=1_000.0,
    )
    assert resolved[0].enabled


def test_grant_id_is_the_final_deterministic_tie_break():
    grants = [
        _grant(id="egr_b", value=EntitlementValue(allowance=5)),
        _grant(id="egr_a", value=EntitlementValue(allowance=5)),
    ]
    resolver = EntitlementPrecedenceResolver()
    first = resolver.resolve(grants, [_definition()], at=1_000.0)
    second = resolver.resolve(list(reversed(grants)), [_definition()], at=1_000.0)
    assert first == second
    assert first[0].grant_id == "egr_a"


def test_resolution_never_depends_on_insertion_order():
    """A resolution that depended on the order rows came back in would be reproducible only by
    accident."""
    grants = [
        _grant(id=f"egr_{i}", source=s, value=EntitlementValue(allowance=i))
        for i, s in enumerate(
            [GrantSource.PLAN, GrantSource.CONTRACT, GrantSource.PROMOTION, GrantSource.PARTNER]
        )
    ]
    resolver = EntitlementPrecedenceResolver()
    baseline = resolver.resolve(grants, [_definition()], at=1_000.0)
    for shift in range(len(grants)):
        rotated = grants[shift:] + grants[:shift]
        assert resolver.resolve(rotated, [_definition()], at=1_000.0) == baseline


def test_a_precedence_override_wins_where_declared_precedence_cannot():
    resolved = EntitlementPrecedenceResolver().resolve(
        [
            _grant(id="egr_a", source=GrantSource.PARTNER, value=EntitlementValue(allowance=1)),
            _grant(
                id="egr_b",
                source=GrantSource.PLAN,
                value=EntitlementValue(allowance=99),
                precedence_override=MAX_PRECEDENCE,
            ),
        ],
        [_definition()],
        at=1_000.0,
    )
    assert resolved[0].allowance == 99


def test_a_precedence_override_outside_the_band_is_rejected():
    """An override outside the band would silently invert the whole ordering."""
    findings = EntitlementValidator().validate_grant(_grant(precedence_override=10_000))
    assert any(f.code.value == "invalid_precedence_override" for f in findings.errors)
    assert (
        not EntitlementValidator()
        .validate_grant(_grant(precedence_override=MIN_PRECEDENCE))
        .has_errors
    )


def test_a_grant_outside_its_window_takes_no_part():
    resolved = EntitlementPrecedenceResolver().resolve(
        [_grant(window=EntitlementWindow(ends_at=100.0))], [_definition()], at=1_000.0
    )
    assert resolved == ()


def test_an_undefined_code_resolves_fail_closed():
    """A capability nobody declared has no classification, and guessing "allow" would be the
    unsafe guess."""
    resolved = EntitlementPrecedenceResolver().resolve([_grant("ghost")], [], at=1_000.0)
    assert resolved[0].classification is GateClassification.FAIL_CLOSED


def test_defaults_are_off_by_default():
    """A snapshot asserting a default the customer was never given is unexplainable from the
    grants."""
    resolver = EntitlementPrecedenceResolver()
    assert resolver.resolve([], [_definition()], at=1_000.0) == ()
    with_defaults = resolver.resolve([], [_definition()], at=1_000.0, include_defaults=True)
    assert len(with_defaults) == 1
    assert "declared default" in with_defaults[0].resolution_path[0]


def test_a_retired_definition_contributes_no_default():
    resolver = EntitlementPrecedenceResolver()
    retired = _definition(retired_at=5.0)
    assert resolver.resolve([], [retired], at=1_000.0, include_defaults=True) == ()


def test_every_resolution_carries_its_path():
    """An entitlement a customer disputes is worth nothing if nobody can say why the system
    decided it."""
    resolved = EntitlementPrecedenceResolver().resolve(
        [_grant(source=GrantSource.CONTRACT, bundle_code="ent")], [_definition()], at=1_000.0
    )
    path = " ".join(resolved[0].resolution_path)
    assert "contract" in path and "bundle" in path and "precedence" in path


def test_a_resolved_entitlement_projects_onto_the_m9_2_type():
    resolved = EntitlementPrecedenceResolver().resolve([_grant()], [_definition()], at=1_000.0)
    projected = resolved[0].as_entitlement()
    assert isinstance(projected, Entitlement)
    assert projected.code == "sso"


# ── 6. Foundation: inspection ───────────────────────────────────────────────────


def test_inspection_keeps_the_losing_grants():
    """ "Why does this account NOT have X?" is asked at least as often as the opposite, and it is
    unanswerable from the winner alone."""
    explanation = EntitlementInspector().explain(
        _profile(
            _grant(id="egr_a", source=GrantSource.PLAN, value=EntitlementValue(allowance=100)),
            _grant(id="egr_b", source=GrantSource.CONTRACT, value=EntitlementValue(enabled=False)),
        ),
        [_definition()],
        "sso",
        at=1_000.0,
    )
    assert len(explanation.considered) == 2
    assert explanation.contested
    assert not explanation.is_granted
    assert sum(1 for c in explanation.considered if c.won) == 1


def test_inspection_says_why_a_grant_was_excluded():
    explanation = EntitlementInspector().explain(
        _profile(
            _grant(id="egr_a", state=GrantState.SUSPENDED),
            _grant(id="egr_b", window=EntitlementWindow(starts_at=5_000.0)),
        ),
        [_definition()],
        "sso",
        at=1_000.0,
    )
    reasons = {c.grant_id: c.excluded_reason for c in explanation.considered}
    assert "suspended" in reasons["egr_a"]
    assert "does not start until" in reasons["egr_b"]


def test_inspecting_an_undeclared_code_says_so():
    explanation = EntitlementInspector().explain(_profile(), [], "ghost", at=1_000.0)
    assert not explanation.definition_known
    assert explanation.winner is None


def test_a_single_grant_is_not_contested():
    explanation = EntitlementInspector().explain(
        _profile(_grant()), [_definition()], "sso", at=1_000.0
    )
    assert not explanation.contested


def test_diagnostics_report_every_finding_not_the_first():
    diagnostics = EntitlementInspector().diagnose(
        _profile(
            _grant(id="egr_a", code="ghost"),
            _grant(id="egr_b", code="ghost2", window=EntitlementWindow(starts_at=9, ends_at=1)),
        ),
        [],
        at=1_000.0,
    )
    assert len(diagnostics.errors) >= 2


def test_diagnostics_flag_a_contested_capability():
    diagnostics = EntitlementInspector().diagnose(
        _profile(
            _grant(id="egr_a", source=GrantSource.PLAN),
            _grant(id="egr_b", source=GrantSource.CONTRACT),
        ),
        [_definition()],
        at=1_000.0,
    )
    assert any(f.code.value == "conflicting_grant" for f in diagnostics.warnings)


def test_diagnostics_flag_a_grant_of_a_retired_capability():
    diagnostics = EntitlementInspector().diagnose(
        _profile(_grant()), [_definition(retired_at=5.0)], at=1_000.0
    )
    assert any(f.code.value == "retired_entitlement_definition" for f in diagnostics.warnings)


def test_a_clean_profile_diagnoses_clean():
    diagnostics = EntitlementInspector().diagnose(_profile(_grant()), [_definition()], at=1_000.0)
    assert diagnostics.is_valid


# ── 7. Foundation: bundles ──────────────────────────────────────────────────────


def _bundle(code, entries=(), members=()) -> EntitlementBundle:
    return EntitlementBundle(
        id="ebn_" + code,
        org_id="org_1",
        code=code,
        entries=tuple(BundleEntry(code=e) for e in entries),
        member_bundles=tuple(members),
    )


def test_a_bundle_expands_to_its_entries():
    expander = EntitlementBundleExpander()
    assert expander.codes([_bundle("sec", ["sso", "scim"])], "sec") == ("scim", "sso")


def test_a_nested_bundle_expands_transitively():
    bundles = [_bundle("sec", ["sso"]), _bundle("partner", ["white_label"], ["sec"])]
    assert EntitlementBundleExpander().codes(bundles, "partner") == ("sso", "white_label")


def test_a_nearer_definition_overrides_a_deeper_one():
    """An outer bundle that restates a capability is overriding the pack it includes — which is
    exactly why it restated it."""
    inner = EntitlementBundle(
        id="b1",
        org_id="org_1",
        code="sec",
        entries=(BundleEntry("minutes", EntitlementValue(allowance=100)),),
    )
    outer = EntitlementBundle(
        id="b2",
        org_id="org_1",
        code="partner",
        entries=(BundleEntry("minutes", EntitlementValue(allowance=9_000)),),
        member_bundles=("sec",),
    )
    expanded = EntitlementBundleExpander().expand([inner, outer], "partner")
    assert expanded[0].value.allowance == 9_000


def test_a_bundle_cycle_is_reported_with_its_path():
    """A bundle graph is customer-facing packaging; a cycle is a mistake somebody made, not an
    infinite capability."""
    bundles = [_bundle("a", ["x"], ["b"]), _bundle("b", ["y"], ["a"])]
    cycle = EntitlementBundleExpander().find_cycle(bundles, "a")
    assert cycle and cycle[0] == cycle[-1]
    with pytest.raises(BillingConflict) as excinfo:
        EntitlementBundleExpander().expand(bundles, "a")
    assert "cycle" in str(excinfo.value)


def test_a_self_including_bundle_is_rejected_at_validation():
    findings = EntitlementValidator().validate_bundle(_bundle("a", ["x"], ["a"]))
    assert any(f.code.value == "entitlement_bundle_cycle" for f in findings.errors)


def test_bundle_expansion_is_bounded():
    chain = [_bundle(f"b{i}", [f"c{i}"], [f"b{i + 1}"]) for i in range(MAX_BUNDLE_DEPTH + 2)]
    chain.append(_bundle(f"b{MAX_BUNDLE_DEPTH + 2}", ["leaf"]))
    with pytest.raises(BillingConflict) as excinfo:
        EntitlementBundleExpander().expand(chain, "b0")
    assert "nests deeper" in str(excinfo.value)


def test_an_empty_bundle_is_rejected():
    findings = EntitlementValidator().validate_bundle(_bundle("a"))
    assert any(f.code.value == "empty_entitlement_bundle" for f in findings.errors)


def test_a_bundle_entry_naming_no_declared_capability_is_rejected():
    findings = EntitlementValidator().validate_bundle(
        _bundle("a", ["ghost"]), known_codes=frozenset({"sso"})
    )
    assert any(f.code.value == "unknown_entitlement_definition" for f in findings.errors)


def test_an_empty_known_set_still_rejects_the_reference():
    """The distinction an empty-collection falsiness test silently loses — the defect M9.6 caught
    the hard way."""
    findings = EntitlementValidator().validate_bundle(
        _bundle("a", ["sso"]), known_codes=frozenset()
    )
    assert findings.has_errors


def test_expansion_is_byte_identical_across_runs():
    bundles = [_bundle("sec", ["sso", "scim", "audit"])]
    expander = EntitlementBundleExpander()
    assert expander.expand(bundles, "sec") == expander.expand(bundles, "sec")


def test_granting_a_bundle_mints_one_grant_per_entry():
    """A grant is the unit that gets revoked, suspended, and explained. A bundle reference would
    make "revoke just the SSO part" impossible without inventing an exception."""
    factory, _ = _factory()
    profile = factory.open_profile(org_id="org_1", account_id="cma_1")
    updated, grants = factory.grant_bundle(
        profile, [_bundle("sec", ["sso", "scim"])], bundle_code="sec"
    )
    assert len(grants) == 2
    assert {g.entitlement_code for g in grants} == {"sso", "scim"}
    assert all(g.bundle_code == "sec" for g in grants)
    assert updated.bundle_codes == ("sec",)


# ── 8. Foundation: snapshots and versions ───────────────────────────────────────


def test_composition_is_deterministic():
    assembler = EntitlementAssembler()
    profile = _profile(_grant(), _grant(id="egr_2", code="scim"))
    definitions = [_definition("sso"), _definition("scim")]
    first = assembler.compose(profile, definitions, snapshot_id="est_1", version=1, at=1_000.0)
    second = assembler.compose(profile, definitions, snapshot_id="est_1", version=1, at=1_000.0)
    assert first == second


def test_a_fingerprint_ignores_when_the_decision_was_made():
    """ "Did anything actually change?" must be a comparison, not an investigation."""
    assembler = EntitlementAssembler()
    profile = _profile(_grant())
    early = assembler.compose(profile, [_definition()], snapshot_id="a", version=1, at=1.0)
    late = assembler.compose(profile, [_definition()], snapshot_id="b", version=2, at=9_000.0)
    assert early.fingerprint == late.fingerprint
    assert assembler.says_the_same_as(late, early)


def test_a_changed_grant_changes_the_fingerprint():
    assembler = EntitlementAssembler()
    first = assembler.compose(
        _profile(_grant(value=EntitlementValue(allowance=1))),
        [_definition()],
        snapshot_id="a",
        version=1,
        at=1.0,
    )
    second = assembler.compose(
        _profile(_grant(value=EntitlementValue(allowance=2))),
        [_definition()],
        snapshot_id="b",
        version=2,
        at=1.0,
    )
    assert not assembler.says_the_same_as(second, first)


def test_gates_are_emitted_for_every_declared_capability_not_only_granted_ones():
    """An enforcing plane asking about an ungranted capability still needs to know how to behave
    when it cannot reach Billing."""
    composed = EntitlementAssembler().compose(
        _profile(_grant()),
        [_definition("sso"), _definition("scim")],
        snapshot_id="est_1",
        version=1,
        at=1_000.0,
    )
    assert composed.codes == ("sso",)
    assert {g.code for g in composed.gates} == {"sso", "scim"}


def test_a_retired_definition_publishes_no_gate():
    composed = EntitlementAssembler().compose(
        _profile(), [_definition("old", retired_at=1.0)], snapshot_id="e", version=1, at=2.0
    )
    assert composed.gates == ()


def test_an_unclassified_gate_is_an_error_not_a_warning():
    """ADR-0023 E3. An enforcing plane that cannot reach Billing has nothing to fall back on."""
    composed = EntitlementSet(
        id="est_1",
        org_id="org_1",
        account_id="cma_1",
        version=1,
        issued_at=1.0,
        entitlements=EntitlementPrecedenceResolver().resolve([_grant()], [_definition()], at=1.0),
        gates=(),
    )
    assert composed.unclassified_gates == ("sso",)
    findings = EntitlementValidator().validate_set(composed)
    assert any(f.code.value == "unclassified_gate" for f in findings.errors)


def test_a_set_projects_onto_the_m9_2_snapshot():
    """A plane written against the M9.5 contract consumes an M9.10 decision without changing a
    line."""
    composed = EntitlementAssembler().compose(
        _profile(_grant(value=EntitlementValue(allowance=42))),
        [_definition()],
        snapshot_id="est_1",
        version=3,
        at=1_000.0,
    )
    projected = composed.to_snapshot()
    assert isinstance(projected, EntitlementSnapshot)
    assert projected.version == 3
    assert projected.is_enabled("sso")
    assert projected.allowance("sso") == 42
    assert projected.classification("sso") is GateClassification.FAIL_OPEN
    assert projected.unclassified_gates == ()


def test_a_version_embeds_both_the_decision_and_the_grants():
    """Embedding the grants is what makes an old decision auditable rather than merely
    readable."""
    assembler = EntitlementAssembler()
    profile = _profile(_grant())
    composed = assembler.compose(profile, [_definition()], snapshot_id="e", version=1, at=1.0)
    version = assembler.freeze(profile, composed, version_id="evr_1", version=1, published_at=1.0)
    assert version.entitlements is not None
    assert version.profile is not None
    assert version.grant_count == 1
    assert version.entitlement_count == 1


def test_a_version_that_embeds_no_decision_is_rejected():
    findings = EntitlementValidator().validate_version(
        EntitlementVersion(id="evr_1", org_id="org_1", account_id="cma_1", version=1)
    )
    assert any(f.code.value == "entitlement_snapshot_integrity" for f in findings.errors)


def test_a_version_number_must_match_what_it_embeds():
    assembler = EntitlementAssembler()
    profile = _profile(_grant())
    composed = assembler.compose(profile, [_definition()], snapshot_id="e", version=5, at=1.0)
    mismatched = EntitlementVersion(
        id="evr_1", org_id="org_1", account_id="cma_1", version=1, entitlements=composed
    )
    assert EntitlementValidator().validate_version(mismatched).has_errors


def test_freezing_is_order_stable():
    assembler = EntitlementAssembler()
    a, b = _grant(id="egr_b", code="z"), _grant(id="egr_a", code="a")
    composed = assembler.compose(_profile(a, b), [], snapshot_id="e", version=1, at=1.0)
    first = assembler.freeze(_profile(a, b), composed, version_id="v", version=1, published_at=1.0)
    second = assembler.freeze(_profile(b, a), composed, version_id="v", version=1, published_at=1.0)
    assert first == second


def test_rollback_publishes_forward_and_records_provenance():
    """Nothing is rewritten and nothing is deleted: an enforcing plane may have acted on any of
    the versions in between."""
    assembler = EntitlementAssembler()
    profile = _profile(_grant())
    composed = assembler.compose(profile, [_definition()], snapshot_id="e", version=1, at=1.0)
    v1 = assembler.freeze(profile, composed, version_id="evr_1", version=1, published_at=1.0)
    v3 = assembler.rollback(v1, version_id="evr_3", version=3, published_at=99.0)
    assert v3.version == 3
    assert v3.rolled_back_from == 1
    assert v3.is_rollback
    assert v3.entitlements.version == 3


def test_rollback_cannot_go_backwards():
    assembler = EntitlementAssembler()
    profile = _profile(_grant())
    composed = assembler.compose(profile, [_definition()], snapshot_id="e", version=5, at=1.0)
    v5 = assembler.freeze(profile, composed, version_id="evr_5", version=5, published_at=1.0)
    with pytest.raises(BillingConflict):
        assembler.rollback(v5, version_id="x", version=2, published_at=2.0)


def test_a_rollback_source_must_be_earlier():
    version = EntitlementVersion(
        id="evr_1", org_id="org_1", account_id="cma_1", version=2, rolled_back_from=5
    )
    assert EntitlementValidator().validate_version(version).has_errors


def test_history_answers_what_was_decided_at_an_instant():
    assembler = EntitlementAssembler()
    profile = _profile(_grant())
    versions = [
        assembler.freeze(
            profile,
            assembler.compose(profile, [], snapshot_id="e", version=v, at=float(v)),
            version_id=f"evr_{v}",
            version=v,
            published_at=float(v * 100),
        )
        for v in (1, 2, 3)
    ]
    history = assembler.history("cma_1", versions=versions)
    assert history.version_at(150.0).version == 1
    assert history.version_at(250.0).version == 2
    assert history.version_at(10.0) is None
    assert history.current_version == 3


# ── 9. Foundation: staleness ────────────────────────────────────────────────────


def test_stale_directives_come_from_the_declared_classification():
    assert STALE_DIRECTIVES[GateClassification.FAIL_OPEN] is StaleDirective.ALLOW
    assert STALE_DIRECTIVES[GateClassification.FAIL_CLOSED] is StaleDirective.DENY
    assert STALE_DIRECTIVES[GateClassification.DEGRADE] is StaleDirective.DEGRADE


def test_every_classification_has_a_directive():
    assert set(STALE_DIRECTIVES) == set(GateClassification)


def test_a_fresh_snapshot_is_not_stale():
    composed = EntitlementAssembler().compose(
        _profile(_grant()), [_definition()], snapshot_id="e", version=1, at=1_000.0
    )
    report = EntitlementStalenessService().report(composed, at=1_100.0)
    assert not report.stale
    assert report.denied_codes == ()
    assert report.age == 100.0


def test_a_stale_snapshot_denies_only_the_fail_closed_gates():
    """ADR-0023 E4: bounded, measured revenue leakage is preferred — explicitly — to a
    customer-visible interruption."""
    composed = EntitlementAssembler().compose(
        _profile(),
        [
            _definition("chat", classification=GateClassification.FAIL_OPEN),
            _definition("spend", classification=GateClassification.FAIL_CLOSED),
            _definition("hd", classification=GateClassification.DEGRADE),
        ],
        snapshot_id="e",
        version=1,
        at=0.0,
    )
    report = EntitlementStalenessService().report(composed, at=DEFAULT_MAX_STALENESS_SECONDS + 1.0)
    assert report.stale
    assert report.denied_codes == ("spend",)
    assert report.degraded_codes == ("hd",)


def test_the_staleness_budget_travels_with_the_snapshot():
    profile = _profile(max_staleness_seconds=60.0)
    composed = EntitlementAssembler().compose(
        profile, [_definition()], snapshot_id="e", version=1, at=0.0
    )
    assert composed.max_staleness_seconds == 60.0
    assert composed.is_stale_at(61.0)
    assert not composed.is_stale_at(59.0)


def test_a_directive_for_an_unknown_gate_is_none():
    composed = EntitlementAssembler().compose(
        _profile(), [_definition()], snapshot_id="e", version=1, at=0.0
    )
    assert EntitlementStalenessService().directive_for(composed, "ghost", at=1.0) is None


# ── 10. Foundation: scheduled changes ───────────────────────────────────────────


def test_scheduling_records_intent_and_fires_nothing():
    factory, _ = _factory()
    profile = factory.open_profile(org_id="org_1", account_id="cma_1")
    scheduled = factory.schedule_change(
        profile,
        kind=EntitlementChangeKind.GRANT,
        effective_at=9_000.0,
        entitlement_code="sso",
    )
    assert len(scheduled.pending_changes) == 1
    # Reading the far future applies nothing.
    assert len(scheduled.due_changes(1e12)) == 1
    assert scheduled.grants == ()
    assert len(scheduled.pending_changes) == 1


def test_a_scheduled_grant_must_name_the_capability():
    findings = EntitlementValidator().validate_schedule(
        [
            _scheduled(EntitlementChangeKind.GRANT, code=""),
        ]
    )
    assert findings.has_errors


def _scheduled(kind, *, code="sso", grant_id="", at=9_000.0, change_id="ech_1"):
    from billing.foundation import ScheduledEntitlementChange

    return ScheduledEntitlementChange(
        id=change_id,
        kind=kind,
        effective_at=at,
        entitlement_code=code,
        grant_id=grant_id,
    )


def test_a_scheduled_revoke_must_name_the_grant():
    findings = EntitlementValidator().validate_schedule(
        [_scheduled(EntitlementChangeKind.REVOKE, grant_id="")]
    )
    assert findings.has_errors


def test_two_pending_changes_on_one_grant_are_an_ambiguous_future():
    findings = EntitlementValidator().validate_schedule(
        [
            _scheduled(EntitlementChangeKind.REVOKE, grant_id="egr_1", change_id="a"),
            _scheduled(EntitlementChangeKind.ADJUST, grant_id="egr_1", change_id="b"),
        ]
    )
    assert any(f.code.value == "conflicting_scheduled_entitlement_change" for f in findings.errors)


def test_pending_changes_are_bounded():
    changes = [
        _scheduled(EntitlementChangeKind.GRANT, change_id=f"ech_{i}", at=float(i + 1))
        for i in range(MAX_PENDING_ENTITLEMENT_CHANGES + 1)
    ]
    assert EntitlementValidator().validate_schedule(changes).has_errors


def test_a_change_due_in_the_past_is_a_warning_not_an_error():
    """Nothing in this domain applies it; a caller must dispatch the command."""
    findings = EntitlementValidator().validate_schedule(
        [_scheduled(EntitlementChangeKind.GRANT, at=10.0)], at=1_000.0
    )
    assert not findings.has_errors
    assert any(f.code.value == "scheduled_change_in_past" for f in findings.warnings)


def test_cancelling_an_intention_leaves_it_recorded():
    factory, _ = _factory()
    profile = factory.open_profile(org_id="org_1", account_id="cma_1")
    profile = factory.schedule_change(
        profile, kind=EntitlementChangeKind.GRANT, effective_at=9_000.0, entitlement_code="sso"
    )
    change_id = profile.pending_changes[0].id
    cancelled = EntitlementScheduleService().cancel_change(profile, change_id, at=10.0)
    assert cancelled.pending_changes == ()
    assert cancelled.scheduled_change(change_id).status.value == "cancelled"


def test_cancelling_an_already_resolved_change_is_refused():
    factory, _ = _factory()
    profile = factory.open_profile(org_id="org_1", account_id="cma_1")
    profile = factory.schedule_change(
        profile, kind=EntitlementChangeKind.GRANT, effective_at=9_000.0, entitlement_code="sso"
    )
    change_id = profile.pending_changes[0].id
    once = EntitlementScheduleService().cancel_change(profile, change_id, at=10.0)
    with pytest.raises(BillingConflict):
        EntitlementScheduleService().cancel_change(once, change_id, at=11.0)


def test_a_conflicting_intention_is_named_before_it_is_added():
    schedules = EntitlementScheduleService()
    profile = _profile(_grant())
    candidate = _scheduled(EntitlementChangeKind.REVOKE, grant_id="egr_missing")
    assert "not on this profile" in schedules.conflicts_with(profile, candidate)


# ── 11. Foundation: search ──────────────────────────────────────────────────────


def test_search_filters_compose():
    service = EntitlementSearchService()
    grants = (
        _grant(id="egr_1", code="sso", source=GrantSource.PLAN),
        _grant(id="egr_2", code="scim", source=GrantSource.CONTRACT, state=GrantState.DRAFT),
        _grant(id="egr_3", code="sso", source=GrantSource.CONTRACT, bundle_code="sec"),
    )
    assert service.search(grants, EntitlementSearchQuery(codes=("sso",))).total == 2
    assert service.search(grants, EntitlementSearchQuery(live_only=True)).total == 2
    assert service.search(grants, EntitlementSearchQuery(bundled_only=True)).total == 1
    assert (
        service.search(grants, EntitlementSearchQuery(sources=(GrantSource.CONTRACT,))).total == 2
    )
    assert service.search(grants, EntitlementSearchQuery(text="SC")).total == 1


def test_search_is_stably_ordered_and_pages_without_losing_the_total():
    service = EntitlementSearchService()
    grants = tuple(_grant(id=f"egr_{i}", code=f"c{i}") for i in range(10))
    page = service.search(grants, EntitlementSearchQuery(limit=3, offset=3))
    assert len(page.grants) == 3
    assert page.total == 10
    assert [g.entitlement_code for g in page.grants] == ["c3", "c4", "c5"]


def test_effective_at_filters_by_window_and_state():
    service = EntitlementSearchService()
    grants = (
        _grant(id="egr_1", window=EntitlementWindow(ends_at=100.0)),
        _grant(id="egr_2"),
        _grant(id="egr_3", state=GrantState.SUSPENDED),
    )
    assert service.search(grants, EntitlementSearchQuery(effective_at=500.0)).total == 1


# ── 12. Foundation: factory discipline ──────────────────────────────────────────


def test_a_rejected_grant_emits_no_business_event():
    """The fail-fast check runs before any emission, so a validation failure leaves no trace on
    the fact stream."""
    factory, emitter = _factory()
    profile = factory.open_profile(org_id="org_1", account_id="cma_1")
    before = len(emitter.events)
    with pytest.raises(BillingValidationError):
        factory.grant(profile, entitlement_code="ghost", known_codes=frozenset({"sso"}))
    assert len(emitter.events) == before


def test_a_successful_grant_emits_exactly_one_business_event():
    from billing.foundation import BillingEventType

    factory, emitter = _factory()
    profile = factory.open_profile(org_id="org_1", account_id="cma_1")
    factory.grant(profile, entitlement_code="sso")
    assert len(emitter.of_type(BillingEventType.ENTITLEMENT_GRANTED)) == 1


def test_a_rejected_definition_emits_no_business_event():
    factory, emitter = _factory()
    before = len(emitter.events)
    with pytest.raises(BillingValidationError):
        factory.define_entitlement(org_id="org_1", code="q", kind=FeatureKind.QUOTA)
    assert len(emitter.events) == before


def test_updating_a_definition_increments_its_version():
    """A changed classification changes how every enforcing plane behaves during an outage —
    that is a new declaration, not an edit."""
    factory, _ = _factory()
    definition = factory.define_entitlement(org_id="org_1", code="sso")
    amended = factory.update_definition(definition, classification=GateClassification.FAIL_CLOSED)
    assert amended.version == 2
    assert amended.classification is GateClassification.FAIL_CLOSED


# ── 13. Application over a real database ────────────────────────────────────────


def _account(app, code="acct") -> str:
    from billing.foundation import Currency

    account = app.commands.dispatch(
        CreateBillingAccount(**ORG, display_name="Acme " + code, currency=Currency.INR)
    )
    app.commands.dispatch(ActivateBillingAccount(**ORG, account_id=account.id))
    return account.id


def _seed(app, *, activate=True, account_id=None):
    account_id = account_id or _account(app)
    app.commands.dispatch(
        DefineEntitlement(**ORG, code="sso", classification=GateClassification.FAIL_OPEN)
    )
    app.commands.dispatch(
        DefineEntitlement(
            **ORG,
            code="minutes",
            kind=FeatureKind.QUOTA,
            meter_code="runtime.session",
            classification=GateClassification.FAIL_CLOSED,
        )
    )
    app.commands.dispatch(OpenEntitlementProfile(**ORG, account_id=account_id))
    profile = app.commands.dispatch(
        GrantCommercialEntitlement(
            **ORG,
            account_id=account_id,
            entitlement_code="sso",
            value=EntitlementValue(enabled=True),
        )
    )
    grant = profile.grants[-1]
    if activate:
        app.commands.dispatch(
            ActivateEntitlementGrant(**ORG, account_id=account_id, grant_id=grant.id)
        )
    return account_id, grant.id


def test_the_whole_grant_lifecycle_through_the_application(app):
    account_id, grant_id = _seed(app, activate=False)
    for command, expected in (
        (ActivateEntitlementGrant(**ORG, account_id=account_id, grant_id=grant_id), "active"),
        (SuspendEntitlementGrant(**ORG, account_id=account_id, grant_id=grant_id), "suspended"),
        (ResumeEntitlementGrant(**ORG, account_id=account_id, grant_id=grant_id), "active"),
        (RevokeCommercialEntitlement(**ORG, account_id=account_id, grant_id=grant_id), "revoked"),
        (ArchiveEntitlementGrant(**ORG, account_id=account_id, grant_id=grant_id), "archived"),
    ):
        profile = app.commands.dispatch(command)
        assert profile.grant(grant_id).state.value == expected


def test_an_illegal_transition_through_the_application_raises(app):
    account_id, grant_id = _seed(app, activate=False)
    with pytest.raises(InvalidBillingTransition):
        app.commands.dispatch(
            ResumeEntitlementGrant(**ORG, account_id=account_id, grant_id=grant_id)
        )


def test_a_grant_of_an_undeclared_capability_is_rejected(app):
    account_id = _account(app)
    app.commands.dispatch(OpenEntitlementProfile(**ORG, account_id=account_id))
    with pytest.raises(BillingValidationError):
        app.commands.dispatch(
            GrantCommercialEntitlement(**ORG, account_id=account_id, entitlement_code="ghost")
        )


def test_publishing_composes_from_grants(app):
    account_id, _ = _seed(app)
    version = app.commands.dispatch(
        PublishCommercialEntitlementSnapshot(**ORG, account_id=account_id, reason="initial")
    )
    assert version.version == 1
    snapshot = app.queries.dispatch(GetCommercialEntitlementSnapshot(**ORG, account_id=account_id))
    assert snapshot.codes == ("sso",)
    assert {g.code for g in snapshot.gates} == {"sso", "minutes"}


def test_republishing_an_unchanged_decision_is_refused(app):
    """A hundred identical versions would bury the ones that meant something."""
    account_id, _ = _seed(app)
    app.commands.dispatch(PublishCommercialEntitlementSnapshot(**ORG, account_id=account_id))
    with pytest.raises(BillingConflict) as excinfo:
        app.commands.dispatch(PublishCommercialEntitlementSnapshot(**ORG, account_id=account_id))
    assert "unchanged" in str(excinfo.value)


def test_an_audit_boundary_can_be_published_deliberately(app):
    account_id, _ = _seed(app)
    app.commands.dispatch(PublishCommercialEntitlementSnapshot(**ORG, account_id=account_id))
    second = app.commands.dispatch(
        PublishCommercialEntitlementSnapshot(
            **ORG, account_id=account_id, allow_identical=True, reason="quarterly audit"
        )
    )
    assert second.version == 2


def test_a_published_snapshot_can_never_be_republished(app):
    account_id, _ = _seed(app)
    app.commands.dispatch(PublishCommercialEntitlementSnapshot(**ORG, account_id=account_id))
    versions = app.context.entitlements_engine.versions
    latest = versions.latest("org_1", account_id)
    with pytest.raises(ImmutabilityViolation):
        versions.publish(latest)


def test_changing_grants_after_publication_does_not_touch_the_published_snapshot(app):
    """The property enforcement depends on: a snapshot a plane acted on must read back exactly
    as it was published."""
    account_id, grant_id = _seed(app)
    app.commands.dispatch(PublishCommercialEntitlementSnapshot(**ORG, account_id=account_id))
    before = app.queries.dispatch(
        GetCommercialEntitlementSnapshot(**ORG, account_id=account_id, version=1)
    )
    app.commands.dispatch(
        RevokeCommercialEntitlement(**ORG, account_id=account_id, grant_id=grant_id)
    )
    after = app.queries.dispatch(
        GetCommercialEntitlementSnapshot(**ORG, account_id=account_id, version=1)
    )
    assert before == after
    assert after.codes == ("sso",)


def test_a_snapshot_is_returned_as_published_not_recomputed(app):
    account_id, grant_id = _seed(app)
    app.commands.dispatch(PublishCommercialEntitlementSnapshot(**ORG, account_id=account_id))
    app.commands.dispatch(
        RevokeCommercialEntitlement(**ORG, account_id=account_id, grant_id=grant_id)
    )
    latest = app.queries.dispatch(GetCommercialEntitlementSnapshot(**ORG, account_id=account_id))
    assert latest.codes == ("sso",)  # v1 still says sso; nothing recomputed on read
    preview = app.queries.dispatch(PreviewEntitlementSnapshot(**ORG, account_id=account_id))
    assert preview.codes == ()  # the *preview* reflects the revocation


def test_preview_publishes_nothing(app):
    account_id, _ = _seed(app)
    app.queries.dispatch(PreviewEntitlementSnapshot(**ORG, account_id=account_id))
    assert app.queries.dispatch(EntitlementVersionHistory(**ORG, account_id=account_id)) == ()


def test_rollback_through_the_application(app):
    account_id, grant_id = _seed(app)
    app.commands.dispatch(
        PublishCommercialEntitlementSnapshot(**ORG, account_id=account_id, reason="v1")
    )
    app.commands.dispatch(
        RevokeCommercialEntitlement(**ORG, account_id=account_id, grant_id=grant_id)
    )
    app.commands.dispatch(
        PublishCommercialEntitlementSnapshot(**ORG, account_id=account_id, reason="v2")
    )
    rolled = app.commands.dispatch(
        RollbackEntitlementVersion(**ORG, account_id=account_id, to_version=1)
    )
    assert rolled.version == 3
    assert rolled.rolled_back_from == 1
    history = app.queries.dispatch(EntitlementVersionHistory(**ORG, account_id=account_id))
    assert [v.version for v in history] == [3, 2, 1]  # nothing in between was removed


def test_rolling_back_to_an_unknown_version_is_not_found(app):
    from billing.foundation import BillingNotFound

    account_id, _ = _seed(app)
    with pytest.raises(BillingNotFound):
        app.commands.dispatch(
            RollbackEntitlementVersion(**ORG, account_id=account_id, to_version=9)
        )


def test_precedence_end_to_end_through_the_application(app):
    account_id, _ = _seed(app)
    profile = app.commands.dispatch(
        GrantCommercialEntitlement(
            **ORG,
            account_id=account_id,
            entitlement_code="sso",
            value=EntitlementValue(enabled=False),
            source=GrantSource.CONTRACT,
            source_ref="ct_1",
        )
    )
    contract_grant = profile.grants[-1]
    app.commands.dispatch(
        ActivateEntitlementGrant(**ORG, account_id=account_id, grant_id=contract_grant.id)
    )
    preview = app.queries.dispatch(PreviewEntitlementSnapshot(**ORG, account_id=account_id))
    assert preview.entitlements[0].source == "contract"
    assert not preview.entitlements[0].enabled


def test_inspection_end_to_end(app):
    account_id, _ = _seed(app)
    explanation = app.queries.dispatch(
        InspectEntitlement(**ORG, account_id=account_id, entitlement_code="sso")
    )
    assert explanation.is_granted
    assert explanation.winner.grant_id
    assert len(explanation.considered) == 1


def test_bundles_end_to_end(app):
    account_id, _ = _seed(app)
    app.commands.dispatch(
        CreateEntitlementBundle(
            **ORG,
            code="sec",
            entries=(BundleEntry("sso"), BundleEntry("minutes", EntitlementValue(allowance=500))),
        )
    )
    profile = app.commands.dispatch(
        GrantEntitlementBundleToAccount(
            **ORG, account_id=account_id, bundle_code="sec", source=GrantSource.CONTRACT
        )
    )
    bundled = [g for g in profile.grants if g.bundle_code == "sec"]
    assert len(bundled) == 2
    for g in bundled:
        app.commands.dispatch(ActivateEntitlementGrant(**ORG, account_id=account_id, grant_id=g.id))
    preview = app.queries.dispatch(PreviewEntitlementSnapshot(**ORG, account_id=account_id))
    assert set(preview.codes) == {"sso", "minutes"}


def test_a_bundle_naming_an_undeclared_capability_is_rejected(app):
    _seed(app)
    with pytest.raises(BillingValidationError):
        app.commands.dispatch(
            CreateEntitlementBundle(**ORG, code="bad", entries=(BundleEntry("ghost"),))
        )


def test_updating_a_bundle_bumps_its_version(app):
    _seed(app)
    app.commands.dispatch(CreateEntitlementBundle(**ORG, code="sec", entries=(BundleEntry("sso"),)))
    updated = app.commands.dispatch(
        UpdateEntitlementBundle(**ORG, code="sec", entries=(BundleEntry("minutes"),))
    )
    assert updated.version == 2


def test_retiring_a_definition_hides_it_from_the_default_listing(app):
    _seed(app)
    app.commands.dispatch(RetireEntitlementDefinition(**ORG, code="sso", reason="superseded"))
    visible = app.queries.dispatch(ListEntitlementDefinitions(**ORG))
    assert "sso" not in {d.code for d in visible}
    everything = app.queries.dispatch(ListEntitlementDefinitions(**ORG, include_retired=True))
    assert "sso" in {d.code for d in everything}


def test_updating_a_definition_through_the_application(app):
    _seed(app)
    updated = app.commands.dispatch(
        UpdateEntitlementDefinition(
            **ORG, code="sso", classification=GateClassification.FAIL_CLOSED
        )
    )
    assert updated.classification is GateClassification.FAIL_CLOSED
    assert updated.version == 2


def test_adjusting_a_grant_through_the_application(app):
    account_id, grant_id = _seed(app)
    profile = app.commands.dispatch(
        AdjustEntitlementGrant(
            **ORG,
            account_id=account_id,
            grant_id=grant_id,
            value=EntitlementValue(enabled=True, allowance=250),
        )
    )
    assert profile.grant(grant_id).value.allowance == 250


def test_a_closed_grant_refuses_adjustment(app):
    account_id, grant_id = _seed(app)
    app.commands.dispatch(
        RevokeCommercialEntitlement(**ORG, account_id=account_id, grant_id=grant_id)
    )
    with pytest.raises(BillingConflict):
        app.commands.dispatch(
            AdjustEntitlementGrant(
                **ORG, account_id=account_id, grant_id=grant_id, value=EntitlementValue()
            )
        )


def test_expiring_a_grant_through_the_application(app):
    account_id, grant_id = _seed(app)
    profile = app.commands.dispatch(
        ExpireEntitlementGrant(**ORG, account_id=account_id, grant_id=grant_id)
    )
    assert profile.grant(grant_id).state is GrantState.EXPIRED


def test_scheduled_changes_through_the_application_apply_nothing(app):
    account_id, _ = _seed(app)
    app.commands.dispatch(
        ScheduleEntitlementChange(
            **ORG,
            account_id=account_id,
            kind=EntitlementChangeKind.GRANT,
            effective_at=9_000.0,
            entitlement_code="minutes",
        )
    )
    due = app.queries.dispatch(
        ListScheduledEntitlementChanges(**ORG, account_id=account_id, due_at=1e12)
    )
    assert len(due) == 1
    preview = app.queries.dispatch(PreviewEntitlementSnapshot(**ORG, account_id=account_id))
    assert "minutes" not in preview.codes  # nothing applied it


def test_a_scheduled_change_can_be_cancelled_through_the_application(app):
    account_id, _ = _seed(app)
    profile = app.commands.dispatch(
        ScheduleEntitlementChange(
            **ORG,
            account_id=account_id,
            kind=EntitlementChangeKind.GRANT,
            effective_at=9_000.0,
            entitlement_code="minutes",
        )
    )
    change_id = profile.pending_changes[0].id
    app.commands.dispatch(
        CancelScheduledEntitlementChange(**ORG, account_id=account_id, change_id=change_id)
    )
    assert app.queries.dispatch(ListScheduledEntitlementChanges(**ORG, account_id=account_id)) == ()


def test_staleness_through_the_application(app):
    account_id, _ = _seed(app)
    app.commands.dispatch(PublishCommercialEntitlementSnapshot(**ORG, account_id=account_id))
    report = app.queries.dispatch(GetEntitlementStaleness(**ORG, account_id=account_id, at=1e10))
    assert report.stale
    assert "minutes" in report.denied_codes  # fail_closed
    assert "sso" not in report.denied_codes  # fail_open


def test_diagnostics_through_the_application(app):
    account_id, _ = _seed(app)
    report = app.queries.dispatch(DiagnoseEntitlements(**ORG, account_id=account_id))
    assert report.is_valid


def test_search_through_the_application(app):
    account_id, _ = _seed(app)
    result = app.queries.dispatch(
        SearchEntitlementGrants(**ORG, query=EntitlementSearchQuery(live_only=True))
    )
    assert result.total == 1
    assert result.grants[0].entitlement_code == "sso"


def test_every_command_appends_to_the_timeline(app):
    account_id, grant_id = _seed(app)
    app.commands.dispatch(
        SuspendEntitlementGrant(**ORG, account_id=account_id, grant_id=grant_id, reason="review")
    )
    timeline = app.queries.dispatch(EntitlementTimeline(**ORG, account_id=account_id))
    actions = {a.action for a in timeline}
    assert "entitlement.profile_opened" in actions
    assert "entitlement.granted" in actions
    assert "entitlement.activated" in actions
    assert "entitlement.suspended" in actions


def test_the_timeline_records_the_states_a_transition_moved_between(app):
    account_id, grant_id = _seed(app)
    timeline = app.queries.dispatch(EntitlementTimeline(**ORG, account_id=account_id))
    activation = next(a for a in timeline if a.action == "entitlement.activated")
    assert (activation.from_state, activation.to_state) == ("draft", "active")


def test_a_failed_command_leaves_no_history_behind(app):
    account_id, _ = _seed(app)
    before = len(app.queries.dispatch(EntitlementTimeline(**ORG, account_id=account_id)))
    with pytest.raises(BillingValidationError):
        app.commands.dispatch(
            GrantCommercialEntitlement(**ORG, account_id=account_id, entitlement_code="ghost")
        )
    after = len(app.queries.dispatch(EntitlementTimeline(**ORG, account_id=account_id)))
    assert before == after


def test_cross_org_isolation(app):
    account_id, _ = _seed(app)
    other = dict(actor_id="usr_2", org_id="org_2")
    assert app.queries.dispatch(GetEntitlementProfile(**other, account_id=account_id)) is None
    assert app.queries.dispatch(ListEntitlementDefinitions(**other)) == ()
    assert app.queries.dispatch(ListEntitlementBundles(**other)) == ()


def test_a_grant_naming_an_unknown_subscription_is_rejected(app):
    """Only PLAN-sourced grants are checked: a contract or partner reference names a document
    this domain knows nothing about."""
    account_id, _ = _seed(app)
    with pytest.raises(BillingValidationError):
        app.commands.dispatch(
            GrantCommercialEntitlement(
                **ORG,
                account_id=account_id,
                entitlement_code="minutes",
                source=GrantSource.PLAN,
                source_ref="csb_nonexistent",
            )
        )


def test_a_contract_reference_is_not_checked_against_subscriptions(app):
    account_id, _ = _seed(app)
    profile = app.commands.dispatch(
        GrantCommercialEntitlement(
            **ORG,
            account_id=account_id,
            entitlement_code="minutes",
            source=GrantSource.CONTRACT,
            source_ref="ct_42",
        )
    )
    assert profile.grants[-1].source_ref == "ct_42"


def test_every_entitlement_command_and_query_is_authorized():
    """Deny-by-default, on every message. A read is as much a decision as a write."""
    from billing.application import ALL_ENTITLEMENT_COMMANDS, ALL_ENTITLEMENT_QUERIES

    wired = build_persistent_billing_application(
        ids=SequentialIdGenerator(), clock=FixedClock(), authorizer=DenyAllAuthorizer()
    )
    for message in (*ALL_ENTITLEMENT_COMMANDS, *ALL_ENTITLEMENT_QUERIES):
        bus = (
            wired.application.commands
            if message in ALL_ENTITLEMENT_COMMANDS
            else wired.application.queries
        )
        with pytest.raises(BillingAuthorizationError):
            bus.dispatch(message(**ORG))
    wired.database.close()


def test_entitlements_and_history_survive_a_restart():
    database = BillingDatabase("sqlite://")
    first = build_persistent_billing_application(
        database=database,
        ids=SequentialIdGenerator(),
        clock=FixedClock(),
        authorizer=AllowAllAuthorizer(),
    )
    account_id, _ = _seed(first.application)
    first.application.commands.dispatch(
        PublishCommercialEntitlementSnapshot(**ORG, account_id=account_id, reason="v1")
    )

    # A second Application over the same storage: proof the data landed in the database rather
    # than in some object's memory. `DefaultIdGenerator` because two sequential generators over
    # one database collide on the shared event-id sequence (recorded at M9.8).
    second = build_persistent_billing_application(
        database=database,
        ids=DefaultIdGenerator(),
        clock=FixedClock(),
        create_all=False,
        authorizer=AllowAllAuthorizer(),
    )
    snapshot = second.application.queries.dispatch(
        GetCommercialEntitlementSnapshot(**ORG, account_id=account_id)
    )
    assert snapshot.codes == ("sso",)
    timeline = second.application.queries.dispatch(
        EntitlementTimeline(**ORG, account_id=account_id)
    )
    assert len(timeline) >= 4
    database.close()


# ── 14. Persistence ─────────────────────────────────────────────────────────────


def test_entitlement_tables_are_created_by_the_migration_chain():
    from sqlalchemy import inspect

    from billing.persistence.entitlement_models import ENTITLEMENT_TABLE_NAMES
    from billing.persistence.migrations import MIGRATIONS, upgrade_all

    database = BillingDatabase("sqlite://")
    applied = upgrade_all(database)
    # Position, not tail: later milestones append to the chain, and asserting the tail would
    # make every future migration break this test for no reason.
    assert "0020" in applied
    assert applied.index("0020") == len(applied) - applied[::-1].index("0020") - 1
    assert [m.VERSION for m in MIGRATIONS] == sorted(m.VERSION for m in MIGRATIONS)
    tables = set(inspect(database.engine).get_table_names())
    for name in ENTITLEMENT_TABLE_NAMES:
        assert name in tables
    database.close()


def test_the_entitlement_migration_alters_nothing_and_is_idempotent():
    from sqlalchemy import inspect

    from billing.persistence.migrations import upgrade_all, v0020_billing_entitlements

    database = BillingDatabase("sqlite://")
    upgrade_all(database)
    before = {
        t: {c["name"] for c in inspect(database.engine).get_columns(t)}
        for t in inspect(database.engine).get_table_names()
    }
    v0020_billing_entitlements.upgrade(database)
    v0020_billing_entitlements.upgrade(database)
    after = {
        t: {c["name"] for c in inspect(database.engine).get_columns(t)}
        for t in inspect(database.engine).get_table_names()
    }
    assert before == after
    # M9.2's minimal entitlement tables are untouched.
    assert "cp_billing_entitlement_grant" in before
    assert "cp_billing_entitlement_snapshot" in before
    database.close()


def test_append_only_entitlement_stores_expose_no_mutation_path():
    from billing.foundation import EntitlementActivityRepository, EntitlementVersionRepository

    for contract in (EntitlementVersionRepository, EntitlementActivityRepository):
        methods = set(dir(contract))
        assert "delete" not in methods
        assert "update" not in methods
        assert "replace" not in methods


def test_round_trip_fidelity_for_the_entitlement_aggregate():
    """The whole aggregate, grants and intentions included, must survive storage unchanged."""
    from billing.persistence.codec import decode, encode

    profile = _profile(
        _grant(
            id="egr_1",
            source=GrantSource.CONTRACT,
            value=EntitlementValue(enabled=True, allowance=42, allows_overage=True),
            window=EntitlementWindow(starts_at=1.0, ends_at=2.0),
            precedence_override=55,
            bundle_code="sec",
        ),
        _grant(id="egr_2", code="scim", state=GrantState.SUSPENDED),
        scheduled_changes=(_scheduled(EntitlementChangeKind.REVOKE, grant_id="egr_1"),),
        subscription_id="csb_1",
        version=3,
    )
    assert decode(EntitlementProfile, encode(profile)) == profile


def test_round_trip_fidelity_for_a_published_version():
    from billing.persistence.codec import decode, encode

    assembler = EntitlementAssembler()
    profile = _profile(_grant())
    composed = assembler.compose(profile, [_definition()], snapshot_id="e", version=1, at=1.0)
    version = assembler.freeze(profile, composed, version_id="evr_1", version=1, published_at=1.0)
    assert decode(EntitlementVersion, encode(version)) == version


def test_grants_travel_with_the_profile_as_one_aggregate():
    """Precedence resolution is only correct when it sees *all* of an account's grants at once;
    a row-per-grant table invites a partial read that resolves to the wrong answer."""
    from billing.persistence.entitlement_models import ENTITLEMENT_TABLE_NAMES

    assert not any("grant" in name for name in ENTITLEMENT_TABLE_NAMES)


# ── 15. HTTP ────────────────────────────────────────────────────────────────────


def _http_seed(client, *, activate=True) -> tuple[str, str]:
    account = client.post(
        "/v1/billing/billing-accounts",
        headers=_auth(),
        json={"display_name": "Acme", "currency": "INR"},
    ).json()
    client.post(f"/v1/billing/billing-accounts/{account['id']}/activate", headers=_auth())
    client.post(
        f"{PREFIX}/definitions",
        headers=_auth(),
        json={"code": "sso", "classification": "fail_open"},
    )
    client.post(
        f"{PREFIX}/definitions",
        headers=_auth(),
        json={
            "code": "minutes",
            "kind": "quota",
            "meter_code": "runtime.session",
            "classification": "fail_closed",
        },
    )
    client.post(f"{PREFIX}/profiles", headers=_auth(), json={"account_id": account["id"]})
    profile = client.post(
        f"{PREFIX}/profiles/{account['id']}/grants",
        headers=_auth(),
        json={"entitlement_code": "sso", "value": {"enabled": True}},
    ).json()
    grant_id = profile["grants"][-1]["id"]
    if activate:
        client.post(
            f"{PREFIX}/profiles/{account['id']}/grants/{grant_id}/activate", headers=_auth()
        )
    return account["id"], grant_id


def test_the_grant_lifecycle_over_http(client):
    account_id, grant_id = _http_seed(client, activate=False)
    base = f"{PREFIX}/profiles/{account_id}/grants/{grant_id}"
    for path, expected in (
        ("activate", "active"),
        ("suspend", "suspended"),
        ("resume", "active"),
        ("revoke", "revoked"),
        ("archive", "archived"),
    ):
        response = client.post(f"{base}/{path}", headers=_auth(), json={"reason": ""})
        assert response.status_code == 200, response.text
        grant = next(g for g in response.json()["grants"] if g["id"] == grant_id)
        assert grant["state"] == expected


def test_an_illegal_transition_over_http_is_409(client):
    account_id, grant_id = _http_seed(client, activate=False)
    response = client.post(
        f"{PREFIX}/profiles/{account_id}/grants/{grant_id}/resume", headers=_auth()
    )
    assert response.status_code == 409


def test_an_undeclared_capability_over_http_is_422_with_diagnostics(client):
    account_id, _ = _http_seed(client)
    response = client.post(
        f"{PREFIX}/profiles/{account_id}/grants",
        headers=_auth(),
        json={"entitlement_code": "ghost"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["diagnostics"]


def test_a_quota_without_a_meter_over_http_is_422(client):
    _http_seed(client)
    response = client.post(
        f"{PREFIX}/definitions", headers=_auth(), json={"code": "q", "kind": "quota"}
    )
    assert response.status_code == 422


def test_there_is_no_delete_on_a_grant_at_any_path(client):
    """A revoked grant is kept: why a capability went away is itself a fact."""
    account_id, grant_id = _http_seed(client)
    for path in (
        f"{PREFIX}/profiles/{account_id}",
        f"{PREFIX}/profiles/{account_id}/grants/{grant_id}",
        f"{PREFIX}/definitions/sso",
        f"{PREFIX}/bundles/sec",
    ):
        assert client.delete(path, headers=_auth()).status_code == 405, path


def test_published_snapshots_have_no_mutation_verbs(client):
    account_id, _ = _http_seed(client)
    client.post(f"{PREFIX}/profiles/{account_id}/snapshots", headers=_auth(), json={})
    for verb in (client.put, client.patch, client.delete):
        assert verb(f"{PREFIX}/profiles/{account_id}/snapshot", headers=_auth()).status_code == 405


def test_the_only_deletion_in_the_surface_is_a_pending_intention(client):
    """A rule statement, not an allow-list: the single DELETE removes an *intention* that was
    never acted on — never a grant, a decision, or a published version."""
    schema = client.get("/openapi.json").json()
    deletions = [
        path
        for path, operations in schema["paths"].items()
        if "/capabilities" in path and "delete" in operations
    ]
    assert deletions == [
        "/v1/billing/capabilities/profiles/{account_id}/scheduled-changes/{change_id}"
    ]


def test_snapshot_publishing_and_reading_over_http(client):
    account_id, _ = _http_seed(client)
    published = client.post(
        f"{PREFIX}/profiles/{account_id}/snapshots", headers=_auth(), json={"reason": "initial"}
    )
    assert published.status_code == 201, published.text
    assert published.json()["version"] == 1

    snapshot = client.get(f"{PREFIX}/profiles/{account_id}/snapshot", headers=_auth()).json()
    assert snapshot["codes"] == ["sso"]
    assert {g["code"] for g in snapshot["gates"]} == {"sso", "minutes"}
    assert snapshot["fail_closed_codes"] == ["minutes"]
    assert snapshot["unclassified_gates"] == []


def test_preview_over_http_publishes_nothing(client):
    account_id, _ = _http_seed(client)
    preview = client.get(f"{PREFIX}/profiles/{account_id}/preview", headers=_auth())
    assert preview.status_code == 200
    versions = client.get(f"{PREFIX}/profiles/{account_id}/versions", headers=_auth()).json()
    assert versions == []


def test_republishing_unchanged_over_http_is_409(client):
    account_id, _ = _http_seed(client)
    client.post(f"{PREFIX}/profiles/{account_id}/snapshots", headers=_auth(), json={})
    again = client.post(f"{PREFIX}/profiles/{account_id}/snapshots", headers=_auth(), json={})
    assert again.status_code == 409


def test_rollback_over_http(client):
    account_id, grant_id = _http_seed(client)
    client.post(f"{PREFIX}/profiles/{account_id}/snapshots", headers=_auth(), json={})
    client.post(
        f"{PREFIX}/profiles/{account_id}/grants/{grant_id}/revoke",
        headers=_auth(),
        json={"reason": ""},
    )
    client.post(f"{PREFIX}/profiles/{account_id}/snapshots", headers=_auth(), json={})
    rolled = client.post(
        f"{PREFIX}/profiles/{account_id}/rollback",
        headers=_auth(),
        json={"to_version": 1, "reason": "restore"},
    )
    assert rolled.status_code == 201
    assert rolled.json()["rolled_back_from"] == 1
    assert rolled.json()["version"] == 3


def test_bundles_over_http(client):
    account_id, _ = _http_seed(client)
    created = client.post(
        f"{PREFIX}/bundles",
        headers=_auth(),
        json={
            "code": "sec",
            "entries": [
                {"code": "sso"},
                {"code": "minutes", "value": {"enabled": True, "allowance": 500}},
            ],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["expanded_codes"] == ["minutes", "sso"]

    granted = client.post(
        f"{PREFIX}/profiles/{account_id}/bundle-grants",
        headers=_auth(),
        json={"bundle_code": "sec", "source": "contract"},
    )
    assert granted.status_code == 201
    assert len([g for g in granted.json()["grants"] if g["bundle_code"] == "sec"]) == 2


def test_a_bundle_cycle_over_http_is_rejected(client):
    _http_seed(client)
    client.post(
        f"{PREFIX}/bundles", headers=_auth(), json={"code": "a", "entries": [{"code": "sso"}]}
    )
    response = client.patch(f"{PREFIX}/bundles/a", headers=_auth(), json={"member_bundles": ["a"]})
    assert response.status_code == 422


def test_inspection_over_http_shows_the_losing_grant(client):
    account_id, _ = _http_seed(client)
    profile = client.post(
        f"{PREFIX}/profiles/{account_id}/grants",
        headers=_auth(),
        json={
            "entitlement_code": "sso",
            "value": {"enabled": False},
            "source": "contract",
            "source_ref": "ct_1",
        },
    ).json()
    contract_grant = profile["grants"][-1]["id"]
    client.post(f"{PREFIX}/profiles/{account_id}/grants/{contract_grant}/activate", headers=_auth())
    explanation = client.get(f"{PREFIX}/profiles/{account_id}/inspect/sso", headers=_auth()).json()
    assert explanation["contested"]
    assert explanation["winner"]["source"] == "contract"
    assert not explanation["is_granted"]
    assert len(explanation["considered"]) == 2
    assert sum(1 for c in explanation["considered"] if c["won"]) == 1


def test_staleness_over_http(client):
    account_id, _ = _http_seed(client)
    client.post(f"{PREFIX}/profiles/{account_id}/snapshots", headers=_auth(), json={})
    report = client.get(f"{PREFIX}/profiles/{account_id}/staleness?at=1e10", headers=_auth()).json()
    assert report["stale"]
    assert report["denied_codes"] == ["minutes"]
    directives = {d["code"]: d["directive"] for d in report["directives"]}
    assert directives == {"sso": "allow", "minutes": "deny"}


def test_diagnostics_over_http(client):
    account_id, _ = _http_seed(client)
    report = client.get(f"{PREFIX}/profiles/{account_id}/diagnostics", headers=_auth()).json()
    assert report["is_valid"]
    assert report["error_count"] == 0


def test_scheduled_changes_over_http(client):
    account_id, _ = _http_seed(client)
    created = client.post(
        f"{PREFIX}/profiles/{account_id}/scheduled-changes",
        headers=_auth(),
        json={"kind": "grant", "effective_at": 9_000.0, "entitlement_code": "minutes"},
    )
    assert created.status_code == 201
    listed = client.get(f"{PREFIX}/profiles/{account_id}/scheduled-changes", headers=_auth()).json()
    assert len(listed) == 1
    change_id = listed[0]["id"]

    # Nothing applies it, even when it is long overdue.
    due = client.get(
        f"{PREFIX}/profiles/{account_id}/scheduled-changes?due_at=1e12", headers=_auth()
    ).json()
    assert len(due) == 1
    preview = client.get(f"{PREFIX}/profiles/{account_id}/preview", headers=_auth()).json()
    assert "minutes" not in preview["codes"]

    cancelled = client.delete(
        f"{PREFIX}/profiles/{account_id}/scheduled-changes/{change_id}", headers=_auth()
    )
    assert cancelled.status_code == 200
    assert (
        client.get(f"{PREFIX}/profiles/{account_id}/scheduled-changes", headers=_auth()).json()
        == []
    )


def test_search_over_http(client):
    account_id, _ = _http_seed(client)
    found = client.get(f"{PREFIX}/grants?live_only=true", headers=_auth()).json()
    assert found["total"] == 1
    assert found["grants"][0]["entitlement_code"] == "sso"


def test_timeline_over_http(client):
    account_id, _ = _http_seed(client)
    timeline = client.get(f"{PREFIX}/profiles/{account_id}/timeline", headers=_auth()).json()
    assert {a["action"] for a in timeline} >= {
        "entitlement.profile_opened",
        "entitlement.granted",
        "entitlement.activated",
    }


def test_retiring_a_definition_over_http(client):
    _http_seed(client)
    retired = client.post(
        f"{PREFIX}/definitions/sso/retire", headers=_auth(), json={"reason": "superseded"}
    )
    assert retired.status_code == 200
    assert retired.json()["is_retired"]
    listed = client.get(f"{PREFIX}/definitions", headers=_auth()).json()
    assert "sso" not in {d["code"] for d in listed}


def test_no_amount_appears_anywhere_on_the_wire(client):
    """An allowance says how much is included, never what it costs."""
    account_id, _ = _http_seed(client)
    client.post(f"{PREFIX}/profiles/{account_id}/snapshots", headers=_auth(), json={})
    for path in (
        f"{PREFIX}/definitions",
        f"{PREFIX}/profiles/{account_id}",
        f"{PREFIX}/profiles/{account_id}/snapshot",
        f"{PREFIX}/profiles/{account_id}/timeline",
    ):
        body = client.get(path, headers=_auth()).text
        for forbidden in ('"minor"', '"currency"', '"amount"', '"price"'):
            assert forbidden not in body, (path, forbidden)


def test_no_principal_appears_anywhere_on_the_wire(client):
    """Entitlement is commercial capability. The API has no field in which to express an
    authorization question."""
    account_id, _ = _http_seed(client)
    client.post(f"{PREFIX}/profiles/{account_id}/snapshots", headers=_auth(), json={})
    schema = client.get("/openapi.json").json()
    entitlement_schemas = {
        name: body
        for name, body in schema["components"]["schemas"].items()
        if name.endswith(("Response", "Body")) and "Entitlement" in name or name.startswith("Grant")
    }
    for name, body in entitlement_schemas.items():
        for forbidden in ("role", "scope", "permission", "principal", "user_id"):
            assert forbidden not in str(body.get("properties", {})).lower(), (name, forbidden)


def test_entitlement_routes_require_identity_and_an_org(client):
    """A token with no organization is refused by the frozen M9.5 dependency, before any
    entitlement code runs."""
    assert client.get(f"{PREFIX}/definitions").status_code == 401
    assert client.get(f"{PREFIX}/definitions", headers=_auth(org_id="")).status_code == 401


def test_entitlement_authorization_denial_is_403():
    with _client(authorizer=DenyAllAuthorizer()) as denied:
        assert denied.get(f"{PREFIX}/definitions", headers=_auth()).status_code == 403


def test_cross_org_isolation_over_http(client):
    account_id, _ = _http_seed(client)
    other = _auth(user_id="usr_2", org_id="org_2")
    assert client.get(f"{PREFIX}/profiles/{account_id}", headers=other).status_code == 404
    assert client.get(f"{PREFIX}/definitions", headers=other).json() == []


def test_the_organization_always_comes_from_the_token(client):
    """A body-supplied org must never be honoured."""
    account_id, _ = _http_seed(client)
    client.post(
        f"{PREFIX}/definitions",
        headers=_auth(),
        json={"code": "smuggled", "org_id": "org_evil", "organization": "org_evil"},
    )
    assert "smuggled" in {
        d["code"] for d in client.get(f"{PREFIX}/definitions", headers=_auth()).json()
    }
    other = _auth(user_id="usr_2", org_id="org_evil")
    assert client.get(f"{PREFIX}/definitions", headers=other).json() == []


def test_entitlement_openapi_is_tagged_and_documents_errors(client):
    schema = client.get("/openapi.json").json()
    for path, operations in schema["paths"].items():
        if "/capabilities" not in path:
            continue
        for operation in operations.values():
            assert operation["tags"] == ["entitlements"]
            assert "422" in operation["responses"] or "401" in operation["responses"]


def test_the_m9_5_entitlement_surface_is_untouched(client):
    """M9.5's routes still exist and still answer; the two surfaces are independent."""
    schema = client.get("/openapi.json").json()
    for path in (
        "/v1/billing/entitlements/grants",
        "/v1/billing/entitlements/snapshots/{account_id}",
        "/v1/billing/entitlements/quota/{account_id}/{entitlement_code}",
    ):
        assert path in schema["paths"], path


def test_no_capability_path_collides_with_an_m9_5_path(client):
    schema = client.get("/openapi.json").json()
    paths = list(schema["paths"])
    assert len(paths) == len(set(paths))
    assert not any(
        p.startswith("/v1/billing/entitlements/") and "/capabilities" in p for p in paths
    )


# ── 16. Studio ──────────────────────────────────────────────────────────────────

STUDIO = BILLING / "customer" / "studio" / "entitlements.html"


def test_entitlement_studio_is_served(client):
    response = client.get("/studio/entitlements.html")
    assert response.status_code == 200
    assert "Entitlements" in response.text


def test_entitlement_studio_is_self_contained():
    source = STUDIO.read_text()
    for forbidden in ("https://", "http://", "cdn.", "<script src", "@import"):
        assert forbidden not in source, forbidden


def test_entitlement_studio_has_a_screen_for_every_required_area():
    source = STUDIO.read_text()
    for screen in (
        "Gate Registry",
        "Bundles",
        "Profile",
        "Grants",
        "Snapshot",
        "Version History",
        "Why?",
        "Diagnostics",
        "Staleness",
        "Scheduled Changes",
        "Timeline",
    ):
        assert screen in source, screen


def test_entitlement_studio_owns_no_state_no_rule_and_no_money():
    """ADR-0021: the Studio is a Consumer. It renders what the API returned and decides
    nothing — including, especially, precedence."""
    source = STUDIO.read_text()
    for forbidden in (
        "localStorage",
        "sessionStorage",
        "GRANT_PRECEDENCE",
        "function resolve",
        "toFixed(2)",
        "minor",
        "currency",
    ):
        assert forbidden not in source, forbidden


def test_entitlement_studio_takes_its_lifecycle_buttons_from_the_server():
    assert "allowed_triggers" in STUDIO.read_text()


# ── 17. Architecture compliance ─────────────────────────────────────────────────

ENTITLEMENT_MODULES = (
    BILLING / "foundation" / "commercial_entitlements.py",
    BILLING / "foundation" / "entitlement_rules.py",
    BILLING / "foundation" / "entitlement_factories.py",
    BILLING / "application" / "entitlements_engine.py",
    BILLING / "persistence" / "entitlement_models.py",
    BILLING / "persistence" / "entitlement_repositories.py",
    BILLING / "customer" / "entitlement_schemas.py",
    BILLING / "customer" / "routers" / "entitlement_engine.py",
)


def _code_only(path: Path) -> str:
    """Source with docstrings stripped. These scans are about what the domain *is*, and prose
    explaining why it performs no authorization must not read as performing authorization."""
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


def test_billing_never_evaluates_a_permission():
    """The load-bearing invariant. Nothing in this domain takes a principal, a role, or a scope
    — §A11.2 RBAC keeps authorization and gains no rival (ADR-0023 §11.3).

    The Application layer is exempt from the `Permission` term only because it *requires* one
    through the frozen authorization seam; it still evaluates nothing, which the separate
    orchestrator scan below asserts."""
    for path in ENTITLEMENT_MODULES:
        code = _code_only(path)
        for forbidden in (
            "has_role",
            "check_permission",
            "is_authorized",
            "RoleAssignment",
            "RBAC",
        ):
            assert forbidden not in code, (path.name, forbidden)
    for path in (
        BILLING / "foundation" / "commercial_entitlements.py",
        BILLING / "foundation" / "entitlement_rules.py",
        BILLING / "foundation" / "entitlement_factories.py",
    ):
        code = _code_only(path)
        for forbidden in ("principal", "Permission", "scope", "actor_id=None"):
            assert forbidden not in code, (path.name, forbidden)


def test_the_application_only_requires_authorization_it_never_decides_it():
    code = _code_only(BILLING / "application" / "entitlements_engine.py")
    assert "self._authz.require(" in code
    for forbidden in ("def authorize", "AuthorizationDecision(", "allowed=True"):
        assert forbidden not in code, forbidden


def test_entitlements_own_no_money_and_compute_no_charge():
    """An allowance says how much is included; what that costs is pricing's."""
    for path in ENTITLEMENT_MODULES:
        code = _code_only(path)
        for forbidden in ("Money", "minor", ".times(", ".allocate(", ".scaled(", "PriceTier"):
            assert forbidden not in code, (path.name, forbidden)


def test_entitlements_consume_no_usage_and_own_no_ledger_or_invoice():
    for path in ENTITLEMENT_MODULES:
        code = _code_only(path)
        for forbidden in (
            "UsageRecord",
            "UsageAggregate",
            "LedgerEntry",
            "Invoice",
            "Payment",
            "Refund",
            "TaxDetermination",
            "CreditGrant",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_entitlements_depend_on_no_runtime_or_deployment_plane():
    """An *import* check rather than a substring one: `from fastapi import APIRouter` contains
    the letters "import api" and means nothing of the sort."""
    forbidden_roots = {"api", "runtime", "deployment", "knowledge", "observability", "product"}
    for path in ENTITLEMENT_MODULES:
        tree = ast.parse(path.read_text())
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
        assert not (roots & forbidden_roots), (path.name, roots & forbidden_roots)


def test_billing_never_blocks_runtime():
    """Nothing here calls out to another plane at all. An enforcing plane reads a cached
    snapshot; Billing being down cannot interrupt a conversation (ADR-0031 D3)."""
    for path in ENTITLEMENT_MODULES:
        code = _code_only(path)
        for forbidden in ("httpx", "requests.", "urllib", "websocket", "grpc"):
            assert forbidden not in code, (path.name, forbidden)


def test_there_is_no_scheduler_in_this_domain():
    for path in ENTITLEMENT_MODULES:
        code = _code_only(path)
        for forbidden in (
            "threading",
            "asyncio.sleep",
            "schedule.every",
            "cron",
            "BackgroundTasks",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_the_entitlement_foundation_stays_pure():
    for path in (
        BILLING / "foundation" / "commercial_entitlements.py",
        BILLING / "foundation" / "entitlement_rules.py",
        BILLING / "foundation" / "entitlement_factories.py",
    ):
        source = path.read_text().lower()
        for forbidden in ("sqlmodel", "sqlalchemy", "fastapi", "pydantic", "session.", "select("):
            assert forbidden not in source, (path.name, forbidden)


def test_the_entitlement_rules_read_no_clock():
    """Every temporal decision takes `at` as an argument — the property that makes a snapshot
    composed for a past instant reproducible in a test and in an audit alike."""
    source = (BILLING / "foundation" / "entitlement_rules.py").read_text()
    for forbidden in ("time.time", "datetime.now", "self._clock", "SystemClock"):
        assert forbidden not in source, forbidden


def test_entitlement_layers_import_only_downward():
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
        BILLING / "foundation" / "commercial_entitlements.py",
        BILLING / "foundation" / "entitlement_rules.py",
        BILLING / "foundation" / "entitlement_factories.py",
    ):
        assert imports(path) <= allowed, (path.name, imports(path))

    app_imports = imports(BILLING / "application" / "entitlements_engine.py")
    assert "sqlmodel" not in app_imports and "fastapi" not in app_imports


def test_entitlement_use_cases_contain_no_rule():
    source = _code_only(BILLING / "application" / "entitlements_engine.py")
    for forbidden in (
        "def validate_",
        "GRANT_LIFECYCLE",
        "GRANT_PRECEDENCE",
        "def resolve",
        "def expand",
        "def find_cycle",
        "STALE_DIRECTIVES",
        "def conflicts_with",
    ):
        assert forbidden not in source, forbidden


def test_entitlement_controllers_stay_transport_only():
    """Routes are identified structurally by their `@router.<verb>` decorator, so a new one
    cannot slip past by being named something an exclusion list allows."""
    tree = ast.parse((BILLING / "customer" / "routers" / "entitlement_engine.py").read_text())
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
        assert "commands.dispatch" in body or "queries.dispatch" in body, node.name
        for forbidden in ("repositories", "session", ".commit()", "uow.", "validate"):
            assert forbidden not in body, (node.name, forbidden)
    assert routes >= 25


def test_repositories_decide_nothing():
    source = _code_only(BILLING / "persistence" / "entitlement_repositories.py")
    for forbidden in (
        "GrantLifecycleService",
        "EntitlementPrecedenceResolver",
        "EntitlementBundleExpander",
        "EntitlementAssembler",
        "EntitlementStalenessService",
        "is_effective_at",
        "is_due",
    ):
        assert forbidden not in source, forbidden


def test_m9_2_entitlement_vocabulary_is_reused_not_duplicated():
    """Two answers to "which source wins?" is the drift §A2.5 exists to prevent — the lesson
    M9.9 learned the expensive way with `INTERVAL_SECONDS`."""
    domain = (BILLING / "foundation" / "commercial_entitlements.py").read_text()
    assert "from .entitlement import" in domain
    for reused in ("GRANT_PRECEDENCE", "GateClassification", "GrantSource"):
        assert f"class {reused}" not in domain, reused
    assert "GRANT_PRECEDENCE: dict" not in domain


def test_command_and_query_coverage_is_complete():
    from billing.application import ALL_ENTITLEMENT_COMMANDS, ALL_ENTITLEMENT_QUERIES

    wired = build_persistent_billing_application(
        ids=SequentialIdGenerator(), clock=FixedClock(), authorizer=AllowAllAuthorizer()
    )
    for message in (*ALL_ENTITLEMENT_COMMANDS, *ALL_ENTITLEMENT_QUERIES):
        bus = (
            wired.application.commands
            if message in ALL_ENTITLEMENT_COMMANDS
            else wired.application.queries
        )
        assert message in bus._handlers, message.__name__  # noqa: SLF001
    assert len(ALL_ENTITLEMENT_COMMANDS) == 19
    assert len(ALL_ENTITLEMENT_QUERIES) == 12
    wired.database.close()


def test_the_entitlement_engine_is_additive_and_optional():
    from billing.application import BillingRepositories
    from billing.application.context import BillingContext

    fields = {f.name for f in BillingRepositories.__dataclass_fields__.values()}
    assert "entitlements_engine" in fields
    assert BillingRepositories.__dataclass_fields__["entitlements_engine"].default is None
    assert BillingContext.__dataclass_fields__["entitlements_engine"].default is None


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
        "Entitlement",
        "EntitlementGate",
        "EntitlementSnapshot",
        "EntitlementGrant",
    ):
        assert name in foundation.__all__
    for name in (
        "BillingApplication",
        "CreateProduct",
        "CreatePriceBook",
        "CreateBillingAccount",
        "CreateSubscription",
        "GrantEntitlement",
        "RevokeEntitlement",
        "PublishEntitlementSnapshot",
        "GetEntitlementSnapshot",
        "GetQuotaVerdict",
    ):
        assert name in application.__all__
    for name in ("BillingDatabase", "build_persistent_billing_application"):
        assert name in persistence.__all__
    assert len(foundation.__all__) > 371  # M9.9 left it at 371; growth only


def test_the_earlier_domain_boundaries_still_hold():
    """M9.6's, M9.7's, M9.8's, and M9.9's boundaries must survive the arrival of a fifth
    domain."""
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
    for path in (
        BILLING / "foundation" / "commercial_subscriptions.py",
        BILLING / "application" / "subscriptions_engine.py",
    ):
        for forbidden in ("Money", "PriceTier"):
            assert forbidden not in _code_only(path), (path.name, forbidden)


def test_the_billing_package_layout_grew_by_addition():
    import pkgutil

    modules = {m.name for m in pkgutil.iter_modules([str(BILLING / "foundation")])}
    # M9.10 — the Entitlement Engine arrived as its own modules beside `entitlement`, never a
    # reshape of it. `entitlement.py` (M9.2) still declares the enforcement contract; these add
    # the governed registry, grants, precedence, and versioned publishing around it.
    for name in ("commercial_entitlements", "entitlement_rules", "entitlement_factories"):
        assert name in modules, name
    assert "entitlement" in modules  # the M9.2 module is still there, unreshaped


def test_billing_is_still_absent_from_the_runtime_composition_root():
    assert "billing" not in (PLATFORM / "bootstrap.py").read_text().lower()
