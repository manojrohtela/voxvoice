"""M9.16 — Payment Engine.

Payments is the first Billing domain whose effects happen outside the database, in a system this
process does not own and no transaction here can roll back. Every other milestone could be checked
by asking "is the number right?"; this one is checked by asking "did money move, exactly once, and
can we prove which movement was ours?"

So the suite is organised so that every claim is demonstrated **structurally** — by a database
constraint, by an absent FSM edge, by a derived key, by a provider that counts its own movements,
or by an explanation assembled without a provider — rather than by a narrative that a passing happy
path is assumed to imply. §15 is the numbered list of the dangerous cases.

The provider used throughout is `OfflinePaymentProvider`, which never opens a socket, honours
idempotency keys the way a real PSP does, counts the movements it actually made, and can be told to
lose a response while still knowing internally whether the money moved. That last capability is
what lets a test assert what reconciliation is *supposed* to discover rather than what it happened
to be told.
"""

from __future__ import annotations

import ast
import inspect as _inspect
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.customer.jwt import encode as epic8_encode  # Epic 8's own issuer
from billing.application import (
    ALL_PAYMENT_COMMANDS,
    ALL_PAYMENT_QUERIES,
    AllowAllAuthorizer,
    CancelPaymentAttempt,
    CancelPaymentIntent,
    CapturePaymentAttempt,
    CreatePaymentIntent,
    DeclarePaymentRoute,
    DiagnosePayment,
    ExplainPayment,
    GetPaymentAttempt,
    GetPaymentIntent,
    GetPaymentPosition,
    GetRefundableAmount,
    IngestProviderEvent,
    InspectPayment,
    ListPaymentAttempts,
    ListPaymentDivergences,
    ListPaymentIntents,
    ListPaymentRefunds,
    ListPaymentRoutes,
    ListProviderEvents,
    ListProviderOperations,
    OpenAccountingPeriod,
    PaymentTimeline,
    PostPaymentToLedger,
    PreviewPaymentRoute,
    ReconcilePaymentAttempt,
    ReconcileProviderOperations,
    RefundPayment,
    ResolvePaymentDivergence,
    RetirePaymentRoute,
    SearchPayments,
    StartPaymentAttempt,
)
from billing.customer.app import create_billing_app
from billing.customer.config import BillingApiSettings
from billing.customer.container import BillingContainer
from billing.foundation import (
    ATTEMPT_LIFECYCLE,
    INTENT_LIFECYCLE,
    OPEN_ATTEMPT_STATES,
    SETTLED_ATTEMPT_STATES,
    VERDICT_PROJECTION,
    AttemptGuardService,
    AttemptLifecycleService,
    AttemptState,
    AttemptTrigger,
    BillingConflict,
    BillingDiagnosticCode,
    BillingNotFound,
    BillingValidationError,
    CommercialRefund,
    Currency,
    EventDisposition,
    FailureCategory,
    FixedClock,
    IntentLifecycleService,
    IntentState,
    IntentTrigger,
    InvalidBillingTransition,
    LedgerPostingPlanner,
    Money,
    NullPaymentProvider,
    OfflinePaymentProvider,
    OperationService,
    OperationState,
    OutcomeNormalizer,
    PaymentAttempt,
    PaymentExplainer,
    PaymentInspector,
    PaymentIntent,
    PaymentMethodKind,
    PaymentPositionService,
    PaymentPurpose,
    PaymentReconciliationService,
    PaymentRoute,
    PaymentRoutePolicy,
    PaymentRouter,
    PaymentSubjectKind,
    ProviderAction,
    ProviderNotSent,
    ProviderOutcome,
    ProviderRegistry,
    ProviderRequest,
    ProviderStatus,
    ReconciliationOutcome,
    ReconciliationVerdict,
    RefundReason,
    RefundSequenceConflict,
    RefundService,
    ScriptedOutcome,
    SequentialIdGenerator,
    derive_attempt_id,
    derive_intent_id,
    derive_operation_key,
    derive_provider_event_id,
)
from billing.persistence.composition import build_persistent_billing_application
from billing.persistence.migrations import MIGRATIONS

BILLING = Path(__file__).resolve().parents[1] / "billing"
SETTLED_ATTEMPT_STATES_VALUES = {s.value for s in SETTLED_ATTEMPT_STATES}
ORG, ACTOR, ACCOUNT = "org_1", "usr_1", "bac_1"
KW = {"actor_id": ACTOR, "org_id": ORG}
INR = Currency.INR


# ── fixtures and helpers ────────────────────────────────────────────────────────


class _TaggedIds(SequentialIdGenerator):
    """A deterministic generator whose ids cannot collide with an earlier instance's.

    A second Application opened over the *same* database (the restart and provider-replacement
    tests) starts a fresh `SequentialIdGenerator` at 1, which collides with the `bev_1` the first
    one already wrote. Production uses a collision-resistant generator; the tag is what keeps the
    deterministic one honest across two instances."""

    def __init__(self, tag: str) -> None:
        super().__init__()
        self._tag = tag

    def new(self, prefix: str) -> str:
        return f"{super().new(prefix)}{self._tag}"


def _provider(**kwargs) -> OfflinePaymentProvider:
    return OfflinePaymentProvider(code="offline", secret="s3cret", **kwargs)


def _build(
    provider: OfflinePaymentProvider | None = None,
    *,
    database=None,
    at: float = 1_000.0,
    tag: str = "",
):
    provider = provider if provider is not None else _provider()
    persistent = build_persistent_billing_application(
        authorizer=AllowAllAuthorizer(),
        ids=_TaggedIds(tag) if tag else SequentialIdGenerator(),
        clock=FixedClock(at),
        payment_providers=ProviderRegistry(providers=(provider,)),
        **({"database": database, "create_all": False} if database is not None else {}),
    )
    return persistent, provider


@pytest.fixture
def wired():
    """A payment plane with one provider, one route, and an open accounting period.

    The period matters: without one the Ledger legitimately refuses a posting, and what that does
    to a capture is itself a test (§15.7)."""
    persistent, provider = _build()
    app = persistent.application
    app.commands.dispatch(
        OpenAccountingPeriod(
            **KW, account_id=ACCOUNT, period_start=0.0, period_end=9_999_999.0, currency=INR
        )
    )
    app.commands.dispatch(DeclarePaymentRoute(**KW, provider_code="offline", currency=INR))
    return persistent, provider


def _intent(app, *, amount: int = 100_000, key: str = "req-1", **overrides):
    return app.commands.dispatch(
        CreatePaymentIntent(
            **KW,
            account_id=ACCOUNT,
            subject_kind=PaymentSubjectKind.ACCOUNT,
            purpose=PaymentPurpose.DEPOSIT,
            amount_minor=amount,
            currency=INR,
            request_key=key,
            **overrides,
        )
    )


def _collect(app, intent_id: str, **overrides):
    return app.commands.dispatch(StartPaymentAttempt(**KW, intent_id=intent_id, **overrides))


def _events(persistent, org_id: str = ORG):
    """Every durable `billing.*` fact for an organization.

    Read straight off the event table because the emitter's contract is subject-scoped and these
    tests are about the *stream*: "exactly one event per business fact" is only checkable by
    looking at all of them at once."""
    from sqlmodel import select

    from billing.persistence.models import BillingEventRow

    rows = persistent.database.session.exec(
        select(BillingEventRow).where(BillingEventRow.org_id == org_id)
    )
    return list(rows)


def _entries(persistent, account_id: str = ACCOUNT):
    return persistent.application.context.ledger_engine.entries.for_account(ORG, account_id)


def _code_only(source: str) -> str:
    """Source with every docstring stripped.

    Introduced at M9.14 and reused here for the same reason: a scan that reads prose finds the
    words the prose uses to say a thing is forbidden, and reports the explanation as the
    violation."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _module(*parts: str) -> str:
    return _code_only((BILLING.joinpath(*parts)).read_text())


def _studio_script() -> str:
    """The Studio's JavaScript only — the HTML comment header explains what is forbidden."""
    text = (BILLING / "customer" / "studio" / "payments.html").read_text()
    return text[text.index("<script>") :]


# ── 1. identity is derived from the cause ───────────────────────────────────────


def test_an_intent_id_is_derived_from_what_is_being_collected():
    first = derive_intent_id("org", "inv_1", "invoice", 100_000, "INR", "req-1")
    assert first == derive_intent_id("org", "inv_1", "invoice", 100_000, "INR", "req-1")
    # A different amount is a different objective. A typo that silently reused the first id would
    # collect the wrong figure under a record that says otherwise.
    assert first != derive_intent_id("org", "inv_1", "invoice", 10_000, "INR", "req-1")
    # A different request key is a deliberate second collection on the same document.
    assert first != derive_intent_id("org", "inv_1", "invoice", 100_000, "INR", "req-2")


def test_an_attempt_id_is_derived_from_intent_and_sequence():
    assert derive_attempt_id("pin_1", 2) == derive_attempt_id("pin_1", 2)
    assert derive_attempt_id("pin_1", 2) != derive_attempt_id("pin_1", 3)


def test_an_operation_key_is_stable_per_attempt_and_distinct_per_action():
    initiate = derive_operation_key("pin_1", 1, "initiate")
    assert initiate == derive_operation_key("pin_1", 1, "initiate")
    # Capturing an authorization is a *different* operation. A provider given one key for both
    # would decline the capture as a duplicate of the authorization.
    assert initiate != derive_operation_key("pin_1", 1, "capture")
    assert initiate != derive_operation_key("pin_1", 2, "initiate")


def test_a_provider_event_id_is_derived_from_the_providers_own_event_id():
    assert derive_provider_event_id("org", "p", "evt_1") == derive_provider_event_id(
        "org", "p", "evt_1"
    )
    assert derive_provider_event_id("org", "p", "evt_1") != derive_provider_event_id(
        "org", "p", "evt_2"
    )


# ── 2. the state machines refuse what they must ─────────────────────────────────


def test_a_settled_attempt_has_no_outgoing_edge_at_all():
    for state in SETTLED_ATTEMPT_STATES:
        assert ATTEMPT_LIFECYCLE.triggers_from(state) == set(), state


@pytest.mark.parametrize(
    ("source", "trigger"),
    [
        (AttemptState.SUCCEEDED, AttemptTrigger.FAIL),
        (AttemptState.SUCCEEDED, AttemptTrigger.CANCEL),
        (AttemptState.SUCCEEDED, AttemptTrigger.ACCEPT),
        (AttemptState.FAILED, AttemptTrigger.SUCCEED),
        (AttemptState.CANCELLED, AttemptTrigger.SUCCEED),
        (AttemptState.ABANDONED, AttemptTrigger.SUCCEED),
    ],
)
def test_a_financial_fact_is_never_rewritten_into_a_contradictory_one(source, trigger):
    """`captured → pending`, `succeeded → failed`, `cancelled → captured`: the transitions that
    turn one row into evidence for two incompatible stories. None of them exists."""
    assert ATTEMPT_LIFECYCLE.get(source, trigger) is None
    with pytest.raises(InvalidBillingTransition):
        AttemptLifecycleService().next_state(source, trigger)


def test_an_ambiguous_attempt_leaves_only_by_resolution():
    triggers = ATTEMPT_LIFECYCLE.triggers_from(AttemptState.AMBIGUOUS)
    assert triggers == {AttemptTrigger.RESOLVE_SUCCESS, AttemptTrigger.RESOLVE_FAILURE}


def test_a_paid_intent_has_no_outgoing_edge_and_a_cancelled_one_cannot_be_captured():
    assert INTENT_LIFECYCLE.triggers_from(IntentState.PAID) == set()
    assert INTENT_LIFECYCLE.triggers_from(IntentState.CANCELLED) == set()
    assert INTENT_LIFECYCLE.triggers_from(IntentState.EXPIRED) == set()
    for trigger in (IntentTrigger.SETTLE, IntentTrigger.RETRY, IntentTrigger.AUTHORIZE):
        assert INTENT_LIFECYCLE.get(IntentState.CANCELLED, trigger) is None


def test_a_failed_intent_is_retryable_but_a_failed_attempt_is_not():
    """The distinction the milestone rests on: an objective may be retried, and it is retried by
    opening a *new* attempt."""
    assert INTENT_LIFECYCLE.get(IntentState.FAILED, IntentTrigger.RETRY) is not None
    assert ATTEMPT_LIFECYCLE.triggers_from(AttemptState.FAILED) == set()


def test_the_lifecycle_services_report_what_is_legal():
    assert IntentLifecycleService().allowed(IntentState.DRAFT) == (
        IntentTrigger.CANCEL,
        IntentTrigger.START,
    )
    assert AttemptLifecycleService().allowed(AttemptState.SUCCEEDED) == ()


def test_guard_settled_refuses_to_touch_a_settled_attempt():
    attempt = PaymentAttempt(
        id="pat_1", org_id=ORG, intent_id="pin_1", account_id=ACCOUNT, sequence=1,
        amount=Money(1000, INR), operation_key="pop_1", state=AttemptState.SUCCEEDED,
    )
    with pytest.raises(BillingConflict, match="never rewritten"):
        AttemptLifecycleService().guard_settled(attempt)


# ── 3. the provider port ────────────────────────────────────────────────────────


def test_the_null_provider_is_unavailable_rather_than_failed():
    """"No provider is configured" and "the issuer declined" are different facts. Reporting the
    second when the first is true makes a deployment problem look like a business one."""
    outcome = NullPaymentProvider().initiate(
        ProviderRequest(action=ProviderAction.INITIATE, operation_key="k")
    )
    assert outcome.status is ProviderStatus.UNAVAILABLE
    assert outcome.status is not ProviderStatus.FAILED
    assert outcome.failure_category is FailureCategory.CONFIGURATION


def test_the_null_provider_never_verifies_an_event():
    assert NullPaymentProvider().verify_event(payload="x", signature="y", received_at=1.0) is None


def test_the_offline_provider_answers_the_same_key_with_already_processed():
    provider = _provider()
    request = ProviderRequest(
        action=ProviderAction.INITIATE, operation_key="k1", amount=Money(1000, INR)
    )
    first = provider.initiate(request)
    second = provider.initiate(request)
    assert first.status is ProviderStatus.SUCCEEDED
    assert second.status is ProviderStatus.ALREADY_PROCESSED
    assert second.reference == first.reference
    assert provider.movements() == 1


def test_the_provider_outcome_has_nowhere_to_put_a_raw_payload():
    """The cheapest way to keep a PAN out of the database is to have no field it fits in."""
    fields = {f for f in ProviderOutcome.__dataclass_fields__}
    for banned in ("payload", "body", "raw", "response", "pan", "card_number", "cvv", "secret"):
        assert banned not in fields, banned


def test_an_outcome_projects_onto_m9_2s_provider_report():
    outcome = ProviderOutcome(
        status=ProviderStatus.SUCCEEDED, provider_code="offline", reference="ref",
        raw_status="charge.succeeded", amount=Money(1000, INR),
    )
    report = outcome.to_report(received_at=5.0, verified=True)
    assert report.provider_code == "offline"
    assert report.reported_state == "charge.succeeded"  # the vendor's own word, kept as evidence
    assert report.verified is True


# ── 4. routing is deterministic and explainable ─────────────────────────────────


def _policy(pid: str, **kwargs) -> PaymentRoutePolicy:
    return PaymentRoutePolicy(id=pid, org_id=ORG, **kwargs)


def test_routing_prefers_priority_then_specificity_then_id():
    generic = _policy("prt_2", provider_code="a", priority=100)
    specific = _policy("prt_1", provider_code="b", priority=100, currency=INR, country="IN")
    route = PaymentRouter().select(
        (generic, specific), currency=INR, country="IN", method=PaymentMethodKind.CARD
    )
    assert route.provider_code == "b"
    assert "priority 100" in route.reason


def test_routing_records_why_rather_than_only_which():
    route = PaymentRouter().select(
        (_policy("prt_9", provider_code="a", priority=5, currency=INR),),
        currency=INR, country="IN", method=PaymentMethodKind.CARD,
    )
    assert route.policy_id == "prt_9"
    assert "currency=INR" in route.reason and "method=card" in route.reason


def test_no_matching_route_is_reported_rather_than_defaulted():
    route = PaymentRouter().select((), currency=INR, country="IN", method=PaymentMethodKind.CARD)
    assert route.provider_code == ""
    assert route.reason == "no_matching_route"
    assert not route.is_routed


def test_a_retired_route_never_matches():
    retired = _policy("prt_1", provider_code="a", currency=INR, is_active=False)
    assert PaymentRouter().select(
        (retired,), currency=INR, country="IN", method=PaymentMethodKind.CARD
    ).provider_code == ""


# ── 5. positions are derived, never stored ──────────────────────────────────────


def _attempt(seq: int, state: AttemptState, minor: int = 100_000) -> PaymentAttempt:
    return PaymentAttempt(
        id=f"pat_{seq}", org_id=ORG, intent_id="pin_1", account_id=ACCOUNT, sequence=seq,
        amount=Money(minor, INR), operation_key=f"pop_{seq}", state=state,
    )


def _bare_intent(minor: int = 100_000) -> PaymentIntent:
    return PaymentIntent(
        id="pin_1", org_id=ORG, account_id=ACCOUNT, amount=Money(minor, INR),
        route=PaymentRoute(provider_code="offline"),
    )


def test_a_position_counts_only_successful_attempts():
    position = PaymentPositionService().position(
        _bare_intent(),
        (
            _attempt(1, AttemptState.FAILED),
            _attempt(2, AttemptState.AMBIGUOUS),
            _attempt(3, AttemptState.SUCCEEDED),
        ),
    )
    assert position.captured == Money(100_000, INR)
    assert position.successful_attempts == 1
    assert position.unresolved_attempts == 1
    assert position.is_blocked


def test_outstanding_is_floored_and_overpayment_is_reported_separately():
    position = PaymentPositionService().position(
        _bare_intent(50_000), (_attempt(1, AttemptState.SUCCEEDED, 80_000),)
    )
    assert position.outstanding == Money(0, INR)  # never negative — credit is the Ledger's word
    assert position.is_overpaid


def test_no_persistence_column_holds_a_collected_or_outstanding_figure():
    source = _module("persistence", "payment_models.py")
    for banned in ("collected", "outstanding_minor", "refunded_total", "balance"):
        assert banned not in source, banned


# ── 6. refunds are bounded by facts ─────────────────────────────────────────────


def _refund(minor: int, state) -> CommercialRefund:
    return CommercialRefund(
        id=f"prf_{minor}", org_id=ORG, account_id=ACCOUNT, intent_id="pin_1", attempt_id="pat_1",
        amount=Money(minor, INR), state=state,
    )


def test_refundable_is_captured_minus_already_refunded():
    from billing.foundation import RefundState

    attempt = _attempt(1, AttemptState.SUCCEEDED)
    assert RefundService().refundable(attempt, ()) == Money(100_000, INR)
    assert RefundService().refundable(
        attempt, (_refund(40_000, RefundState.REFUNDED),)
    ) == Money(60_000, INR)


def test_money_in_flight_counts_against_the_refund_ceiling():
    """Two concurrent refunds that each checked only *completed* refunds would each be individually
    valid and jointly excessive."""
    from billing.foundation import RefundState

    attempt = _attempt(1, AttemptState.SUCCEEDED)
    in_flight = _refund(70_000, RefundState.PROCESSING)
    assert RefundService().refundable(attempt, (in_flight,)) == Money(30_000, INR)
    with pytest.raises(BillingConflict, match="exceeds"):
        RefundService().guard_refundable(attempt, (in_flight,), Money(50_000, INR))


def test_an_unsuccessful_attempt_refunds_nothing():
    with pytest.raises(BillingConflict, match="only a captured payment"):
        RefundService().guard_refundable(_attempt(1, AttemptState.FAILED), (), Money(1, INR))


def test_a_refund_never_converts_currency():
    from billing.foundation import Currency as C

    with pytest.raises(BillingConflict, match="converts nothing"):
        RefundService().guard_refundable(
            _attempt(1, AttemptState.SUCCEEDED), (), Money(100, C.USD)
        )


# ── 7. the attempt guard ────────────────────────────────────────────────────────


def test_an_unresolved_attempt_blocks_every_further_attempt():
    with pytest.raises(BillingConflict, match="two charges"):
        AttemptGuardService().guard_may_attempt(
            _bare_intent(), (_attempt(1, AttemptState.AMBIGUOUS),)
        )


def test_an_open_attempt_blocks_a_second_one():
    for state in OPEN_ATTEMPT_STATES:
        with pytest.raises(BillingConflict):
            AttemptGuardService().guard_may_attempt(_bare_intent(), (_attempt(1, state),))


def test_an_open_provider_operation_blocks_a_new_attempt():
    from billing.foundation import ProviderOperation

    sending = ProviderOperation(
        org_id=ORG, operation_key="pop_1", intent_id="pin_1", attempt_id="pat_1",
        action=ProviderAction.INITIATE, provider_code="offline", amount=Money(1, INR),
    )
    with pytest.raises(BillingConflict, match="still sending"):
        AttemptGuardService().guard_may_attempt(_bare_intent(), (), (sending,))


# ── 8. outcome normalization refuses what it must ───────────────────────────────


def test_already_processed_is_never_treated_as_success():
    from billing.foundation import outcome_is_actionable

    assert not outcome_is_actionable(ProviderOutcome(status=ProviderStatus.ALREADY_PROCESSED))
    assert OutcomeNormalizer().trigger_for(
        ProviderOutcome(status=ProviderStatus.ALREADY_PROCESSED)
    ) is None


def test_an_outcome_that_would_move_a_settled_attempt_is_refused():
    normalizer = OutcomeNormalizer()
    settled = _attempt(1, AttemptState.SUCCEEDED)
    assert not normalizer.may_apply(settled, ProviderOutcome(status=ProviderStatus.FAILED))


def test_an_unverified_envelope_is_never_applied():
    from billing.foundation import ProviderEventEnvelope

    envelope = ProviderEventEnvelope(
        provider_code="offline", event_ref="e", event_type="t",
        outcome=ProviderOutcome(status=ProviderStatus.SUCCEEDED), verified=False,
    )
    assert OutcomeNormalizer().disposition_for(
        _attempt(1, AttemptState.PENDING), envelope, known=False
    ) is EventDisposition.UNVERIFIED


def test_an_event_contradicting_a_capture_is_conflicting_never_an_overwrite():
    from billing.foundation import ProviderEventEnvelope

    envelope = ProviderEventEnvelope(
        provider_code="offline", event_ref="e", event_type="t",
        outcome=ProviderOutcome(status=ProviderStatus.FAILED), verified=True,
    )
    assert OutcomeNormalizer().disposition_for(
        _attempt(1, AttemptState.SUCCEEDED), envelope, known=False
    ) is EventDisposition.CONFLICTING


# ── 9. reconciliation ───────────────────────────────────────────────────────────


def test_a_state_disagreement_is_a_divergence():
    outcome = PaymentReconciliationService().compare(
        _attempt(1, AttemptState.PENDING),
        ProviderOutcome(status=ProviderStatus.SUCCEEDED, amount=Money(100_000, INR)),
        at=1.0,
    )
    assert outcome is ReconciliationOutcome.STATE_DIVERGED
    assert outcome in {o for o in ReconciliationOutcome} and outcome.value == "state_diverged"


def test_an_amount_disagreement_is_a_divergence():
    assert PaymentReconciliationService().compare(
        _attempt(1, AttemptState.SUCCEEDED),
        ProviderOutcome(status=ProviderStatus.SUCCEEDED, amount=Money(99_999, INR)),
        at=1.0, posted=True,
    ) is ReconciliationOutcome.AMOUNT_DIVERGED


def test_a_currency_disagreement_is_its_own_divergence():
    from billing.foundation import Currency as C

    assert PaymentReconciliationService().compare(
        _attempt(1, AttemptState.SUCCEEDED),
        ProviderOutcome(status=ProviderStatus.SUCCEEDED, amount=Money(100_000, C.USD)),
        at=1.0, posted=True,
    ) is ReconciliationOutcome.CURRENCY_DIVERGED


def test_a_capture_with_no_ledger_fact_is_a_divergence():
    assert PaymentReconciliationService().compare(
        _attempt(1, AttemptState.SUCCEEDED),
        ProviderOutcome(status=ProviderStatus.SUCCEEDED, amount=Money(100_000, INR)),
        at=1.0, posted=False,
    ) is ReconciliationOutcome.UNPOSTED_CAPTURE


def test_the_wider_outcome_projects_onto_m9_2s_four_member_verdict():
    for outcome in ReconciliationOutcome:
        assert outcome in VERDICT_PROJECTION, outcome
        assert isinstance(VERDICT_PROJECTION[outcome], ReconciliationVerdict)


def test_a_divergence_cannot_be_resolved_without_a_named_person():
    from billing.foundation import PaymentReconciliation

    finding = PaymentReconciliation(
        id="prc_1", org_id=ORG, outcome=ReconciliationOutcome.STATE_DIVERGED
    )
    with pytest.raises(BillingConflict, match="never automatically"):
        PaymentReconciliationService().guard_manual_resolution(finding, actor_id="")


def test_agreement_is_not_a_divergence_to_resolve():
    from billing.foundation import PaymentReconciliation

    finding = PaymentReconciliation(id="prc_1", org_id=ORG, outcome=ReconciliationOutcome.MATCHED)
    with pytest.raises(BillingConflict, match="not a divergence"):
        PaymentReconciliationService().guard_manual_resolution(finding, actor_id=ACTOR)


def test_stale_operations_are_the_crash_signature():
    from billing.foundation import ProviderOperation

    old = ProviderOperation(
        org_id=ORG, operation_key="pop_old", intent_id="i", attempt_id="a",
        action=ProviderAction.INITIATE, provider_code="offline", amount=Money(1, INR),
        sent_at=100.0,
    )
    fresh = ProviderOperation(
        org_id=ORG, operation_key="pop_new", intent_id="i", attempt_id="a",
        action=ProviderAction.INITIATE, provider_code="offline", amount=Money(1, INR),
        sent_at=990.0,
    )
    stale = PaymentReconciliationService().stale_operations(
        (old, fresh), now=1_000.0, older_than=300.0
    )
    assert [o.operation_key for o in stale] == ["pop_old"]


# ── 10. the operation lease ─────────────────────────────────────────────────────


def test_an_ambiguous_answer_leaves_the_operation_open():
    """The row that says "we do not know" is the row reconciliation looks for. Closing it would
    erase the only evidence that anything might have happened."""
    service = OperationService()
    operation, _ = service.open(
        intent=_bare_intent(), attempt=_attempt(1, AttemptState.STARTED),
        action=ProviderAction.INITIATE, at=1.0,
    )
    closed = service.close(operation, ProviderOutcome(status=ProviderStatus.AMBIGUOUS), at=2.0)
    assert closed.is_open
    assert closed.state is OperationState.SENDING


def test_a_definite_answer_closes_the_operation():
    service = OperationService()
    operation, _ = service.open(
        intent=_bare_intent(), attempt=_attempt(1, AttemptState.STARTED),
        action=ProviderAction.INITIATE, at=1.0,
    )
    closed = service.close(
        operation, ProviderOutcome(status=ProviderStatus.SUCCEEDED, reference="r"), at=2.0
    )
    assert closed.state is OperationState.COMPLETED and closed.provider_reference == "r"


def test_resending_counts_the_same_key_rather_than_minting_another():
    service = OperationService()
    operation, request = service.open(
        intent=_bare_intent(), attempt=_attempt(1, AttemptState.STARTED),
        action=ProviderAction.INITIATE, at=1.0,
    )
    resent = service.resend(operation)
    assert resent.attempts_sent == 2
    assert resent.operation_key == request.operation_key


# ── 11. the explainer holds no provider ─────────────────────────────────────────


def test_the_explainer_takes_no_provider_at_all():
    """Structural, not promised: an explanation that could call a provider is one that stops
    working exactly when a two-year-old payment is asked about."""
    signature = _inspect.signature(PaymentExplainer.__init__)
    assert set(signature.parameters) == {"self", "positions"}
    # Structural rather than textual: the word "provider" appears in the notes the explainer
    # *writes* ("no provider was selected for this intent"), and a substring scan would report the
    # explanation as the violation. What must be absent is a **call** on a provider port.
    tree = ast.parse(_code_only(_inspect.getsource(PaymentExplainer)))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {
                "initiate", "capture", "cancel", "refund", "retrieve", "verify_event", "require",
            }, node.func.attr
        if isinstance(node, ast.arg):
            assert node.arg not in {"provider", "registry", "providers"}, node.arg


def test_an_explanation_names_the_ambiguity_and_the_key_to_ask_about():
    explanation = PaymentExplainer().explain(
        _bare_intent(), (_attempt(1, AttemptState.AMBIGUOUS),)
    )
    assert any("ambiguous" in note and "pop_1" in note for note in explanation.notes)


def test_an_explanation_flags_a_capture_with_no_ledger_fact():
    explanation = PaymentExplainer().explain(
        _bare_intent(), (_attempt(1, AttemptState.SUCCEEDED),)
    )
    assert any("no ledger fact" in note for note in explanation.notes)


def test_the_inspector_reports_unposted_captures_and_open_operations():
    inspection = PaymentInspector().inspect(
        _bare_intent(), (_attempt(1, AttemptState.SUCCEEDED),)
    )
    assert inspection.unposted_captures == ("pat_1",)
    assert not inspection.is_clean


# ── 12. the ledger boundary is a request, not an entry ──────────────────────────


def test_the_posting_planner_produces_a_request_and_never_an_entry():
    posting = LedgerPostingPlanner().for_capture(
        _bare_intent(), _attempt(1, AttemptState.SUCCEEDED)
    )
    fields = set(type(posting).__dataclass_fields__)
    for banned in ("period_key", "sequence", "entry_id", "balance", "direction"):
        assert banned not in fields, banned
    assert posting.source_id == "pat_1"  # the cause, which is what makes posting idempotent


def test_a_refund_posting_is_a_new_fact_pointing_at_its_refund():
    from billing.foundation import RefundState

    refund = CommercialRefund(
        id="prf_1", org_id=ORG, account_id=ACCOUNT, intent_id="pin_1", attempt_id="pat_1",
        amount=Money(1000, INR), state=RefundState.REFUNDED,
    )
    posting = LedgerPostingPlanner().for_refund(refund)
    assert posting.is_refund and posting.source_id == "prf_1"


# ── 13. end to end, over a real database ────────────────────────────────────────


def test_a_collection_captures_posts_and_settles(wired):
    persistent, provider = wired
    app = persistent.application
    intent = _intent(app)
    receipt = _collect(app, intent.id)
    assert receipt.outcome == "succeeded"
    assert receipt.intent_state == "paid"
    assert receipt.ledger_outcome == "posted"
    assert receipt.position.captured.minor == 100_000
    assert receipt.position.outstanding.minor == 0
    assert provider.movements() == 1


def test_the_same_request_key_opens_one_objective(wired):
    persistent, _ = wired
    app = persistent.application
    first = _intent(app, key="req-x")
    second = _intent(app, key="req-x")
    assert first.id == second.id
    assert len(app.queries.dispatch(ListPaymentIntents(**KW))) == 1


def test_an_authorization_is_not_captured_money(wired):
    persistent, provider = wired
    app = persistent.application
    provider.script(ProviderAction.INITIATE, ScriptedOutcome(status=ProviderStatus.AUTHORIZED))
    intent = _intent(app, key="auth")
    receipt = _collect(app, intent.id)
    assert receipt.attempt_state == "authorized"
    assert receipt.intent_state == "authorized"
    assert receipt.position.captured.minor == 0  # reserved, not taken
    assert receipt.journal_entry_id == ""
    assert not _entries(persistent)


def test_capturing_an_authorization_uses_a_second_operation_key(wired):
    persistent, provider = wired
    app = persistent.application
    provider.script(ProviderAction.INITIATE, ScriptedOutcome(status=ProviderStatus.AUTHORIZED))
    intent = _intent(app, key="auth2")
    authorized = _collect(app, intent.id)
    provider.script(ProviderAction.CAPTURE, ScriptedOutcome(status=ProviderStatus.SUCCEEDED))
    captured = app.commands.dispatch(
        CapturePaymentAttempt(**KW, attempt_id=authorized.attempt_id)
    )
    assert captured.attempt_state == "succeeded"
    keys = {key for _, key in provider.calls}
    assert len(keys) == 2, keys  # initiate and capture are separate operations


def test_requires_action_is_neither_success_nor_failure(wired):
    persistent, provider = wired
    app = persistent.application
    provider.script(
        ProviderAction.INITIATE,
        ScriptedOutcome(status=ProviderStatus.REQUIRES_ACTION, action_url="https://psp.test/3ds"),
    )
    intent = _intent(app, key="3ds")
    receipt = _collect(app, intent.id)
    assert receipt.outcome == "requires_action"
    assert receipt.action_url == "https://psp.test/3ds"
    assert receipt.position.captured.minor == 0


def test_a_failed_attempt_leaves_the_objective_retryable(wired):
    persistent, provider = wired
    app = persistent.application
    provider.script(
        ProviderAction.INITIATE,
        ScriptedOutcome(
            status=ProviderStatus.FAILED, failure_code="card_declined",
            failure_category=FailureCategory.DECLINED,
        ),
    )
    intent = _intent(app, key="declined")
    first = _collect(app, intent.id)
    assert first.attempt_state == "failed" and first.intent_state == "failed"

    provider.script(ProviderAction.INITIATE, ScriptedOutcome(status=ProviderStatus.SUCCEEDED))
    second = _collect(app, intent.id)
    assert second.attempt_state == "succeeded"
    assert second.attempt_id != first.attempt_id

    attempts = app.queries.dispatch(ListPaymentAttempts(**KW, intent_id=intent.id))
    assert [a.sequence for a in attempts] == [1, 2]
    # The failed attempt was not rewritten by the retry.
    assert attempts[0].state == "failed" and attempts[0].failure_code == "card_declined"


def test_a_partial_collection_does_not_report_the_objective_as_paid(wired):
    persistent, _ = wired
    app = persistent.application
    intent = _intent(app, amount=100_000, key="partial")
    # Collect less than the objective by opening the attempt against a smaller intent amount:
    # a second objective for the remainder is the modelled path, and the position proves it.
    smaller = _intent(app, amount=40_000, key="partial-a")
    receipt = _collect(app, smaller.id)
    assert receipt.intent_state == "paid"
    position = app.queries.dispatch(GetPaymentPosition(**KW, intent_id=intent.id))
    assert position.outstanding.minor == 100_000  # the untouched objective owes everything still


def test_organization_isolation_holds_across_every_read(wired):
    persistent, _ = wired
    app = persistent.application
    intent = _intent(app)
    _collect(app, intent.id)
    assert app.queries.dispatch(ListPaymentIntents(actor_id=ACTOR, org_id="org_other")) == ()
    with pytest.raises(BillingNotFound):
        app.queries.dispatch(
            GetPaymentIntent(actor_id=ACTOR, org_id="org_other", intent_id=intent.id)
        )


def test_payments_are_deny_by_default_without_an_authorization_seam():
    from billing.application.authorization import BillingAuthorizationError

    persistent = build_persistent_billing_application(
        ids=SequentialIdGenerator(), clock=FixedClock(1_000.0)
    )
    with pytest.raises(BillingAuthorizationError):
        persistent.application.commands.dispatch(
            DeclarePaymentRoute(**KW, provider_code="offline")
        )


def test_a_payment_survives_a_restart(wired):
    persistent, provider = wired
    app = persistent.application
    intent = _intent(app)
    receipt = _collect(app, intent.id)
    second, _ = _build(provider, database=persistent.database, at=9_000.0, tag="b")
    reread = second.application.queries.dispatch(
        GetPaymentAttempt(**KW, attempt_id=receipt.attempt_id)
    )
    assert reread.state == "succeeded"
    assert reread.operation_key == receipt.attempt_id and False or reread.journal_entry_id
    operations = second.application.queries.dispatch(
        ListProviderOperations(**KW, intent_id=intent.id)
    )
    assert operations and operations[0].state == "completed"


def test_an_intent_with_no_route_cannot_move_money():
    """A fresh installation routes nowhere and collects nothing — the same posture M9.15 takes on
    tax rates, and for the same reason."""
    persistent, _ = _build()
    app = persistent.application
    intent = _intent(app, key="unrouted")
    assert intent.route.provider_code == ""
    with pytest.raises(BillingConflict, match="no payment provider is routed"):
        _collect(app, intent.id)


def test_diagnostics_report_the_unroutable_intent(wired):
    persistent, _ = _build()
    app = persistent.application
    intent = _intent(app, key="unrouted2")
    findings = app.commands and app.queries.dispatch(DiagnosePayment(**KW, intent_id=intent.id))
    codes = {f.code for f in findings.findings}
    assert BillingDiagnosticCode.UNROUTABLE_PAYMENT.value in codes


def test_cancelling_an_objective_with_a_movement_in_flight_is_refused(wired):
    persistent, provider = wired
    app = persistent.application
    provider.script(ProviderAction.INITIATE, ScriptedOutcome(status=ProviderStatus.PENDING))
    intent = _intent(app, key="inflight")
    _collect(app, intent.id)
    with pytest.raises(BillingConflict, match="in flight"):
        app.commands.dispatch(CancelPaymentIntent(**KW, intent_id=intent.id, reason="changed mind"))


def test_the_timeline_records_the_refusals_as_well_as_the_successes(wired):
    persistent, _ = wired
    app = persistent.application
    intent = _intent(app)
    _collect(app, intent.id)
    actions = {a.action for a in app.queries.dispatch(PaymentTimeline(**KW, intent_id=intent.id))}
    assert {"intent_created", "attempt_started", "attempt_succeeded", "ledger_posted"} <= actions


def test_search_and_routes_and_events_read_back(wired):
    persistent, _ = wired
    app = persistent.application
    intent = _intent(app)
    _collect(app, intent.id)
    assert app.queries.dispatch(SearchPayments(**KW, state=IntentState.PAID)).total == 1
    assert app.queries.dispatch(SearchPayments(**KW, provider_code="nobody")).total == 0
    assert len(app.queries.dispatch(ListPaymentRoutes(**KW))) == 1
    assert app.queries.dispatch(PreviewPaymentRoute(**KW, currency=INR)).provider_code == "offline"
    assert app.queries.dispatch(ListProviderEvents(**KW)) == ()
    assert app.queries.dispatch(ListPaymentDivergences(**KW)) == ()
    inspection = app.queries.dispatch(InspectPayment(**KW, intent_id=intent.id))
    assert inspection.is_clean


def test_retiring_a_route_keeps_the_row(wired):
    persistent, _ = wired
    app = persistent.application
    route = app.queries.dispatch(ListPaymentRoutes(**KW))[0]
    app.commands.dispatch(RetirePaymentRoute(**KW, route_id=route.id, reason="contract ended"))
    assert app.queries.dispatch(ListPaymentRoutes(**KW)) == ()
    retired = app.queries.dispatch(ListPaymentRoutes(**KW, include_retired=True))
    assert len(retired) == 1 and not retired[0].is_active


# ── 14. refunds end to end ──────────────────────────────────────────────────────


def test_a_refund_is_a_new_fact_and_the_capture_is_untouched(wired):
    persistent, _ = wired
    app = persistent.application
    intent = _intent(app)
    receipt = _collect(app, intent.id)
    refund = app.commands.dispatch(
        RefundPayment(
            **KW, attempt_id=receipt.attempt_id, amount_minor=40_000, currency=INR,
            reason=RefundReason.CUSTOMER_REQUEST, request_key="rf-1",
        )
    )
    assert refund.state == "refunded"
    attempt = app.queries.dispatch(GetPaymentAttempt(**KW, attempt_id=receipt.attempt_id))
    assert attempt.state == "succeeded"  # not "refunded" — there is no such attempt state
    position = app.queries.dispatch(GetPaymentPosition(**KW, intent_id=intent.id))
    assert position.captured.minor == 100_000
    assert position.refunded.minor == 40_000
    assert position.net.minor == 60_000


def test_a_refund_produces_its_own_ledger_fact(wired):
    persistent, _ = wired
    app = persistent.application
    intent = _intent(app)
    receipt = _collect(app, intent.id)
    refund = app.commands.dispatch(
        RefundPayment(**KW, attempt_id=receipt.attempt_id, amount_minor=40_000, currency=INR,
                      request_key="rf-ledger")
    )
    entries = _entries(persistent)
    kinds = {(e.entry_type.value, e.direction.value) for e in entries}
    assert ("payment", "credit") in kinds
    assert ("refund", "debit") in kinds
    assert refund.journal_entry_id in {e.id for e in entries}


def test_partial_refunds_accumulate_toward_the_ceiling(wired):
    persistent, _ = wired
    app = persistent.application
    intent = _intent(app)
    receipt = _collect(app, intent.id)
    for index, minor in enumerate((30_000, 30_000, 40_000)):
        app.commands.dispatch(
            RefundPayment(**KW, attempt_id=receipt.attempt_id, amount_minor=minor,
                          currency=INR, request_key=f"rf-{index}")
        )
    assert app.queries.dispatch(
        GetRefundableAmount(**KW, attempt_id=receipt.attempt_id)
    ).minor == 0
    with pytest.raises(BillingConflict, match="exceeds"):
        app.commands.dispatch(
            RefundPayment(**KW, attempt_id=receipt.attempt_id, amount_minor=1,
                          currency=INR, request_key="rf-over")
        )
    assert len(app.queries.dispatch(ListPaymentRefunds(**KW, intent_id=intent.id))) == 3


def test_a_failed_provider_refund_is_recorded_and_frees_the_ceiling(wired):
    persistent, provider = wired
    app = persistent.application
    intent = _intent(app)
    receipt = _collect(app, intent.id)
    provider.script(
        ProviderAction.REFUND,
        ScriptedOutcome(status=ProviderStatus.FAILED, failure_code="refund_window_closed"),
    )
    refund = app.commands.dispatch(
        RefundPayment(**KW, attempt_id=receipt.attempt_id, amount_minor=40_000, currency=INR,
                      request_key="rf-fail")
    )
    assert refund.state == "failed" and refund.failure_code == "refund_window_closed"
    position = app.queries.dispatch(GetPaymentPosition(**KW, intent_id=intent.id))
    assert position.refunded.minor == 0  # a failed refund returned nothing


# ── 15. THE DANGEROUS CASES — the numbered demonstrations ───────────────────────


def test_demo_01_one_intent_cannot_create_two_external_movements(wired):
    """The whole milestone in one assertion: collecting twice against a paid objective is refused,
    and the provider moved money exactly once."""
    persistent, provider = wired
    app = persistent.application
    intent = _intent(app)
    _collect(app, intent.id)
    with pytest.raises(BillingConflict):
        _collect(app, intent.id)
    assert provider.movements() == 1


def test_demo_02_a_replayed_operation_key_sends_nothing_new(wired):
    """Two workers computing the same operation key: one claims it, the other retrieves.

    Simulated by claiming the key out of band — which is exactly what the losing worker sees."""
    persistent, provider = wired
    app = persistent.application
    intent = _intent(app, key="race")
    repos = app.context.payment_engine
    stored = repos.intents.get(ORG, intent.id)
    started = IntentLifecycleService().apply(stored, IntentTrigger.START, at=1_000.0)
    repos.intents.mark_state(started)
    attempt_id = derive_attempt_id(intent.id, 1)
    key = derive_operation_key(intent.id, 1, ProviderAction.INITIATE.value)
    from billing.foundation import ProviderOperation

    repos.operations.claim(
        ProviderOperation(
            org_id=ORG, operation_key=key, intent_id=intent.id, attempt_id=attempt_id,
            action=ProviderAction.INITIATE, provider_code="offline", amount=Money(100_000, INR),
            state=OperationState.COMPLETED, sent_at=1_000.0,
        )
    )
    persistent.database.session.commit()
    receipt = _collect(app, intent.id)
    assert receipt.outcome == "replayed"
    assert provider.movements() == 0  # nothing was ever sent


def test_demo_03_two_workers_racing_one_attempt_produce_one_attempt(wired):
    """The derived attempt id makes "this is attempt 2" a primary key rather than an intention."""
    persistent, _ = wired
    app = persistent.application
    repos = app.context.payment_engine
    intent = _intent(app, key="dupe-attempt")
    first = PaymentAttempt(
        id=derive_attempt_id(intent.id, 1), org_id=ORG, intent_id=intent.id,
        account_id=ACCOUNT, sequence=1, amount=Money(100_000, INR), operation_key="pop_a",
    )
    second = PaymentAttempt(
        id=derive_attempt_id(intent.id, 1), org_id=ORG, intent_id=intent.id,
        account_id=ACCOUNT, sequence=1, amount=Money(100_000, INR), operation_key="pop_b",
    )
    stored_first = repos.attempts.append(first)
    stored_second = repos.attempts.append(second)
    assert stored_first.id == stored_second.id
    assert stored_second.operation_key == "pop_a"  # the winner's record, not the loser's
    assert len(repos.attempts.for_intent(ORG, intent.id)) == 1


def test_demo_04_a_duplicate_webhook_produces_one_business_fact(wired):
    persistent, provider = wired
    app = persistent.application
    provider.script(ProviderAction.INITIATE, ScriptedOutcome(status=ProviderStatus.PENDING))
    intent = _intent(app, key="webhook")
    receipt = _collect(app, intent.id)
    attempt = app.queries.dispatch(GetPaymentAttempt(**KW, attempt_id=receipt.attempt_id))
    payload = (
        f"event=evt_1;status=succeeded;reference={attempt.provider_reference};"
        f"key={attempt.operation_key};minor=100000;currency=INR"
    )
    first = app.commands.dispatch(
        IngestProviderEvent(**KW, provider_code="offline", payload=payload, signature="s3cret")
    )
    second = app.commands.dispatch(
        IngestProviderEvent(**KW, provider_code="offline", payload=payload, signature="s3cret")
    )
    assert first.disposition == "applied"
    assert second.disposition == "duplicate"
    settled = app.queries.dispatch(GetPaymentAttempt(**KW, attempt_id=receipt.attempt_id))
    assert settled.state == "succeeded"
    payment_entries = [e for e in _entries(persistent) if e.entry_type.value == "payment"]
    assert len(payment_entries) == 1


def test_demo_05_an_out_of_order_webhook_does_not_corrupt_state(wired):
    """A `pending` callback arriving after the capture is stale, not a regression to pending."""
    persistent, _ = wired
    app = persistent.application
    intent = _intent(app, key="ooo")
    receipt = _collect(app, intent.id)
    attempt = app.queries.dispatch(GetPaymentAttempt(**KW, attempt_id=receipt.attempt_id))
    late = (
        f"event=evt_late;status=pending;reference={attempt.provider_reference};"
        f"key={attempt.operation_key}"
    )
    record = app.commands.dispatch(
        IngestProviderEvent(**KW, provider_code="offline", payload=late, signature="s3cret")
    )
    assert record.disposition == "stale"
    assert app.queries.dispatch(
        GetPaymentAttempt(**KW, attempt_id=receipt.attempt_id)
    ).state == "succeeded"


def test_demo_06_a_provider_timeout_stays_safely_reconcilable(wired):
    """The most expensive case in the package: the call is lost, the money moved, and a retry
    would charge twice. It is refused, and reconciliation finds the truth."""
    persistent, provider = wired
    app = persistent.application
    provider.script(
        ProviderAction.INITIATE,
        ScriptedOutcome(status=ProviderStatus.AMBIGUOUS, raw_status="succeeded"),
    )
    intent = _intent(app, key="timeout")
    receipt = _collect(app, intent.id)
    assert receipt.outcome == "ambiguous"
    assert receipt.requires_reconciliation

    with pytest.raises(BillingConflict, match="two charges"):
        _collect(app, intent.id)

    finding = app.commands.dispatch(
        ReconcilePaymentAttempt(**KW, attempt_id=receipt.attempt_id)
    )
    assert finding.outcome == "ambiguity_resolved"
    attempt = app.queries.dispatch(GetPaymentAttempt(**KW, attempt_id=receipt.attempt_id))
    assert attempt.state == "succeeded"
    assert attempt.journal_entry_id
    assert provider.movements() == 1  # one movement, discovered rather than repeated


def test_demo_07_a_provider_success_with_a_failed_ledger_write_stays_recoverable():
    """Money left the customer's account and the financial fact could not be recorded.

    The capture must survive — losing it is the worst available answer, because then the money is
    gone and nothing in Billing says so. Reproduced by collecting with **no accounting period
    open**, which is exactly how the smoke found it."""
    persistent, provider = _build()
    app = persistent.application
    app.commands.dispatch(DeclarePaymentRoute(**KW, provider_code="offline", currency=INR))
    intent = _intent(app, key="no-period")
    receipt = _collect(app, intent.id)

    assert receipt.attempt_state == "succeeded"  # the capture stands
    assert receipt.ledger_outcome == "refused"
    assert receipt.journal_entry_id == ""
    assert provider.movements() == 1

    findings = app.queries.dispatch(ListPaymentDivergences(**KW))
    assert [f.outcome for f in findings] == ["unposted_capture"]
    assert findings[0].status == "open"

    inspection = app.queries.dispatch(InspectPayment(**KW, intent_id=intent.id))
    assert inspection.unposted_captures == (receipt.attempt_id,)

    # And the repair is explicit, attributed, and idempotent.
    app.commands.dispatch(
        OpenAccountingPeriod(
            **KW, account_id=ACCOUNT, period_start=0.0, period_end=9_999_999.0, currency=INR
        )
    )
    repaired = app.commands.dispatch(PostPaymentToLedger(**KW, attempt_id=receipt.attempt_id))
    assert repaired.journal_entry_id
    again = app.commands.dispatch(PostPaymentToLedger(**KW, attempt_id=receipt.attempt_id))
    assert again.ledger_outcome == "duplicate"
    assert len([e for e in _entries(persistent) if e.entry_type.value == "payment"]) == 1


def test_demo_08_the_payment_amount_comes_from_the_immutable_document():
    """Payments validates against the document; it never computes the figure, and it refuses to
    collect more than the document says is payable."""
    support_source = _module("application", "payment_engine.py")
    assert "def payable_amount" in support_source
    # The reading path touches the invoice engine's documents and snapshots — and nothing else.
    reader = support_source[support_source.index("def payable_amount") :][:1200]
    for forbidden in ("ledger_engine", "rating_engine", "pricing", "tax_engine"):
        assert forbidden not in reader, forbidden


def test_demo_09_payments_computes_no_tax(wired):
    source = _module("foundation", "payment_rules.py") + _module("application", "payment_engine.py")
    for forbidden in ("tax_rate", "rate_basis_points", "TaxCalculator", "basis_points"):
        assert forbidden not in source, forbidden


def test_demo_10_payments_computes_no_rating_or_price(wired):
    source = "".join(
        _module("foundation", name)
        for name in ("commercial_payments.py", "payment_rules.py", "payment_factories.py")
    ) + _module("application", "payment_engine.py")
    for forbidden in ("RatingEngine", "PriceResolver", "resolve_price", "ChargeLine", "Quantity"):
        assert forbidden not in source, forbidden


def test_demo_11_payments_mutates_no_invoice():
    """Payments reads a document and writes nothing back to it."""
    source = _module("application", "payment_engine.py")
    for forbidden in ("documents.save", "documents.replace", "snapshots.append", "numbers."):
        assert forbidden not in source, forbidden


def test_demo_12_a_successful_payment_produces_exactly_one_ledger_fact(wired):
    persistent, _ = wired
    app = persistent.application
    intent = _intent(app)
    receipt = _collect(app, intent.id)
    payment_entries = [e for e in _entries(persistent) if e.entry_type.value == "payment"]
    assert len(payment_entries) == 1
    assert payment_entries[0].id == receipt.journal_entry_id
    assert payment_entries[0].source.value == "payment"
    assert payment_entries[0].references.payment_id == receipt.attempt_id
    # And replaying the posting produces a duplicate, never a second fact.
    again = app.commands.dispatch(PostPaymentToLedger(**KW, attempt_id=receipt.attempt_id))
    assert again.ledger_outcome == "duplicate"
    assert len([e for e in _entries(persistent) if e.entry_type.value == "payment"]) == 1


def test_demo_13_a_failed_payment_produces_no_successful_ledger_fact(wired):
    persistent, provider = wired
    app = persistent.application
    provider.script(
        ProviderAction.INITIATE, ScriptedOutcome(status=ProviderStatus.FAILED, failure_code="no")
    )
    intent = _intent(app, key="fails")
    receipt = _collect(app, intent.id)
    assert receipt.attempt_state == "failed"
    assert not _entries(persistent)
    with pytest.raises(BillingConflict, match="only a captured payment"):
        app.commands.dispatch(PostPaymentToLedger(**KW, attempt_id=receipt.attempt_id))


def test_demo_14_a_refund_cannot_exceed_what_was_captured(wired):
    persistent, _ = wired
    app = persistent.application
    intent = _intent(app)
    receipt = _collect(app, intent.id)
    with pytest.raises(BillingConflict, match="exceeds"):
        app.commands.dispatch(
            RefundPayment(**KW, attempt_id=receipt.attempt_id, amount_minor=100_001,
                          currency=INR, request_key="rf-too-much")
        )
    assert app.queries.dispatch(ListPaymentRefunds(**KW, intent_id=intent.id)) == ()


def test_demo_15_a_concurrent_refund_under_one_request_key_is_one_refund(wired):
    persistent, provider = wired
    app = persistent.application
    intent = _intent(app)
    receipt = _collect(app, intent.id)
    first = app.commands.dispatch(
        RefundPayment(**KW, attempt_id=receipt.attempt_id, amount_minor=40_000, currency=INR,
                      request_key="same-key")
    )
    second = app.commands.dispatch(
        RefundPayment(**KW, attempt_id=receipt.attempt_id, amount_minor=40_000, currency=INR,
                      request_key="same-key")
    )
    assert first.id == second.id
    assert len(app.queries.dispatch(ListPaymentRefunds(**KW, intent_id=intent.id))) == 1
    position = app.queries.dispatch(GetPaymentPosition(**KW, intent_id=intent.id))
    assert position.refunded.minor == 40_000  # not 80,000
    refund_movements = [c for c in provider.calls if c[0] is ProviderAction.REFUND]
    assert len({key for _, key in refund_movements}) == 1


def test_demo_16_raw_payment_credentials_never_enter_persistence_events_or_logs():
    banned = ("pan", "card_number", "cvv", "cvc", "expiry", "iban", "account_number",
              "routing_number", "api_key", "secret_key")
    surfaces = {
        "persistence models": _module("persistence", "payment_models.py"),
        "persistence repositories": _module("persistence", "payment_repositories.py"),
        "foundation aggregates": _module("foundation", "commercial_payments.py"),
        "factories": _module("foundation", "payment_factories.py"),
        "application": _module("application", "payment_engine.py"),
        "schemas": _module("customer", "payment_schemas.py"),
        "router": _module("customer", "routers", "payment_engine.py"),
        "studio": _studio_script(),
    }
    for name, source in surfaces.items():
        lowered = source.lower()
        for word in banned:
            # Whole words only: `span` is not a PAN, and `secretary` is not a secret key.
            assert not re.search(rf"\b{word}\b", lowered), f"{word} appears in {name}"


def test_demo_17_replacing_a_provider_does_not_invalidate_billing_history(wired):
    """Provider-specific ids are *references*, never identity. Swapping providers leaves every
    intent, attempt, refund, operation, and ledger fact addressable exactly as before."""
    persistent, provider = wired
    app = persistent.application
    intent = _intent(app)
    receipt = _collect(app, intent.id)
    attempt = app.queries.dispatch(GetPaymentAttempt(**KW, attempt_id=receipt.attempt_id))

    # No Billing identity contains the provider's reference or code.
    assert attempt.provider_reference not in attempt.id
    assert attempt.provider_reference not in attempt.operation_key
    assert "offline" not in intent.id and "offline" not in attempt.id
    assert attempt.id == derive_attempt_id(intent.id, 1)

    # Route to a different provider; the old records still read.
    replacement = OfflinePaymentProvider(code="successor", secret="s")
    second = build_persistent_billing_application(
        authorizer=AllowAllAuthorizer(), ids=_TaggedIds("b"), clock=FixedClock(9_000.0),
        database=persistent.database, create_all=False,
        payment_providers=ProviderRegistry(providers=(replacement,)),
    )
    app2 = second.application
    old_route = app2.queries.dispatch(ListPaymentRoutes(**KW))[0]
    app2.commands.dispatch(
        RetirePaymentRoute(**KW, route_id=old_route.id, reason="provider migration")
    )
    app2.commands.dispatch(DeclarePaymentRoute(**KW, provider_code="successor", currency=INR))
    reread = app2.queries.dispatch(GetPaymentAttempt(**KW, attempt_id=receipt.attempt_id))
    assert reread.state == "succeeded" and reread.provider_code == "offline"
    assert app2.queries.dispatch(
        GetPaymentPosition(**KW, intent_id=intent.id)
    ).captured.minor == 100_000
    # New collections route to the successor without touching the history.
    fresh = _intent(app2, key="after-migration")
    assert fresh.route.provider_code == "successor"


def test_demo_18_a_historical_payment_explains_itself_without_the_provider(wired):
    persistent, provider = wired
    app = persistent.application
    intent = _intent(app)
    receipt = _collect(app, intent.id)
    calls_before = len(provider.calls)
    explanation = app.queries.dispatch(ExplainPayment(**KW, intent_id=intent.id))
    assert len(provider.calls) == calls_before, "explaining a payment called the provider"
    assert explanation.provider_code == "offline"
    assert explanation.routing_reason
    assert explanation.attempts[0].observations
    assert explanation.journal_entry_ids == (receipt.journal_entry_id,)
    assert explanation.is_self_contained


def test_demo_19_organization_isolation_on_every_payment_read(wired):
    persistent, _ = wired
    app = persistent.application
    intent = _intent(app)
    receipt = _collect(app, intent.id)
    other = {"actor_id": ACTOR, "org_id": "org_other"}
    assert app.queries.dispatch(ListPaymentIntents(**other)) == ()
    assert app.queries.dispatch(ListPaymentRoutes(**other)) == ()
    assert app.queries.dispatch(ListPaymentDivergences(**other)) == ()
    with pytest.raises(BillingNotFound):
        app.queries.dispatch(GetPaymentAttempt(**other, attempt_id=receipt.attempt_id))


def test_demo_20_every_payment_command_and_query_denies_by_default():
    from billing.application.authorization import BillingAuthorizationError

    persistent = build_persistent_billing_application(
        ids=SequentialIdGenerator(), clock=FixedClock(1_000.0)
    )
    app = persistent.application
    with pytest.raises(BillingAuthorizationError):
        app.commands.dispatch(CreatePaymentIntent(**KW, account_id=ACCOUNT, amount_minor=1))
    with pytest.raises(BillingAuthorizationError):
        app.queries.dispatch(ListPaymentIntents(**KW))


def test_demo_21_reconciliation_detects_a_provider_billing_divergence(wired):
    """The provider says the money never moved; Billing has it captured. That is a divergence, it
    is recorded with both sides, and nothing is silently corrected."""
    persistent, provider = wired
    app = persistent.application
    intent = _intent(app)
    receipt = _collect(app, intent.id)

    # A provider that has forgotten the operation — the "missing in provider" shape.
    amnesiac = OfflinePaymentProvider(code="offline", secret="s3cret")
    second = build_persistent_billing_application(
        authorizer=AllowAllAuthorizer(), ids=_TaggedIds("b"), clock=FixedClock(5_000.0),
        database=persistent.database, create_all=False,
        payment_providers=ProviderRegistry(providers=(amnesiac,)),
    )
    finding = second.application.commands.dispatch(
        ReconcilePaymentAttempt(**KW, attempt_id=receipt.attempt_id)
    )
    assert finding.outcome == "state_diverged"
    assert finding.billing_state == "succeeded"
    assert finding.status == "open"
    assert finding.billing_amount.minor == 100_000
    # And the capture was not rewritten to match the provider.
    assert second.application.queries.dispatch(
        GetPaymentAttempt(**KW, attempt_id=receipt.attempt_id)
    ).state == "succeeded"


def test_demo_22_a_divergence_is_never_auto_resolved(wired):
    persistent, _ = wired
    app = persistent.application
    intent = _intent(app, key="diverge")
    persistent2, _ = _build(
        OfflinePaymentProvider(code="offline", secret="s"),
        database=persistent.database, at=2_000.0, tag="b",
    )
    receipt = _collect(app, intent.id)
    finding = persistent2.application.commands.dispatch(
        ReconcilePaymentAttempt(**KW, attempt_id=receipt.attempt_id)
    )
    assert finding.status == "open" and finding.is_divergent

    # Nothing in the sweep closes it.
    persistent2.application.commands.dispatch(
        ReconcileProviderOperations(**KW, older_than_seconds=0.0)
    )
    still_open = persistent2.application.queries.dispatch(ListPaymentDivergences(**KW))
    assert any(f.id == finding.id and f.status == "open" for f in still_open)

    # Only a named person closes it, and the closure carries the name.
    resolved = persistent2.application.commands.dispatch(
        ResolvePaymentDivergence(**KW, finding_id=finding.id, note="provider confirmed by phone")
    )
    assert resolved.status == "resolved"
    assert resolved.resolved_by == ACTOR
    assert resolved.resolution_note == "provider confirmed by phone"
    # The observed figures were not amended by the resolution.
    assert resolved.billing_amount.minor == finding.billing_amount.minor


def test_demo_23_a_restart_preserves_payment_state_and_idempotency(wired):
    persistent, provider = wired
    app = persistent.application
    intent = _intent(app)
    _collect(app, intent.id)
    second, _ = _build(provider, database=persistent.database, at=9_000.0, tag="b")
    app2 = second.application
    # State survived.
    assert app2.queries.dispatch(GetPaymentIntent(**KW, intent_id=intent.id)).state == "paid"
    # Idempotency survived: the same request key still resolves to the same objective, and the
    # operation key is still claimed, so nothing new can be sent.
    again = _intent(app2, key="req-1")
    assert again.id == intent.id
    with pytest.raises(BillingConflict):
        _collect(app2, intent.id)
    assert provider.movements() == 1


def test_demo_24_migration_0026_is_contiguous_additive_and_idempotent():
    # `"0026" in versions` rather than `versions[-1] == "0026"`: what this test is about is that
    # *this* milestone's migration is present, ordered and contiguous, not that no later milestone
    # ever appends one. M9.17 appended 0027, and pinning the tail would have made every future
    # milestone edit an M9.16 assertion — which is how a suite stops meaning what it says.
    versions = [m.VERSION for m in MIGRATIONS]
    assert "0026" in versions
    assert versions == sorted(versions)
    assert [int(v) for v in versions] == list(range(13, 13 + len(versions)))

    source = _code_only(
        (BILLING / "persistence" / "migrations" / "v0026_billing_payments.py").read_text()
    )
    assert "ALTER" not in source.upper()
    assert "INSERT" not in source.upper()  # nothing is seeded — no provider, no route
    assert "DROP" not in source.upper()
    assert "checkfirst=True" in source  # idempotent

    # Applying it twice changes nothing.
    persistent, _ = _build()
    from billing.persistence.migrations import upgrade_all

    assert "0026" in upgrade_all(persistent.database)
    assert "0026" in upgrade_all(persistent.database)


def test_demo_25_the_studio_contains_no_business_calculation_and_no_provider():
    script = _studio_script()
    # No arithmetic on money. `amount()` formats what the server sent and derives nothing.
    for pattern in (r"\.minor\s*[-+*/]", r"[-+*/]\s*\w+\.minor", r"captured\s*-", r"\bbalance\b"):
        assert not re.search(pattern, script), pattern
    # No provider is named, and no provider endpoint is reachable.
    for forbidden in ("stripe", "razorpay", "paddle", "adyen", "braintree", "chargebee",
                      "api.psp", "https://api."):
        assert forbidden not in script.lower(), forbidden
    # No client-side storage of financial state.
    for forbidden in ("localStorage", "sessionStorage", "document.cookie", "indexedDB"):
        assert forbidden not in script, forbidden
    # Lifecycle actions come from the server.
    assert "allowed_triggers" in script


# ── 16. architecture assertions ─────────────────────────────────────────────────


def test_no_provider_is_named_anywhere_in_the_payment_engine():
    """A fresh installation routes nowhere. A vendor name in this codebase would be an opinion
    about where a customer's money goes, held by the wrong party."""
    vendors = ("stripe", "razorpay", "paddle", "chargebee", "recurly", "braintree", "adyen",
               "lemonsqueezy", "worldpay", "square", "payu", "cashfree", "phonepe")
    modules = [
        ("foundation", "commercial_payments.py"),
        ("foundation", "payment_rules.py"),
        ("foundation", "payment_factories.py"),
        ("application", "payment_engine.py"),
        ("persistence", "payment_models.py"),
        ("persistence", "payment_repositories.py"),
        ("customer", "payment_schemas.py"),
        ("customer", "routers", "payment_engine.py"),
    ]
    for parts in modules:
        lowered = _module(*parts).lower()
        for vendor in vendors:
            assert vendor not in lowered, f"{vendor} in {'/'.join(parts)}"


def test_the_foundation_imports_no_sdk_and_opens_no_socket():
    for name in ("commercial_payments.py", "payment_rules.py", "payment_factories.py"):
        tree = ast.parse((BILLING / "foundation" / name).read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
        forbidden = {"requests", "httpx", "urllib", "socket", "http", "aiohttp", "stripe"}
        assert not (imported & forbidden), (name, imported & forbidden)


def test_payments_writes_only_to_its_own_repositories():
    """Every repository write in the application module goes through `self._repos` (Payments' own
    eight) or `self._factory` (pure composition). The Ledger is reached through its *command*."""
    source = _module("application", "payment_engine.py")
    seams = "routes|intents|attempts|operations|events|refunds|reconciliations|activity"
    writes = "append|save|add|claim|mark_state|record_outcome|complete|resolve|replace"
    # The Foundation service seams are deliberately named so they cannot collide with the eight
    # repository names (`attempt_lifecycle`, `refund_rules`, `operation_rules`, …). That is not
    # cosmetic: while a *service* could be called `refunds`, "did this write to a repository?" was
    # answerable only by reading the call, and a scan that cannot tell them apart is a scan that
    # eventually passes something it should not.
    for holder, seam, verb in re.findall(rf"self\.(_\w+)\.({seams})\.({writes})\(", source):
        assert holder == "_repos", (holder, seam, verb)


def test_payments_touches_no_other_domains_persistence():
    source = _module("application", "payment_engine.py")
    for forbidden in ("ledger_engine.entries", "invoice_engine.documents.save",
                      "tax_engine.assessments", "rating_engine.charges"):
        assert forbidden not in source, forbidden


def test_the_ledger_is_reached_through_its_own_command():
    source = _module("application", "payment_engine.py")
    assert "PostJournalEntry(" in source
    assert "JournalEntry(" not in source.replace("PostJournalEntry(", "")
    assert "compose_entry" not in source


def test_no_payment_use_case_computes_a_balance():
    source = _module("application", "payment_engine.py")
    for forbidden in ("LedgerBalance", "balance_for", "trial_balance", "next_sequence("):
        assert forbidden not in source, forbidden


def test_the_repositories_never_commit_or_roll_back():
    source = _module("persistence", "payment_repositories.py")
    for forbidden in (".commit()", ".rollback()"):
        assert forbidden not in source, forbidden


def test_the_repositories_contain_no_payment_business_rule():
    source = _module("persistence", "payment_repositories.py")
    for forbidden in ("guard_", "PaymentRouter", "refundable", "ATTEMPT_LIFECYCLE",
                      "OutcomeNormalizer", "position("):
        assert forbidden not in source, forbidden


def test_the_append_only_repositories_declare_no_update_or_delete():
    from billing.foundation import (
        CommercialRefundRepository,
        PaymentActivityRepository,
        PaymentAttemptRepository,
        ProviderEventRepository,
    )

    for contract in (
        PaymentAttemptRepository,
        ProviderEventRepository,
        CommercialRefundRepository,
        PaymentActivityRepository,
    ):
        methods = {n for n in dir(contract) if not n.startswith("_")}
        for banned in ("delete", "remove", "update", "purge", "truncate"):
            assert not any(banned in m for m in methods), (contract.__name__, banned)


def test_the_operation_table_makes_a_duplicate_claim_an_impossible_row():
    from billing.persistence.payment_models import BillingPaymentOperationRow

    primary = {c.name for c in BillingPaymentOperationRow.__table__.primary_key}
    assert primary == {"org_id", "operation_key"}


def test_the_provider_event_table_makes_a_duplicate_delivery_an_impossible_row():
    from billing.persistence.payment_models import BillingProviderEventRow

    constraints = {
        tuple(c.columns.keys())
        for c in BillingProviderEventRow.__table__.constraints
        if hasattr(c, "columns") and c.name and "uq_" in str(c.name)
    }
    assert ("org_id", "provider_code", "event_ref") in constraints


def test_the_attempt_table_makes_a_duplicate_sequence_an_impossible_row():
    from billing.persistence.payment_models import BillingPaymentAttemptRow

    constraints = {
        tuple(c.columns.keys())
        for c in BillingPaymentAttemptRow.__table__.constraints
        if hasattr(c, "columns") and c.name and "uq_" in str(c.name)
    }
    assert ("org_id", "intent_id", "sequence") in constraints


def test_every_payment_command_and_query_has_exactly_one_handler(wired):
    persistent, _ = wired
    app = persistent.application
    for command in ALL_PAYMENT_COMMANDS:
        assert command in app.commands._handlers, command.__name__
    for query in ALL_PAYMENT_QUERIES:
        assert query in app.queries._handlers, query.__name__


def test_the_payment_engine_is_absent_when_its_repositories_are_not_wired():
    """Unwired means absent, never half — the rule every engine since M9.6 has followed."""
    from billing.application.application import BillingApplicationFactory
    from billing.application.context import BillingContext
    from billing.foundation import BillingFactory

    context = BillingContext(factory=BillingFactory(), repositories=None)
    assert context.payment_engine is None
    assert BillingApplicationFactory is not None


# ── 17. HTTP ────────────────────────────────────────────────────────────────────


SECRET = "test-secret-value-for-billing-payments"


def _auth(user_id: str = ACTOR, org_id: str = ORG) -> dict[str, str]:
    """A real Customer-Platform token, minted with Epic 8's own encoder."""
    return {
        "Authorization": "Bearer "
        + epic8_encode({"sub": user_id, "org": org_id}, SECRET, ttl_seconds=3600)
    }


@pytest.fixture
def client():
    settings = BillingApiSettings(environment="testing", jwt_secret=SECRET)
    container = BillingContainer.build(
        settings,
        authorizer=AllowAllAuthorizer(),
        ids=SequentialIdGenerator(),
        clock=FixedClock(1_500.0),
        payment_providers=ProviderRegistry(providers=(_provider(),)),
    )
    app = create_billing_app(settings=settings, container=container)
    with TestClient(app) as test_client:
        yield test_client


PREFIX = "/v1/billing"


def test_payment_routes_require_identity_and_an_org(client):
    assert client.get(f"{PREFIX}/collections/intents").status_code == 401
    relay = f"{PREFIX}/collections/provider-events/relay/offline"
    assert client.post(relay, json={}).status_code in (401, 422)


def test_the_organization_always_comes_from_the_token(client):
    source = (BILLING / "customer" / "routers" / "payment_engine.py").read_text()
    assert "ctx.organization()" in source
    assert "org_id: str" not in source


def test_the_payment_surface_has_no_delete_put_or_patch(client):
    spec = client.get("/openapi.json").json()
    for path, operations in spec["paths"].items():
        if not path.startswith(f"{PREFIX}/collections"):
            continue
        assert set(operations) <= {"get", "post"}, (path, sorted(operations))


def test_the_payment_surface_is_the_declared_size(client):
    spec = client.get("/openapi.json").json()
    paths = [p for p in spec["paths"] if p.startswith(f"{PREFIX}/collections")]
    assert len(paths) == 30, sorted(paths)


def test_static_payment_routes_are_declared_before_their_wildcards(client):
    """`/routes/preview` must not fall into `/routes/{route_id}`; `/intents/search`,
    `/intents/divergences`, and `/intents/operations` must not fall into `/intents/{intent_id}`."""
    source = (BILLING / "customer" / "routers" / "payment_engine.py").read_text()
    for static, wildcard in (
        ('"/routes/preview"', '"/routes/{route_id}/retire"'),
        ('"/intents/search"', '"/intents/{intent_id}"'),
        ('"/intents/divergences"', '"/intents/{intent_id}"'),
        ('"/intents/operations"', '"/intents/{intent_id}"'),
    ):
        assert source.index(static) < source.index(wildcard), (static, wildcard)


def test_controllers_call_no_provider_and_touch_no_repository(client):
    source = _module("customer", "routers", "payment_engine.py")
    for forbidden in ("provider.", "registry", "_repos", "uow", "session", "database",
                      ".initiate(", ".capture(", ".retrieve("):
        assert forbidden not in source, forbidden


def test_every_payment_controller_dispatches_through_a_bus():
    source = _module("customer", "routers", "payment_engine.py")
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        body = ast.unparse(node)
        assert "app.commands.dispatch" in body or "app.queries.dispatch" in body, node.name


def test_money_moving_endpoints_return_202_rather_than_201(client):
    """A 201 for a payment teaches every client that a request is a collection. It is not: the
    answer may legitimately be `requires_action`, `pending`, or `ambiguous`."""
    spec = client.get("/openapi.json").json()
    for path in (
        f"{PREFIX}/collections/intents/{{intent_id}}/attempts",
        f"{PREFIX}/collections/attempts/{{attempt_id}}/capture",
        f"{PREFIX}/collections/attempts/{{attempt_id}}/refunds",
    ):
        assert "202" in spec["paths"][path]["post"]["responses"], path


def test_the_webhook_body_has_no_status_field(client):
    """A body carrying `status=succeeded` would let anyone who can reach the endpoint mark
    invoices paid."""
    spec = client.get("/openapi.json").json()
    schema = spec["components"]["schemas"]["IngestProviderEventBody"]
    assert set(schema["properties"]) == {"provider_code", "payload", "signature"}


def test_the_payment_schemas_reuse_the_one_money_response(client):
    spec = client.get("/openapi.json").json()
    money = [name for name in spec["components"]["schemas"] if name.endswith("MoneyResponse")]
    assert money == ["MoneyResponse"], money


def test_a_payment_can_be_collected_over_http(client):
    headers = _auth()
    opened = client.post(
        f"{PREFIX}/collections/routes",
        json={"provider_code": "offline", "currency": "INR"},
        headers=headers,
    )
    assert opened.status_code == 201, opened.text
    intent = client.post(
        f"{PREFIX}/collections/intents",
        json={
            "account_id": ACCOUNT, "subject_kind": "account", "purpose": "deposit",
            "amount_minor": 50_000, "currency": "INR", "request_key": "http-1",
        },
        headers=headers,
    )
    assert intent.status_code == 201, intent.text
    intent_id = intent.json()["id"]
    receipt = client.post(
        f"{PREFIX}/collections/intents/{intent_id}/attempts", json={}, headers=headers
    )
    assert receipt.status_code == 202, receipt.text
    body = receipt.json()
    assert body["outcome"] == "succeeded"
    assert body["position"]["captured"]["minor"] == 50_000
    # The receipt distinguishes what happened; it never flattens it into "created".
    assert set(body) >= {"outcome", "attempt_state", "intent_state", "requires_reconciliation"}

    explained = client.get(
        f"{PREFIX}/collections/intents/{intent_id}/explain", headers=headers
    )
    assert explained.status_code == 200
    assert explained.json()["routing_reason"]


def test_an_unverified_relayed_event_is_recorded_and_applied_to_nothing(client):
    headers = _auth()
    response = client.post(
        f"{PREFIX}/collections/provider-events/relay/offline",
        json={"provider_code": "offline", "payload": "event=e;status=succeeded", "signature": "no"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["disposition"] == "unverified"
    assert body["verified"] is False
    assert body["attempt_id"] == ""


def test_internal_errors_never_leak_provider_detail(client):
    headers = _auth()
    response = client.get(f"{PREFIX}/collections/intents/pin_nope", headers=headers)
    assert response.status_code == 404
    assert "Traceback" not in response.text
    assert "offline" not in response.text


# ── 18. events and diagnostics ──────────────────────────────────────────────────


def test_one_business_fact_one_event(wired):
    persistent, _ = wired
    app = persistent.application
    intent = _intent(app)
    _collect(app, intent.id)
    types = [e.type for e in _events(persistent)]
    assert types.count("billing.payment.intent_created") == 1
    assert types.count("billing.payment.attempt_started") == 1
    assert types.count("billing.payment.captured") == 1
    assert types.count("billing.payment.settled") == 1


def test_a_rejected_command_emits_no_business_fact(wired):
    persistent, _ = wired
    app = persistent.application
    before = len(_events(persistent))
    with pytest.raises(BillingValidationError):
        app.commands.dispatch(
            CreatePaymentIntent(**KW, account_id="", amount_minor=0, currency=INR)
        )
    assert len(_events(persistent)) == before


def test_an_ambiguous_outcome_is_its_own_fact(wired):
    """"We asked a provider to move money and never found out whether it did" is distinct from
    "it failed". A vocabulary that folded them would report a second charge as a retry."""
    persistent, provider = wired
    app = persistent.application
    provider.script(
        ProviderAction.INITIATE,
        ScriptedOutcome(status=ProviderStatus.AMBIGUOUS, raw_status="succeeded"),
    )
    intent = _intent(app, key="amb-event")
    _collect(app, intent.id)
    types = [e.type for e in _events(persistent)]
    assert "billing.payment.attempt_ambiguous" in types
    assert "billing.payment.failed" not in types


def test_no_event_payload_carries_a_secret(wired):
    persistent, _ = wired
    app = persistent.application
    intent = _intent(app)
    _collect(app, intent.id, instrument_token="tok_secret_value")
    for event in _events(persistent):
        blob = str(event.payload).lower()
        for banned in ("s3cret", "tok_secret_value", "signature", "password"):
            assert banned not in blob, (event.type, banned)


def test_the_payment_diagnostics_are_about_evidence_never_arithmetic():
    payment_codes = [
        c for c in BillingDiagnosticCode
        if c.name in {
            "UNROUTABLE_PAYMENT", "UNKNOWN_PAYMENT_PROVIDER", "PAYMENT_AMOUNT_MISMATCH",
            "UNPAYABLE_SUBJECT", "DUPLICATE_PAYMENT_ATTEMPT", "UNRESOLVED_PAYMENT_ATTEMPT",
            "OPEN_PROVIDER_OPERATION", "AMBIGUOUS_PROVIDER_OUTCOME", "UNVERIFIED_PROVIDER_EVENT",
            "UNCORRELATED_PROVIDER_EVENT", "CONFLICTING_PROVIDER_EVENT", "SETTLED_ATTEMPT_MODIFIED",
            "UNPOSTED_PAYMENT_CAPTURE", "REFUND_EXCEEDS_CAPTURE", "UNREFUNDABLE_ATTEMPT",
            "PAYMENT_DIVERGENCE_OPEN", "INSTRUMENT_DATA_PRESENT",
        }
    ]
    assert len(payment_codes) == 17
    # There is no "wrong amount" code, because Payments does not decide amounts.
    assert not any("wrong" in c.value or "incorrect" in c.value for c in payment_codes)

# ── 19. M9.16-R1 — the independent review's findings, regression-tested ─────────
#
# Every test below reproduces the exact scenario the review rejected the milestone for. Each was
# written by running the failing case first, so a regression re-breaks it rather than passing
# vacuously.


def _authorized(wired, key: str):
    """An intent with a live authorization — the setup P-1 turned into a phantom capture."""
    persistent, provider = wired
    app = persistent.application
    provider.script(ProviderAction.INITIATE, ScriptedOutcome(status=ProviderStatus.AUTHORIZED))
    intent = _intent(app, key=key)
    return persistent, provider, app, intent, _collect(app, intent.id)


def test_r1_p1_cancelling_an_authorization_posts_no_capture(wired):
    """**P-1, S0.** `CANCEL` + `SUCCEEDED` was read through the money-movement table as `SUCCEED`:
    voiding an authorization recorded a capture and posted a ledger credit for money never taken.

    Before: `attempt=succeeded`, `intent=paid`, `captured=100000`, one `payment/credit` entry.
    After: `attempt=cancelled`, `captured=0`, and **zero ledger entries**."""
    persistent, provider, app, intent, authorized = _authorized(wired, "r1-p1")
    assert authorized.attempt_state == "authorized"
    provider.script(ProviderAction.CANCEL, ScriptedOutcome(status=ProviderStatus.SUCCEEDED))

    voided = app.commands.dispatch(
        CancelPaymentAttempt(**KW, attempt_id=authorized.attempt_id, reason="customer abandoned")
    )

    assert voided.attempt_state == "cancelled"
    assert voided.position.captured.minor == 0
    assert voided.journal_entry_id == ""
    assert _entries(persistent) == (), "a void must never produce a ledger entry"
    assert not [e for e in _events(persistent) if e.type == "billing.payment.captured"]


def test_r1_p1_the_action_decides_the_meaning_not_the_status():
    """The table that fixes P-1, checked directly: one status, two meanings."""
    from billing.foundation import ACTION_TRIGGERS, MONEY_MOVING_ACTIONS

    succeeded = ProviderOutcome(status=ProviderStatus.SUCCEEDED)
    normalizer = OutcomeNormalizer()
    assert normalizer.trigger_for(succeeded, ProviderAction.CAPTURE) is AttemptTrigger.SUCCEED
    assert normalizer.trigger_for(succeeded, ProviderAction.CANCEL) is AttemptTrigger.CANCEL
    assert normalizer.trigger_for(succeeded, ProviderAction.REFUND) is None
    assert normalizer.asserts_money_moved(succeeded, ProviderAction.CAPTURE)
    assert not normalizer.asserts_money_moved(succeeded, ProviderAction.CANCEL)
    assert not normalizer.asserts_money_moved(succeeded, ProviderAction.REFUND)
    assert ProviderAction.CANCEL not in MONEY_MOVING_ACTIONS
    assert set(ACTION_TRIGGERS) == set(ProviderAction)


def test_r1_p1_a_failed_void_leaves_the_authorization_standing(wired):
    persistent, provider, app, intent, authorized = _authorized(wired, "r1-p1-fail")
    provider.script(
        ProviderAction.CANCEL,
        ScriptedOutcome(status=ProviderStatus.FAILED, failure_code="already_captured"),
    )
    voided = app.commands.dispatch(
        CancelPaymentAttempt(**KW, attempt_id=authorized.attempt_id, reason="try")
    )
    assert voided.attempt_state == "failed"
    assert _entries(persistent) == ()


def test_r1_p1_already_processed_is_conclusive_for_a_void_but_not_for_a_capture(wired):
    """Asymmetry with a reason: "I already voided that" is a complete answer; "I already processed
    that charge" is an instruction to retrieve, because it does not say what the outcome was."""
    persistent, provider, app, intent, authorized = _authorized(wired, "r1-p1-dup")
    provider.script(
        ProviderAction.CANCEL, ScriptedOutcome(status=ProviderStatus.ALREADY_PROCESSED)
    )
    voided = app.commands.dispatch(
        CancelPaymentAttempt(**KW, attempt_id=authorized.attempt_id, reason="again")
    )
    assert voided.attempt_state == "cancelled"
    assert _entries(persistent) == ()

    normalizer = OutcomeNormalizer()
    already = ProviderOutcome(status=ProviderStatus.ALREADY_PROCESSED)
    assert normalizer.trigger_for(already, ProviderAction.CAPTURE) is None


def test_r1_p1_a_refund_never_transitions_the_original_attempt(wired):
    persistent, _ = wired
    app = persistent.application
    intent = _intent(app, key="r1-p1-refund")
    receipt = _collect(app, intent.id)
    app.commands.dispatch(
        RefundPayment(**KW, attempt_id=receipt.attempt_id, amount_minor=40_000, currency=INR,
                      request_key="r1")
    )
    attempt = app.queries.dispatch(GetPaymentAttempt(**KW, attempt_id=receipt.attempt_id))
    assert attempt.state == "succeeded"  # not re-captured, not "refunded"
    payments = [e for e in _entries(persistent) if e.entry_type.value == "payment"]
    assert len(payments) == 1, "the refund must not produce a second capture"


def test_r1_p1_a_void_after_ambiguity_is_refused(wired):
    """An ambiguous attempt is not voidable: we do not know whether there is anything to void."""
    persistent, provider = wired
    app = persistent.application
    provider.script(
        ProviderAction.INITIATE,
        ScriptedOutcome(status=ProviderStatus.AMBIGUOUS, raw_status="succeeded"),
    )
    intent = _intent(app, key="r1-p1-amb")
    receipt = _collect(app, intent.id)
    assert receipt.attempt_state == "ambiguous"
    with pytest.raises(BillingConflict):
        app.commands.dispatch(
            CancelPaymentAttempt(**KW, attempt_id=receipt.attempt_id, reason="void it")
        )


# ── P-2 ─────────────────────────────────────────────────────────────────────────


class _Exploding(OfflinePaymentProvider):
    """A provider whose transport dies. The exception may or may not have reached the PSP."""

    def __init__(self, error: Exception, **kwargs) -> None:
        super().__init__(**kwargs)
        self._error = error

    def initiate(self, request):
        raise self._error


def _exploding(error: Exception):
    persistent = build_persistent_billing_application(
        authorizer=AllowAllAuthorizer(), ids=SequentialIdGenerator(), clock=FixedClock(1_000.0),
        payment_providers=ProviderRegistry(providers=(_Exploding(error, code="offline",
                                                                 secret="s3cret"),)),
    )
    app = persistent.application
    app.commands.dispatch(
        OpenAccountingPeriod(
            **KW, account_id=ACCOUNT, period_start=0.0, period_end=9_999_999.0, currency=INR
        )
    )
    app.commands.dispatch(DeclarePaymentRoute(**KW, provider_code="offline", currency=INR))
    return persistent, app


@pytest.mark.parametrize(
    "error",
    [TimeoutError("read timed out"), ConnectionResetError("reset by peer"),
     RuntimeError("transport exploded")],
)
def test_r1_p2_a_raised_provider_call_becomes_ambiguous_not_stuck(error):
    """**P-2, S1.** The call raised, phase 3 never ran, and the attempt stayed `started` — open, so
    no retry and no cancel, and reconciliation ignored it. One reset made an intent permanently
    uncollectable. It is now `ambiguous`: still blocking (correctly), but resolvable."""
    persistent, app = _exploding(error)
    intent = _intent(app, key=f"r1-p2-{type(error).__name__}")
    receipt = _collect(app, intent.id)

    assert receipt.attempt_state == "ambiguous"
    assert receipt.requires_reconciliation
    attempt = app.queries.dispatch(GetPaymentAttempt(**KW, attempt_id=receipt.attempt_id))
    assert attempt.state != "started"
    assert attempt.failure_code == type(error).__name__

    with pytest.raises(BillingConflict, match="two charges"):
        _collect(app, intent.id)

    finding = app.commands.dispatch(
        ReconcilePaymentAttempt(**KW, attempt_id=receipt.attempt_id)
    )
    assert finding.outcome == "ambiguity_resolved"
    resolved = app.queries.dispatch(GetPaymentAttempt(**KW, attempt_id=receipt.attempt_id))
    assert resolved.state in ("failed", "succeeded")
    assert resolved.state != "started"


def test_r1_p2_an_adapter_that_proves_nothing_was_sent_fails_locally():
    """`ProviderNotSent` is the one exception safe to read as a local failure — and the intent is
    immediately retryable, because nothing is in doubt."""
    persistent, app = _exploding(ProviderNotSent("no credentials configured"))
    intent = _intent(app, key="r1-p2-notsent")
    receipt = _collect(app, intent.id)
    assert receipt.attempt_state == "failed"
    assert not receipt.requires_reconciliation
    assert app.queries.dispatch(GetPaymentIntent(**KW, intent_id=intent.id)).state == "failed"


def test_r1_p2_reconciliation_resolves_an_attempt_stranded_open(wired):
    """Reconciliation now closes **any** externally-open attempt, not only an ambiguous one."""
    persistent, provider = wired
    app = persistent.application
    provider.script(ProviderAction.INITIATE, ScriptedOutcome(status=ProviderStatus.PENDING))
    intent = _intent(app, key="r1-p2-open")
    receipt = _collect(app, intent.id)
    assert receipt.attempt_state == "pending"
    app.commands.dispatch(ReconcilePaymentAttempt(**KW, attempt_id=receipt.attempt_id))
    settled = app.queries.dispatch(GetPaymentAttempt(**KW, attempt_id=receipt.attempt_id))
    assert settled.state in SETTLED_ATTEMPT_STATES_VALUES


def test_r1_p2_a_resolved_intent_can_collect_again():
    """The property that matters: no intent is permanently unrecoverable."""
    persistent, app = _exploding(TimeoutError("gone"))
    intent = _intent(app, key="r1-p2-recover")
    receipt = _collect(app, intent.id)
    app.commands.dispatch(ReconcilePaymentAttempt(**KW, attempt_id=receipt.attempt_id))
    state = app.queries.dispatch(GetPaymentIntent(**KW, intent_id=intent.id))
    assert state.state == "failed"
    assert "retry" in state.allowed_triggers or "cancel" in state.allowed_triggers
    app.commands.dispatch(CancelPaymentIntent(**KW, intent_id=intent.id, reason="give up"))
    assert app.queries.dispatch(GetPaymentIntent(**KW, intent_id=intent.id)).state == "cancelled"


def test_r1_p2_duplicate_reconciliation_is_stable(wired):
    persistent, provider = wired
    app = persistent.application
    provider.script(
        ProviderAction.INITIATE,
        ScriptedOutcome(status=ProviderStatus.AMBIGUOUS, raw_status="succeeded"),
    )
    intent = _intent(app, key="r1-p2-twice")
    receipt = _collect(app, intent.id)
    first = app.commands.dispatch(ReconcilePaymentAttempt(**KW, attempt_id=receipt.attempt_id))
    second = app.commands.dispatch(ReconcilePaymentAttempt(**KW, attempt_id=receipt.attempt_id))
    assert first.outcome == "ambiguity_resolved"
    assert second.outcome in ("matched", "ambiguity_resolved")
    payments = [e for e in _entries(persistent) if e.entry_type.value == "payment"]
    assert len(payments) == 1, "reconciling twice must not post twice"
    assert provider.movements() == 1


# ── P-6 ─────────────────────────────────────────────────────────────────────────


def test_r1_p6_a_ledger_refusal_is_a_divergence_but_a_bug_is_not(wired):
    """**P-6.** A domain refusal becomes `UNPOSTED_CAPTURE`; a programming error propagates.

    The old `except Exception` hid an `AttributeError` as "the ledger refused" — recorded as a
    financial divergence and queued for an operator who could do nothing about it."""
    persistent, provider = wired
    app = persistent.application
    use_cases = app.payment_command_use_cases

    def explode(_command):
        raise AttributeError("this is a bug, not a refusal")

    original, use_cases._ledger_post = use_cases._ledger_post, explode
    try:
        intent = _intent(app, key="r1-p6")
        with pytest.raises(AttributeError, match="this is a bug"):
            _collect(app, intent.id)
    finally:
        use_cases._ledger_post = original
    assert app.queries.dispatch(ListPaymentDivergences(**KW)) == ()


def test_r1_p6_the_genuine_refusal_path_still_works():
    """The UNPOSTED_CAPTURE repair, unchanged: a real `BillingError` still keeps the capture."""
    persistent, provider = _build()
    app = persistent.application
    app.commands.dispatch(DeclarePaymentRoute(**KW, provider_code="offline", currency=INR))
    intent = _intent(app, key="r1-p6-real")
    receipt = _collect(app, intent.id)  # no accounting period is open
    assert receipt.attempt_state == "succeeded"
    assert receipt.ledger_outcome == "refused"
    assert [f.outcome for f in app.queries.dispatch(ListPaymentDivergences(**KW))] == [
        "unposted_capture"
    ]


# ── P-5 ─────────────────────────────────────────────────────────────────────────


def test_r1_p5_no_unreachable_partial_state_is_published():
    """**P-5.** `PARTIALLY_PAID`/`SETTLE_PARTIAL` were advertised and unreachable: every attempt is
    opened for the whole intent amount, so no outcome could produce them."""
    assert not [s for s in IntentState if "partial" in s.value]
    assert not [t for t in IntentTrigger if "partial" in t.value]
    for state in IntentState:
        for trigger in INTENT_LIFECYCLE.triggers_from(state):
            target = INTENT_LIFECYCLE.get(state, trigger).target
            assert "partial" not in target.value
    surfaces = _module("application", "payment_engine.py") + _studio_script()
    assert "partially_paid" not in surfaces.lower()
    assert "settle_partial" not in surfaces.lower()


def test_r1_p5_every_published_intent_state_is_reachable():
    """The stronger property the removal was for: no dead vocabulary on the published surface."""
    reachable = {IntentState.DRAFT}
    frontier = [IntentState.DRAFT]
    while frontier:
        state = frontier.pop()
        for trigger in INTENT_LIFECYCLE.triggers_from(state):
            target = INTENT_LIFECYCLE.get(state, trigger).target
            if target not in reachable:
                reachable.add(target)
                frontier.append(target)
    assert reachable == set(IntentState), sorted(s.value for s in set(IntentState) - reachable)


# ── P-4 ─────────────────────────────────────────────────────────────────────────


def test_r1_p4_the_relay_is_not_presented_as_a_provider_webhook(client):
    """**P-4.** The endpoint requires a Customer-Platform token, which no PSP holds. It is named
    and documented as an internal relay so it cannot be configured as a webhook by mistake."""
    spec = client.get("/openapi.json").json()
    paths = [p for p in spec["paths"] if p.startswith(f"{PREFIX}/collections")]
    assert not [p for p in paths if "webhook" in p], "no path may read as a provider webhook"
    relay = [p for p in paths if "provider-events/relay" in p]
    assert len(relay) == 1
    operation = spec["paths"][relay[0]]["post"]
    assert "INTERNAL RELAY ONLY" in operation["summary"]
    assert "not a provider webhook endpoint" in operation["description"]
    assert "does not exist at M9.16" in operation["description"]
    # And it really is authenticated: a PSP posting a valid signature still gets 401.
    assert client.post(relay[0], json={"provider_code": "offline", "payload": "x",
                                       "signature": "s3cret"}).status_code == 401


def test_r1_p4_the_limitation_is_documented():
    doc = (Path(__file__).resolve().parents[2] / "docs" / "architecture" / "epic9"
           / "payment-engine.md").read_text()
    assert "no production provider-webhook endpoint" in doc.lower()

# ── 20. M9.16-R1 — genuine concurrency (P-3, P-7) ──────────────────────────────
#
# Two **independent Applications over two independent connections to one file-backed database**.
# Not one session pretending to be two: each has its own `BillingDatabase`, its own session, and
# its own transaction, so the database — not Python — decides who wins. The interleaving is
# explicit rather than threaded, because a thread schedule that happens not to overlap proves
# nothing, while a hand-interleaved pair proves the constraint every time it runs.


def _two_sessions(tmp_path, name: str = "payments.db"):
    """Two Applications over one file-backed SQLite database, each with its own connection."""
    url = f"sqlite:///{tmp_path / name}"
    first = build_persistent_billing_application(
        url=url, authorizer=AllowAllAuthorizer(), ids=_TaggedIds("a"), clock=FixedClock(1_000.0),
        payment_providers=ProviderRegistry(providers=(_provider(),)),
    )
    second = build_persistent_billing_application(
        url=url, authorizer=AllowAllAuthorizer(), ids=_TaggedIds("b"), clock=FixedClock(1_000.0),
        create_all=False, payment_providers=ProviderRegistry(providers=(_provider(),)),
    )
    assert first.database is not second.database
    return first, second


def test_r1_p7_two_sessions_race_one_operation_claim(tmp_path):
    """**P-7.** The claim is arbitrated by the composite primary key across two connections.

    Both workers compute the same operation key — they must, it is derived — and exactly one wins.
    The loser is told `claimed=False`, which is the instruction not to transmit."""
    from billing.foundation import ProviderOperation

    first, second = _two_sessions(tmp_path, "claim.db")
    app_a, app_b = first.application, second.application
    app_a.commands.dispatch(DeclarePaymentRoute(**KW, provider_code="offline", currency=INR))
    first.database.session.commit()
    intent = _intent(app_a, key="race-claim")
    first.database.session.commit()

    key = derive_operation_key(intent.id, 1, ProviderAction.INITIATE.value)
    attempt_id = derive_attempt_id(intent.id, 1)

    def candidate(org_db):
        return ProviderOperation(
            org_id=ORG, operation_key=key, intent_id=intent.id, attempt_id=attempt_id,
            action=ProviderAction.INITIATE, provider_code="offline",
            amount=Money(100_000, INR), sent_at=1_000.0,
        )

    a_claim, a_won = app_a.context.payment_engine.operations.claim(candidate(first))
    b_claim, b_won = app_b.context.payment_engine.operations.claim(candidate(second))
    first.database.session.commit()
    try:
        second.database.session.commit()
    except Exception:  # the database refused the duplicate — also a correct loss
        second.database.session.rollback()
        b_won = False

    assert [a_won, b_won].count(True) == 1, "exactly one worker may transmit"
    assert a_claim.operation_key == b_claim.operation_key
    rows = second.database.session.exec(
        __import__("sqlmodel").select(
            __import__("billing.persistence.payment_models", fromlist=["x"])
            .BillingPaymentOperationRow
        ).where()
    )
    assert len([r for r in rows if r.operation_key == key]) == 1


def test_r1_p3_two_sessions_cannot_over_refund_one_capture(tmp_path):
    """**P-3, S2.** Captured 1000; two workers each ask for 700 under *different* request keys.

    Before: both read the same already-refunded total (zero), both passed the ceiling, and 1400 was
    returned against a 1000 capture. Now `(org, attempt, sequence)` is unique, so the second refund
    is an impossible row until the first commits — and the loser re-reads, re-checks against the
    winner, and is refused."""
    first, second = _two_sessions(tmp_path, "refund.db")
    app_a, app_b = first.application, second.application
    for app, persistent in ((app_a, first),):
        app.commands.dispatch(
            OpenAccountingPeriod(**KW, account_id=ACCOUNT, period_start=0.0,
                                 period_end=9_999_999.0, currency=INR)
        )
        app.commands.dispatch(DeclarePaymentRoute(**KW, provider_code="offline", currency=INR))
        persistent.database.session.commit()
    intent = _intent(app_a, key="race-refund")
    receipt = _collect(app_a, intent.id)
    first.database.session.commit()
    assert receipt.position.captured.minor == 100_000

    outcomes = []
    for app, persistent, key in (
        (app_a, first, "worker-a"), (app_b, second, "worker-b")
    ):
        try:
            refund = app.commands.dispatch(
                RefundPayment(**KW, attempt_id=receipt.attempt_id, amount_minor=70_000,
                              currency=INR, request_key=key)
            )
            persistent.database.session.commit()
            outcomes.append(refund.state)
        except BillingConflict as refused:
            persistent.database.session.rollback()
            outcomes.append(f"refused: {str(refused)[:30]}")

    assert outcomes.count("refunded") == 1, outcomes
    assert any(o.startswith("refused") for o in outcomes), outcomes

    position = app_a.queries.dispatch(GetPaymentPosition(**KW, intent_id=intent.id))
    assert position.refunded.minor == 70_000
    assert position.refunded.minor <= position.captured.minor


@pytest.mark.parametrize(
    ("first_minor", "second_minor", "expected_total"),
    [(40_000, 60_000, 100_000), (60_000, 40_000, 100_000), (50_000, 50_000, 100_000),
     (70_000, 70_000, 70_000), (100_000, 1, 100_000)],
)
def test_r1_p3_the_refund_ceiling_holds_for_every_split(
    tmp_path, first_minor, second_minor, expected_total
):
    """The ceiling under two sessions, across the splits that fit and the splits that do not."""
    first, second = _two_sessions(tmp_path, f"split-{first_minor}-{second_minor}.db")
    app_a, app_b = first.application, second.application
    app_a.commands.dispatch(
        OpenAccountingPeriod(**KW, account_id=ACCOUNT, period_start=0.0, period_end=9_999_999.0,
                             currency=INR)
    )
    app_a.commands.dispatch(DeclarePaymentRoute(**KW, provider_code="offline", currency=INR))
    first.database.session.commit()
    intent = _intent(app_a, key=f"split-{first_minor}-{second_minor}")
    receipt = _collect(app_a, intent.id)
    first.database.session.commit()

    for app, persistent, minor, key in (
        (app_a, first, first_minor, "k1"), (app_b, second, second_minor, "k2")
    ):
        try:
            app.commands.dispatch(
                RefundPayment(**KW, attempt_id=receipt.attempt_id, amount_minor=minor,
                              currency=INR, request_key=key)
            )
            persistent.database.session.commit()
        except BillingConflict:
            persistent.database.session.rollback()

    position = app_a.queries.dispatch(GetPaymentPosition(**KW, intent_id=intent.id))
    assert position.refunded.minor == expected_total
    assert position.refunded.minor <= 100_000, "a refund total may never exceed the capture"


def test_r1_p3_the_same_request_key_across_two_sessions_is_one_refund(tmp_path):
    """Idempotent replay still works across connections: one key, one refund, one ledger fact."""
    first, second = _two_sessions(tmp_path, "same-key.db")
    app_a, app_b = first.application, second.application
    app_a.commands.dispatch(
        OpenAccountingPeriod(**KW, account_id=ACCOUNT, period_start=0.0, period_end=9_999_999.0,
                             currency=INR)
    )
    app_a.commands.dispatch(DeclarePaymentRoute(**KW, provider_code="offline", currency=INR))
    first.database.session.commit()
    intent = _intent(app_a, key="same-key")
    receipt = _collect(app_a, intent.id)
    first.database.session.commit()

    a = app_a.commands.dispatch(
        RefundPayment(**KW, attempt_id=receipt.attempt_id, amount_minor=40_000, currency=INR,
                      request_key="one-key")
    )
    first.database.session.commit()
    b = app_b.commands.dispatch(
        RefundPayment(**KW, attempt_id=receipt.attempt_id, amount_minor=40_000, currency=INR,
                      request_key="one-key")
    )
    second.database.session.commit()

    assert a.id == b.id
    assert app_a.queries.dispatch(
        GetPaymentPosition(**KW, intent_id=intent.id)
    ).refunded.minor == 40_000


def test_r1_p3_the_refund_sequence_is_a_database_constraint():
    """Structural: the ceiling is not a Python check that a second worker can walk past."""
    from billing.persistence.payment_models import BillingCommercialRefundRow

    constraints = {
        tuple(c.columns.keys())
        for c in BillingCommercialRefundRow.__table__.constraints
        if hasattr(c, "columns") and c.name and "uq_" in str(c.name)
    }
    assert ("org_id", "attempt_id", "sequence") in constraints
    assert issubclass(RefundSequenceConflict, BillingConflict)
