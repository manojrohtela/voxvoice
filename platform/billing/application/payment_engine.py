"""Payment Engine application layer (M9.16) — commands, queries, use cases, mapping.

Additive to M9.3/M9.6–M9.15: the buses, Unit of Work, authorization orchestrator,
application-event vocabulary, and dispatch discipline are reused exactly as they are. This module
adds 13 commands, 17 queries, their use cases, and the view DTOs — and nothing else.

The four beats are unchanged — **authorize → open a transaction → coordinate the Foundation →
stage an event** — with one difference that defines the milestone: for the operations that move
money there are **two** transactions with an external call between them, and pretending otherwise
would be the most expensive lie in the codebase.

## The external transaction boundary

A database transaction cannot roll back a charge. So the money-moving path is three phases and the
ordering is load-bearing:

    ┌─ transaction 1 ──────────────────────────────────────────────────────────┐
    │ read the intent · guard the attempt · append the attempt                 │
    │ **claim the operation key** (composite PK — one claim wins) · COMMIT     │
    └──────────────────────────────────────────────────────────────────────────┘
                                     ↓  no transaction is open
                        provider.initiate(request)   ← the only external call
                                     ↓
    ┌─ transaction 2 ──────────────────────────────────────────────────────────┐
    │ append the observation · move the attempt FSM · move the intent FSM      │
    │ post the capture through the Ledger boundary · close the operation       │
    │ COMMIT                                                                    │
    └──────────────────────────────────────────────────────────────────────────┘

Every failure mode has a defined resting place:

* **crash before the call** — the operation row is `SENDING` and the provider never saw the key.
  `ReconcileProviderOperations` retrieves it, gets "not found", and abandons it. Nothing moved.
* **crash after the call, before transaction 2** — the operation row is `SENDING` and the provider
  may have moved money. Retrieve **the same key**: the provider answers what it did. This is the
  case that makes the protocol worth its cost.
* **the call times out** — the outcome is `AMBIGUOUS`, the attempt becomes `AMBIGUOUS`, and the
  intent is **blocked from another attempt** until it is reconciled. Not retried. Never retried.
* **a duplicate request** — the operation key is already claimed, so no second call is made at all;
  the existing operation is retrieved instead.
* **the provider says "already processed"** — that is not success. It is an instruction to
  retrieve, and `outcome_is_actionable` says so.

## What this layer reads, and what it refuses

Collecting a document is the only path by which money is taken against one, and it reads
**forward-only**:

* the **document** (M9.14) for what is payable — never the ledger, never rating, never pricing,
  never tax. Payments collects an amount another domain decided; recomputing it would make this a
  second source of a figure it does not own;
* the **billing account** (M9.8) for who is being collected from;
* the **routing policies**, which are Payments' own.

There is no arithmetic here beyond comparing two amounts for equality, and no provider name
anywhere. Both are asserted by AST-driven tests.

## The one write into another milestone's domain, and why it is not a leak

A captured payment is posted through **M9.13's own command** (`PostJournalEntry`, extended
additively at this milestone with an explicit `source`), dispatched into the Ledger use cases. The
Ledger decides the period, the sequence, the entry id, and whether the posting is a duplicate.
Payments supplies a cause and an amount and receives a receipt. It writes nothing into ledger
persistence, and a test asserts the ledger repositories are untouched by this module.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from billing.foundation import (
    MONEY_MOVING_ACTIONS,
    AttemptGuardService,
    AttemptState,
    BillingError,
    Currency,
    EventDisposition,
    FailureCategory,
    IntentState,
    IntentTrigger,
    LedgerDirection,
    LedgerEntryType,
    Money,
    PaymentAction,
    PaymentAttempt,
    PaymentIntent,
    PaymentMethodKind,
    PaymentPurpose,
    PaymentReconciliation,
    PaymentRoute,
    PaymentSearchQuery,
    PaymentSubjectKind,
    PostingSource,
    ProviderAction,
    ProviderNotSent,
    ProviderOutcome,
    ProviderRequest,
    ProviderStatus,
    ReconciliationOutcome,
    RefundReason,
    RefundSequenceConflict,
    outcome_is_actionable,
)
from billing.foundation import BillingConflict as _Conflict
from billing.foundation import BillingNotFound as _NotFound

from .authorization import AuthorizationOrchestrator
from .authorization import BillingPermission as P
from .commands import _Command
from .events import PREFIX_APP_EVENT, ApplicationEvent, ApplicationEventType
from .ledger_engine import PostJournalEntry
from .mapping import MoneyView
from .queries import _Query
from .unit_of_work import UnitOfWorkFactory

#: How many times a refund will re-take a lost slot before giving up. Small and bounded: each
#: round either consumes a slot or is refused by the ceiling, so the loop cannot spin.
_REFUND_SLOT_ATTEMPTS = 5

# ── commands ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DeclarePaymentRoute(_Command):
    """Declare which provider serves which payments. **The only place a provider is named.**"""

    provider_code: str = ""
    priority: int = 100
    currency: Currency | None = None
    country: str = ""
    method: PaymentMethodKind | None = None
    account_id: str = ""
    version: str = ""
    notes: str = ""


@dataclass(frozen=True, slots=True)
class RetirePaymentRoute(_Command):
    route_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CreatePaymentIntent(_Command):
    """Open a collection objective against an already-issued document.

    `request_key` is the caller's idempotency token and is part of the intent's derived identity, so
    the same request twice is one objective and a *deliberate* second collection on a partly-paid
    document is a different one."""

    account_id: str = ""
    subject_id: str = ""
    subject_kind: PaymentSubjectKind = PaymentSubjectKind.INVOICE
    purpose: PaymentPurpose = PaymentPurpose.INVOICE
    amount_minor: int = 0
    currency: Currency = Currency.INR
    method: PaymentMethodKind = PaymentMethodKind.CARD
    instrument_id: str = ""
    request_key: str = ""
    country: str = ""
    description: str = ""


@dataclass(frozen=True, slots=True)
class CancelPaymentIntent(_Command):
    intent_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ExpirePaymentIntent(_Command):
    intent_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class StartPaymentAttempt(_Command):
    """**Move money.** The three-phase path; the only command that opens an external movement."""

    intent_id: str = ""
    instrument_token: str = ""
    customer_reference: str = ""
    return_url: str = ""
    correlation_id: str = ""


@dataclass(frozen=True, slots=True)
class CapturePaymentAttempt(_Command):
    """Take money that was authorized. Its own operation key, its own idempotency."""

    attempt_id: str = ""


@dataclass(frozen=True, slots=True)
class CancelPaymentAttempt(_Command):
    """Void an authorization that has not been captured."""

    attempt_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class IngestProviderEvent(_Command):
    """A provider callback arriving at the trust boundary.

    Takes the **raw payload and signature**, never a parsed status: a command that accepted
    `status="succeeded"` from its caller would let anyone who can reach the endpoint mark invoices
    paid. Verification happens in the adapter, and an unverified envelope is recorded and never
    applied."""

    provider_code: str = ""
    payload: str = ""
    signature: str = ""


@dataclass(frozen=True, slots=True)
class ReconcilePaymentAttempt(_Command):
    """Ask the provider what happened to one attempt's operation key, and record the comparison.

    This is the *only* command that resolves an ambiguous attempt, and it resolves it by retrieving
    the same key — never by sending another one."""

    attempt_id: str = ""


@dataclass(frozen=True, slots=True)
class ReconcileProviderOperations(_Command):
    """The crash-recovery sweep: every operation still `SENDING` past its window."""

    older_than_seconds: float = 300.0
    limit: int = 100


@dataclass(frozen=True, slots=True)
class ResolvePaymentDivergence(_Command):
    """Close a reconciliation finding. **Always attributed** — ADR-0023 PY5."""

    finding_id: str = ""
    note: str = ""
    acknowledge_only: bool = False


@dataclass(frozen=True, slots=True)
class RefundPayment(_Command):
    """Return money. A new financial fact, bounded by what was captured and not already returned."""

    attempt_id: str = ""
    amount_minor: int = 0
    currency: Currency = Currency.INR
    reason: RefundReason = RefundReason.CUSTOMER_REQUEST
    request_key: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class PostPaymentToLedger(_Command):
    """Record a capture that moved money but whose ledger posting never landed.

    Exists because transaction 2 can fail after the provider succeeded: the money is real, the
    financial fact is missing, and the repair must be an explicit, idempotent, attributed act
    rather than a background process nobody knows ran."""

    attempt_id: str = ""

#: The command vocabulary this milestone adds, as a tuple so "one handler per command" is checkable
#: by iteration rather than asserted in prose.
ALL_PAYMENT_COMMANDS = (
    DeclarePaymentRoute,
    RetirePaymentRoute,
    CreatePaymentIntent,
    CancelPaymentIntent,
    ExpirePaymentIntent,
    StartPaymentAttempt,
    CapturePaymentAttempt,
    CancelPaymentAttempt,
    IngestProviderEvent,
    ReconcilePaymentAttempt,
    ReconcileProviderOperations,
    ResolvePaymentDivergence,
    RefundPayment,
    PostPaymentToLedger,
)


# ── queries ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetPaymentIntent(_Query):
    intent_id: str


@dataclass(frozen=True, slots=True)
class ListPaymentIntents(_Query):
    account_id: str = ""
    subject_id: str = ""
    limit: int = 200


@dataclass(frozen=True, slots=True)
class GetPaymentAttempt(_Query):
    attempt_id: str


@dataclass(frozen=True, slots=True)
class ListPaymentAttempts(_Query):
    intent_id: str


@dataclass(frozen=True, slots=True)
class ExplainPayment(_Query):
    """Why is this payment in this state? Answered from stored evidence, never from a provider."""

    intent_id: str


@dataclass(frozen=True, slots=True)
class InspectPayment(_Query):
    intent_id: str


@dataclass(frozen=True, slots=True)
class DiagnosePayment(_Query):
    intent_id: str


@dataclass(frozen=True, slots=True)
class GetPaymentPosition(_Query):
    intent_id: str


@dataclass(frozen=True, slots=True)
class ListProviderOperations(_Query):
    intent_id: str = ""
    open_only: bool = False
    limit: int = 200


@dataclass(frozen=True, slots=True)
class ListProviderEvents(_Query):
    attempt_id: str = ""
    limit: int = 100


@dataclass(frozen=True, slots=True)
class ListPaymentRefunds(_Query):
    intent_id: str = ""
    attempt_id: str = ""
    limit: int = 200


@dataclass(frozen=True, slots=True)
class GetRefundableAmount(_Query):
    attempt_id: str


@dataclass(frozen=True, slots=True)
class ListPaymentDivergences(_Query):
    open_only: bool = True
    limit: int = 200


@dataclass(frozen=True, slots=True)
class SearchPayments(_Query):
    account_id: str = ""
    subject_id: str = ""
    provider_code: str = ""
    state: IntentState | None = None
    purpose: PaymentPurpose | None = None
    currency: Currency | None = None
    limit: int = 100


@dataclass(frozen=True, slots=True)
class PaymentTimeline(_Query):
    account_id: str = ""
    intent_id: str = ""
    limit: int = 100


@dataclass(frozen=True, slots=True)
class ListPaymentRoutes(_Query):
    include_retired: bool = False


@dataclass(frozen=True, slots=True)
class PreviewPaymentRoute(_Query):
    """Which provider would serve this payment, and why — decided without sending anything."""

    currency: Currency = Currency.INR
    country: str = ""
    method: PaymentMethodKind = PaymentMethodKind.CARD
    account_id: str = ""

ALL_PAYMENT_QUERIES = (
    GetPaymentIntent,
    ListPaymentIntents,
    GetPaymentAttempt,
    ListPaymentAttempts,
    ExplainPayment,
    InspectPayment,
    DiagnosePayment,
    GetPaymentPosition,
    ListProviderOperations,
    ListProviderEvents,
    ListPaymentRefunds,
    GetRefundableAmount,
    ListPaymentDivergences,
    SearchPayments,
    PaymentTimeline,
    ListPaymentRoutes,
    PreviewPaymentRoute,
)


# ── views ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PaymentRouteView:
    provider_code: str
    reason: str = ""
    policy_id: str = ""
    policy_version: str = ""
    decided_at: float = 0.0


@dataclass(frozen=True, slots=True)
class PaymentRoutePolicyView:
    id: str
    provider_code: str
    priority: int
    currency: str = ""
    country: str = ""
    method: str = ""
    account_id: str = ""
    version: str = ""
    is_active: bool = True
    notes: str = ""


@dataclass(frozen=True, slots=True)
class PaymentPositionView:
    intent_id: str
    currency: str
    requested: MoneyView
    captured: MoneyView
    refunded: MoneyView
    net: MoneyView
    outstanding: MoneyView
    attempts: int = 0
    successful_attempts: int = 0
    open_attempts: int = 0
    unresolved_attempts: int = 0
    refunds: int = 0
    is_fully_collected: bool = False
    is_overpaid: bool = False
    is_blocked: bool = False


@dataclass(frozen=True, slots=True)
class ProviderObservationView:
    provider_code: str
    reference: str
    reported_state: str
    received_at: float
    verified: bool = False
    amount: MoneyView | None = None


@dataclass(frozen=True, slots=True)
class PaymentAttemptView:
    id: str
    intent_id: str
    account_id: str
    sequence: int
    state: str
    amount: MoneyView
    operation_key: str
    provider_code: str = ""
    provider_reference: str = ""
    provider_status: str = ""
    method: str = ""
    failure_code: str = ""
    failure_category: str = ""
    failure_message: str = ""
    action_url: str = ""
    journal_entry_id: str = ""
    started_at: float = 0.0
    settled_at: float | None = None
    resolved_by: str = ""
    resolution_reason: str = ""
    is_unresolved: bool = False
    observations: tuple[ProviderObservationView, ...] = ()
    allowed_triggers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PaymentIntentView:
    id: str
    account_id: str
    state: str
    amount: MoneyView
    purpose: str
    subject_kind: str
    subject_id: str = ""
    subject_digest: str = ""
    method: str = ""
    instrument_id: str = ""
    route: PaymentRouteView | None = None
    request_key: str = ""
    attempts_opened: int = 0
    country: str = ""
    description: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    closed_at: float | None = None
    allowed_triggers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderOperationView:
    operation_key: str
    intent_id: str
    attempt_id: str
    action: str
    provider_code: str
    state: str
    amount: MoneyView
    sent_at: float = 0.0
    completed_at: float | None = None
    provider_reference: str = ""
    provider_status: str = ""
    outcome_status: str = ""
    attempts_sent: int = 0
    last_error: str = ""


@dataclass(frozen=True, slots=True)
class ProviderEventView:
    id: str
    provider_code: str
    event_ref: str
    event_type: str
    disposition: str
    verified: bool
    attempt_id: str = ""
    intent_id: str = ""
    outcome_status: str = ""
    provider_reference: str = ""
    raw_status: str = ""
    provider_ts: float = 0.0
    received_at: float = 0.0
    applied_at: float | None = None
    note: str = ""


@dataclass(frozen=True, slots=True)
class CommercialRefundView:
    """Named distinctly from M9.3's `RefundView`, which serves M9.2's thinner `Refund`.

    Not cosmetic: both are exported from `billing.application`, so a second `RefundView` would
    shadow the first in the package namespace depending on import order. Caught by the linter.
    """

    id: str
    intent_id: str
    attempt_id: str
    account_id: str
    state: str
    amount: MoneyView
    reason: str
    destination: str
    provider_code: str = ""
    provider_reference: str = ""
    request_key: str = ""
    requested_at: float = 0.0
    completed_at: float | None = None
    failure_code: str = ""
    journal_entry_id: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class PaymentDivergenceView:
    id: str
    outcome: str
    status: str
    provider_code: str = ""
    intent_id: str = ""
    attempt_id: str = ""
    operation_key: str = ""
    provider_reference: str = ""
    billing_state: str = ""
    provider_state: str = ""
    billing_amount: MoneyView | None = None
    provider_amount: MoneyView | None = None
    journal_entry_id: str = ""
    observed_at: float = 0.0
    resolved_at: float | None = None
    resolved_by: str = ""
    resolution_note: str = ""
    detail: str = ""
    is_divergent: bool = False


@dataclass(frozen=True, slots=True)
class PaymentExplanationView:
    intent_id: str
    state: str
    position: PaymentPositionView
    provider_code: str = ""
    routing_reason: str = ""
    subject_id: str = ""
    subject_digest: str = ""
    attempts: tuple[PaymentAttemptView, ...] = ()
    operations: tuple[ProviderOperationView, ...] = ()
    events: tuple[ProviderEventView, ...] = ()
    findings: tuple[PaymentDivergenceView, ...] = ()
    journal_entry_ids: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    is_self_contained: bool = True


@dataclass(frozen=True, slots=True)
class PaymentInspectionView:
    intent_id: str
    state: str
    position: PaymentPositionView
    is_blocked: bool = False
    has_open_divergence: bool = False
    unposted_captures: tuple[str, ...] = ()
    open_operations: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    is_clean: bool = True


@dataclass(frozen=True, slots=True)
class PaymentDiagnosticView:
    path: str
    code: str
    severity: str
    message: str
    remediation: str = ""


@dataclass(frozen=True, slots=True)
class PaymentDiagnosticsView:
    intent_id: str
    is_valid: bool
    findings: tuple[PaymentDiagnosticView, ...] = ()


@dataclass(frozen=True, slots=True)
class PaymentActivityView:
    id: str
    action: str
    at: float
    actor_id: str = ""
    account_id: str = ""
    intent_id: str = ""
    attempt_id: str = ""
    refund_id: str = ""
    provider_code: str = ""
    operation_key: str = ""
    amount_minor: int = 0
    currency: str = ""
    reason: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class PaymentSearchView:
    intents: tuple[PaymentIntentView, ...] = ()
    total: int = 0


@dataclass(frozen=True, slots=True)
class PaymentReceiptView:
    """What one money-moving command did — including the cases where it deliberately did nothing.

    `outcome` distinguishes the states an HTTP layer must not flatten: `succeeded`,
    `requires_action`, `pending`, `authorized`, `failed`, `ambiguous`, and `replayed` (the operation
    key was already claimed, so nothing was sent)."""

    intent_id: str
    attempt_id: str
    outcome: str
    intent_state: str
    attempt_state: str
    provider_code: str = ""
    provider_reference: str = ""
    action_url: str = ""
    failure_code: str = ""
    message: str = ""
    journal_entry_id: str = ""
    ledger_outcome: str = ""
    position: PaymentPositionView | None = None
    requires_reconciliation: bool = False


# ── mapping ─────────────────────────────────────────────────────────────────────


def _money(amount: Money) -> MoneyView:
    return MoneyView(minor=amount.minor, currency=amount.currency.value)


def _optional_money(amount: Money | None) -> MoneyView | None:
    return _money(amount) if amount is not None else None


def route_view(route: PaymentRoute) -> PaymentRouteView:
    return PaymentRouteView(
        provider_code=route.provider_code,
        reason=route.reason,
        policy_id=route.policy_id,
        policy_version=route.policy_version,
        decided_at=route.decided_at,
    )


def route_policy_view(policy) -> PaymentRoutePolicyView:
    return PaymentRoutePolicyView(
        id=policy.id,
        provider_code=policy.provider_code,
        priority=policy.priority,
        currency=policy.currency.value if policy.currency else "",
        country=policy.country,
        method=policy.method.value if policy.method else "",
        account_id=policy.account_id,
        version=policy.version,
        is_active=policy.is_active,
        notes=policy.notes,
    )


def payment_position_view(position) -> PaymentPositionView:
    return PaymentPositionView(
        intent_id=position.intent_id,
        currency=position.currency.value,
        requested=_money(position.requested),
        captured=_money(position.captured),
        refunded=_money(position.refunded),
        net=_money(position.net),
        outstanding=_money(position.outstanding),
        attempts=position.attempts,
        successful_attempts=position.successful_attempts,
        open_attempts=position.open_attempts,
        unresolved_attempts=position.unresolved_attempts,
        refunds=position.refunds,
        is_fully_collected=position.is_fully_collected,
        is_overpaid=position.is_overpaid,
        is_blocked=position.is_blocked,
    )


def payment_attempt_view(attempt: PaymentAttempt, allowed: tuple = ()) -> PaymentAttemptView:
    return PaymentAttemptView(
        id=attempt.id,
        intent_id=attempt.intent_id,
        account_id=attempt.account_id,
        sequence=attempt.sequence,
        state=attempt.state.value,
        amount=_money(attempt.amount),
        operation_key=attempt.operation_key,
        provider_code=attempt.provider_code,
        provider_reference=attempt.provider_reference,
        provider_status=attempt.provider_status,
        method=attempt.method.value,
        failure_code=attempt.failure_code,
        failure_category=attempt.failure_category.value,
        failure_message=attempt.failure_message,
        action_url=attempt.action_url,
        journal_entry_id=attempt.journal_entry_id,
        started_at=attempt.started_at,
        settled_at=attempt.settled_at,
        resolved_by=attempt.resolved_by,
        resolution_reason=attempt.resolution_reason,
        is_unresolved=attempt.is_unresolved,
        observations=tuple(
            ProviderObservationView(
                provider_code=o.provider_code,
                reference=o.reference,
                reported_state=o.reported_state,
                received_at=o.received_at,
                verified=o.verified,
                amount=_optional_money(o.amount),
            )
            for o in attempt.observations
        ),
        allowed_triggers=tuple(t.value for t in allowed),
    )


def intent_view(intent: PaymentIntent, allowed: tuple = ()) -> PaymentIntentView:
    return PaymentIntentView(
        id=intent.id,
        account_id=intent.account_id,
        state=intent.state.value,
        amount=_money(intent.amount),
        purpose=intent.purpose.value,
        subject_kind=intent.subject_kind.value,
        subject_id=intent.subject_id,
        subject_digest=intent.subject_digest,
        method=intent.method.value,
        instrument_id=intent.instrument_id,
        route=route_view(intent.route),
        request_key=intent.request_key,
        attempts_opened=intent.attempts_opened,
        country=intent.country,
        description=intent.description,
        created_at=intent.created_at,
        updated_at=intent.updated_at,
        closed_at=intent.closed_at,
        allowed_triggers=tuple(t.value for t in allowed),
    )


def operation_view(operation) -> ProviderOperationView:
    return ProviderOperationView(
        operation_key=operation.operation_key,
        intent_id=operation.intent_id,
        attempt_id=operation.attempt_id,
        action=operation.action.value,
        provider_code=operation.provider_code,
        state=operation.state.value,
        amount=_money(operation.amount),
        sent_at=operation.sent_at,
        completed_at=operation.completed_at,
        provider_reference=operation.provider_reference,
        provider_status=operation.provider_status,
        outcome_status=operation.outcome_status.value if operation.outcome_status else "",
        attempts_sent=operation.attempts_sent,
        last_error=operation.last_error,
    )


def payment_event_view(record) -> ProviderEventView:
    return ProviderEventView(
        id=record.id,
        provider_code=record.provider_code,
        event_ref=record.event_ref,
        event_type=record.event_type,
        disposition=record.disposition.value,
        verified=record.verified,
        attempt_id=record.attempt_id,
        intent_id=record.intent_id,
        outcome_status=record.outcome_status.value if record.outcome_status else "",
        provider_reference=record.provider_reference,
        raw_status=record.raw_status,
        provider_ts=record.provider_ts,
        received_at=record.received_at,
        applied_at=record.applied_at,
        note=record.note,
    )


def refund_view(refund) -> CommercialRefundView:
    return CommercialRefundView(
        id=refund.id,
        intent_id=refund.intent_id,
        attempt_id=refund.attempt_id,
        account_id=refund.account_id,
        state=refund.state.value,
        amount=_money(refund.amount),
        reason=refund.reason.value,
        destination=refund.destination.value,
        provider_code=refund.provider_code,
        provider_reference=refund.provider_reference,
        request_key=refund.request_key,
        requested_at=refund.requested_at,
        completed_at=refund.completed_at,
        failure_code=refund.failure_code,
        journal_entry_id=refund.journal_entry_id,
        note=refund.note,
    )


def divergence_view(finding: PaymentReconciliation) -> PaymentDivergenceView:
    return PaymentDivergenceView(
        id=finding.id,
        outcome=finding.outcome.value,
        status=finding.status.value,
        provider_code=finding.provider_code,
        intent_id=finding.intent_id,
        attempt_id=finding.attempt_id,
        operation_key=finding.operation_key,
        provider_reference=finding.provider_reference,
        billing_state=finding.billing_state,
        provider_state=finding.provider_state,
        billing_amount=_optional_money(finding.billing_amount),
        provider_amount=_optional_money(finding.provider_amount),
        journal_entry_id=finding.journal_entry_id,
        observed_at=finding.observed_at,
        resolved_at=finding.resolved_at,
        resolved_by=finding.resolved_by,
        resolution_note=finding.resolution_note,
        detail=finding.detail,
        is_divergent=finding.is_divergent,
    )


def payment_activity_view(activity) -> PaymentActivityView:
    return PaymentActivityView(
        id=activity.id,
        action=activity.action.value,
        at=activity.at,
        actor_id=activity.actor_id,
        account_id=activity.account_id,
        intent_id=activity.intent_id,
        attempt_id=activity.attempt_id,
        refund_id=activity.refund_id,
        provider_code=activity.provider_code,
        operation_key=activity.operation_key,
        amount_minor=activity.amount_minor,
        currency=activity.currency.value if activity.currency else "",
        reason=activity.reason,
        detail=activity.detail,
    )


def diagnostics_view(intent_id: str, diagnostics) -> PaymentDiagnosticsView:
    return PaymentDiagnosticsView(
        intent_id=intent_id,
        is_valid=diagnostics.is_valid,
        findings=tuple(
            PaymentDiagnosticView(
                path=f.path,
                code=f.code.value,
                severity=f.severity.value,
                message=f.message,
                remediation=f.remediation,
            )
            for f in diagnostics.findings
        ),
    )


# ── the seams ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PaymentRepositories:
    """The payment seams, injected. Interfaces only — implementations in persistence."""

    routes: object
    intents: object
    attempts: object
    operations: object
    events: object
    refunds: object
    reconciliations: object
    activity: object


class _PaymentSupport:
    """Shared read helpers for the command and query sides.

    Both sides derive a position from the same facts, and computing it twice from two different
    sets of reads is how a receipt and a dashboard come to disagree about whether an invoice is
    paid."""

    def __init__(self, *, context, repos: PaymentRepositories, services) -> None:
        self._ctx = context
        self._repos = repos
        self._svc = services

    # ── the document being paid (read-only) ─────────────────────────────────────

    def payable_amount(self, org_id: str, subject_id: str) -> tuple[Money | None, str]:
        """What the immutable document says is payable, and the digest it was read from.

        Read forward-only. The figure came from the Ledger through the Invoice and, since M9.15,
        carries tax; Payments reads it as given. Going back to the ledger — let alone to rating or
        pricing — would make Payments a second source of a figure another domain owns.

        Returns `(None, "")` when the invoice engine is not wired, which is not an error: a deposit
        or an instrument verification has no document at all."""
        engine = getattr(self._ctx, "invoice_engine", None)
        documents = getattr(engine, "documents", None)
        snapshots = getattr(engine, "snapshots", None)
        if documents is None or snapshots is None or not subject_id:
            return None, ""
        document = documents.get(org_id, subject_id)
        if document is None:
            raise _NotFound(f"document {subject_id!r} not found")
        digest = document.issued_snapshot_digest
        snapshot = (
            snapshots.by_digest(org_id, digest) if digest else snapshots.latest(org_id, document.id)
        )
        if snapshot is None:
            raise _NotFound(f"document {subject_id!r} has no issued content to pay")
        return getattr(snapshot, "total", None), digest

    # ── facts, never figures ────────────────────────────────────────────────────

    def attempts_for(self, org_id: str, intent_id: str) -> tuple[PaymentAttempt, ...]:
        return self._repos.attempts.for_intent(org_id, intent_id)

    def refunds_for(self, org_id: str, intent_id: str) -> tuple:
        return self._repos.refunds.for_intent(org_id, intent_id)

    def position_for(self, org_id: str, intent: PaymentIntent):
        return self._svc.positions.position(
            intent, self.attempts_for(org_id, intent.id), self.refunds_for(org_id, intent.id)
        )

    def intent_or_404(self, org_id: str, intent_id: str) -> PaymentIntent:
        found = self._repos.intents.get(org_id, intent_id)
        if found is None:
            raise _NotFound(f"payment intent {intent_id!r} not found")
        return found

    def attempt_or_404(self, org_id: str, attempt_id: str) -> PaymentAttempt:
        found = self._repos.attempts.get(org_id, attempt_id)
        if found is None:
            raise _NotFound(f"payment attempt {attempt_id!r} not found")
        return found

    def provider_for(self, attempt_or_intent) -> object:
        code = getattr(attempt_or_intent, "provider_code", "") or getattr(
            attempt_or_intent, "route", PaymentRoute()
        ).provider_code
        if not code:
            raise _Conflict(
                "no payment provider is routed for this payment; declare a payment route first"
            )
        return self._svc.registry.require(code)


@dataclass(frozen=True, slots=True)
class PaymentServices:
    """The Foundation services this layer coordinates. Composed once, held by reference."""

    intent_lifecycle: object
    attempt_lifecycle: object
    guards: AttemptGuardService
    router: object
    operation_rules: object
    normalizer: object
    positions: object
    refund_rules: object
    reconciliation: object
    planner: object
    explainer: object
    inspector: object
    validator: object
    search: object
    registry: object


class PaymentUseCases(_PaymentSupport):
    """Payment orchestration. **Three phases where money moves, one everywhere else.**"""

    def __init__(
        self,
        *,
        context,
        repos: PaymentRepositories,
        factory,
        services: PaymentServices,
        authz: AuthorizationOrchestrator,
        uow_factory: UnitOfWorkFactory,
        ids,
        clock,
        ledger_post: Callable[[PostJournalEntry], object] | None = None,
    ) -> None:
        super().__init__(context=context, repos=repos, services=services)
        self._factory = factory
        self._authz = authz
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock
        self._ledger_post = ledger_post

    # ── routing configuration ───────────────────────────────────────────────────

    def declare_route(self, command: DeclarePaymentRoute) -> PaymentRoutePolicyView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            stored = self._repos.routes.add(
                self._factory.route_policy(
                    org_id=command.org_id,
                    provider_code=command.provider_code,
                    priority=command.priority,
                    currency=command.currency,
                    country=command.country,
                    method=command.method,
                    account_id=command.account_id,
                    version=command.version,
                    notes=command.notes,
                    actor_id=command.actor_id,
                )
            )
            self._record(command, PaymentAction.ROUTE_DECLARED, provider_code=stored.provider_code)
            uow.stage(self._event(command, stored.id, provider=stored.provider_code))
            return route_policy_view(stored)

    def retire_route(self, command: RetirePaymentRoute) -> PaymentRoutePolicyView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            found = self._repos.routes.get(command.org_id, command.route_id)
            if found is None:
                raise _NotFound(f"payment route {command.route_id!r} not found")
            stored = self._repos.routes.replace(
                self._factory.retire_route(
                    found, actor_id=command.actor_id, reason=command.reason
                )
            )
            self._record(
                command,
                PaymentAction.ROUTE_RETIRED,
                provider_code=stored.provider_code,
                reason=command.reason,
            )
            uow.stage(self._event(command, stored.id))
            return route_policy_view(stored)

    # ── the collection objective ────────────────────────────────────────────────

    def create_intent(self, command: CreatePaymentIntent) -> PaymentIntentView:
        """Open an objective. Idempotent on the derived id, so the same request twice is one."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            amount = Money(command.amount_minor, command.currency)
            payable, digest = self.payable_amount(command.org_id, command.subject_id)
            route = self._svc.router.select(
                self._repos.routes.all(command.org_id),
                currency=command.currency,
                country=command.country,
                method=command.method,
                account_id=command.account_id,
                at=self._clock.now(),
            )
            built = self._factory.intent(
                org_id=command.org_id,
                account_id=command.account_id,
                amount=amount,
                purpose=command.purpose,
                subject_kind=command.subject_kind,
                subject_id=command.subject_id,
                subject_digest=digest,
                method=command.method,
                instrument_id=command.instrument_id,
                route=route,
                request_key=command.request_key,
                country=command.country,
                description=command.description,
                subject_amount=payable,
                actor_id=command.actor_id,
            )
            stored = self._repos.intents.save(built)
            self._record(
                command,
                PaymentAction.INTENT_CREATED,
                account_id=stored.account_id,
                intent_id=stored.id,
                amount=stored.amount,
                provider_code=stored.route.provider_code,
            )
            uow.stage(self._event(command, stored.id, amount_minor=stored.amount.minor))
            return intent_view(stored, self._svc.intent_lifecycle.allowed(stored.state))

    def cancel_intent(self, command: CancelPaymentIntent) -> PaymentIntentView:
        return self._close_intent(
            command, command.intent_id, IntentTrigger.CANCEL, PaymentAction.INTENT_CANCELLED
        )

    def expire_intent(self, command: ExpirePaymentIntent) -> PaymentIntentView:
        return self._close_intent(
            command, command.intent_id, IntentTrigger.EXPIRE, PaymentAction.INTENT_EXPIRED
        )

    def _close_intent(self, command, intent_id, trigger, action) -> PaymentIntentView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            intent = self.intent_or_404(command.org_id, intent_id)
            attempts = self.attempts_for(command.org_id, intent.id)
            open_attempts = [a for a in attempts if a.is_open or a.is_unresolved]
            if open_attempts:
                raise _Conflict(
                    f"payment attempt {open_attempts[0].id} is {open_attempts[0].state.value}; "
                    "an objective with a movement in flight is not closed until it is known"
                )
            moved = self._svc.intent_lifecycle.apply(intent, trigger, at=self._clock.now())
            stored = self._repos.intents.mark_state(moved)
            self._factory.emit_intent_state(
                stored, actor_id=command.actor_id, reason=command.reason
            )
            self._record(command, action, intent_id=stored.id, reason=command.reason)
            uow.stage(self._event(command, stored.id, state=stored.state.value))
            return intent_view(stored, self._svc.intent_lifecycle.allowed(stored.state))

    # ── moving money: the three-phase path ──────────────────────────────────────

    def start_attempt(self, command: StartPaymentAttempt) -> PaymentReceiptView:
        """**Phase 1 commits, phase 2 calls a provider, phase 3 commits what came back.**"""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)

        # ── phase 1: durable, and committed before anything external happens ────
        with self._uow.new() as uow:
            intent = self.intent_or_404(command.org_id, command.intent_id)
            attempts = self.attempts_for(command.org_id, intent.id)
            operations = self._repos.operations.for_intent(command.org_id, intent.id)
            self._svc.guards.guard_may_attempt(intent, attempts, operations)
            provider = self.provider_for(intent)
            if intent.state is IntentState.DRAFT:
                intent = self._repos.intents.mark_state(
                    self._svc.intent_lifecycle.apply(
                        intent, IntentTrigger.START, at=self._clock.now()
                    )
                )
            elif intent.state is IntentState.FAILED:
                intent = self._repos.intents.mark_state(
                    self._svc.intent_lifecycle.apply(
                        intent, IntentTrigger.RETRY, at=self._clock.now()
                    )
                )
            sequence = intent.next_sequence
            attempt = self._repos.attempts.append(
                self._factory.attempt(
                    intent=intent,
                    sequence=sequence,
                    correlation_id=command.correlation_id,
                    actor_id=command.actor_id,
                )
            )
            intent = self._repos.intents.mark_state(
                replace(intent, attempts_opened=max(intent.attempts_opened, sequence))
            )
            operation, request = self._svc.operation_rules.open(
                intent=intent,
                attempt=attempt,
                action=ProviderAction.INITIATE,
                at=self._clock.now(),
                instrument_token=command.instrument_token,
                customer_reference=command.customer_reference,
                return_url=command.return_url,
            )
            operation, claimed = self._repos.operations.claim(operation)
            self._factory.emit_operation(
                operation, replayed=not claimed, actor_id=command.actor_id
            )
            self._record(
                command,
                PaymentAction.ATTEMPT_STARTED if claimed else PaymentAction.OPERATION_DUPLICATE,
                account_id=intent.account_id,
                intent_id=intent.id,
                attempt_id=attempt.id,
                operation_key=operation.operation_key,
                provider_code=attempt.provider_code,
                amount=attempt.amount,
            )
            uow.stage(self._event(command, attempt.id, claimed=claimed))

        # ── phase 2: the external call. No transaction is open. ─────────────────
        if not claimed and operation.is_resolved:
            # Somebody else already sent this and recorded the answer. Nothing to send.
            return self._receipt(
                command.org_id, intent, attempt, outcome="replayed", ledger_outcome=""
            )
        outcome = self._call_provider(
            provider.initiate if claimed else provider.retrieve, request, claimed=claimed
        )

        # ── phase 3: record what came back ──────────────────────────────────────
        return self._settle_attempt(
            command, attempt.id, operation.operation_key, outcome, action=ProviderAction.INITIATE
        )

    def capture_attempt(self, command: CapturePaymentAttempt) -> PaymentReceiptView:
        """Take authorized money. A **separate** operation key from the authorization, because a
        provider that saw one key for both would decline the capture as a duplicate."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            attempt = self.attempt_or_404(command.org_id, command.attempt_id)
            if attempt.state is not AttemptState.AUTHORIZED:
                raise _Conflict(
                    f"payment attempt {attempt.id} is {attempt.state.value}; only an authorized "
                    "attempt can be captured"
                )
            intent = self.intent_or_404(command.org_id, attempt.intent_id)
            provider = self.provider_for(attempt)
            operation, request = self._svc.operation_rules.open(
                intent=intent,
                attempt=attempt,
                action=ProviderAction.CAPTURE,
                at=self._clock.now(),
                reference=attempt.provider_reference,
            )
            operation, claimed = self._repos.operations.claim(operation)
            self._factory.emit_operation(
                operation, replayed=not claimed, actor_id=command.actor_id
            )
            uow.stage(self._event(command, attempt.id, claimed=claimed))

        outcome = (
            provider.capture(request)
            if claimed
            else provider.retrieve(replace(request, action=ProviderAction.RETRIEVE))
        )
        return self._settle_attempt(
            command, attempt.id, operation.operation_key, outcome, action=ProviderAction.INITIATE
        )

    def cancel_attempt(self, command: CancelPaymentAttempt) -> PaymentReceiptView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            attempt = self.attempt_or_404(command.org_id, command.attempt_id)
            self._svc.attempt_lifecycle.guard_cancellable(attempt)
            intent = self.intent_or_404(command.org_id, attempt.intent_id)
            provider = self.provider_for(attempt)
            operation, request = self._svc.operation_rules.open(
                intent=intent,
                attempt=attempt,
                action=ProviderAction.CANCEL,
                at=self._clock.now(),
                reference=attempt.provider_reference,
            )
            operation, claimed = self._repos.operations.claim(operation)
            uow.stage(self._event(command, attempt.id, claimed=claimed))

        outcome = self._call_provider(
            provider.cancel if claimed else provider.retrieve, request, claimed=claimed
        )
        return self._settle_attempt(
            command,
            attempt.id,
            operation.operation_key,
            outcome,
            action=ProviderAction.CANCEL,
            reason=command.reason,
        )

    # ── the provider edge: the only place a provider is called ──────────────────

    def _call_provider(self, verb, request: ProviderRequest, *, claimed: bool = True):
        """**Every provider call goes through here, and a raised exception becomes `AMBIGUOUS`.**

        P-2 was an S1 defect: an adapter that *raised* instead of returning an outcome skipped
        phase 3 entirely, leaving the attempt in `started` — open, so no retry and no cancel were
        permitted, and reconciliation ignored it because it only resolved `AMBIGUOUS`. One reset
        connection made an intent permanently uncollectable.

        The rule is now the honest one: **if we cannot prove the request never left, we do not know
        whether money moved.** A timeout, a reset, a broken pipe, a bug inside the adapter — all of
        them produce `AMBIGUOUS`, which blocks a blind retry and is resolvable by retrieving the
        same operation key. Only `ProviderNotSent`, which an adapter raises to *assert* nothing was
        transmitted, is safe to read as a local failure.

        `claimed=False` means another worker already holds this operation key, so the verb passed in
        is `retrieve` rather than a second transmission."""
        if not claimed:
            request = replace(request, action=ProviderAction.RETRIEVE)
        try:
            return verb(request)
        except ProviderNotSent as never_sent:
            return ProviderOutcome(
                status=ProviderStatus.FAILED,
                operation_key=request.operation_key,
                provider_code=request.provider_code,
                raw_status="not_sent",
                failure_code="not_sent",
                failure_category=FailureCategory.CONFIGURATION,
                message=str(never_sent),
            )
        except Exception as transport:  # noqa: BLE001 — see the docstring: not knowing is the fact
            return ProviderOutcome(
                status=ProviderStatus.AMBIGUOUS,
                operation_key=request.operation_key,
                provider_code=request.provider_code,
                raw_status="transport_error",
                failure_code=type(transport).__name__,
                failure_category=FailureCategory.RETRIABLE,
                message=f"the provider call did not complete: {transport}",
            )

    def _settle_attempt(
        self,
        command,
        attempt_id: str,
        operation_key: str,
        outcome: ProviderOutcome,
        *,
        action: ProviderAction,
        reason: str = "",
    ) -> PaymentReceiptView:
        """**Phase 3.** One transaction: the observation, both lifecycles, the ledger fact.

        `action` is **required**, and its absence was an S0 defect (P-1). This method is shared by
        every operation, and the same provider status means different things depending on what was
        asked: `SUCCEEDED` after a capture means money moved, and `SUCCEEDED` after a **void** means
        it did not. Reading the second through the first's table recorded a capture for a cancelled
        authorization and posted a ledger credit for money never collected. The interpretation now
        comes from `ACTION_TRIGGERS`, and the Ledger is consulted only when
        `asserts_money_moved(outcome, action)` — a predicate, not an inference.

        `ALREADY_PROCESSED` is still not success for a money-moving action: it means the provider
        recognised our key and is telling us to *ask*. For a **cancel** it is conclusive, because
        "I already voided that" is a complete answer."""
        now = self._clock.now()
        with self._uow.new() as uow:
            attempt = self.attempt_or_404(command.org_id, attempt_id)
            intent = self.intent_or_404(command.org_id, attempt.intent_id)
            operation = self._repos.operations.get(command.org_id, operation_key)
            if operation is not None:
                self._repos.operations.complete(
                    self._svc.operation_rules.close(operation, outcome, at=now)
                )

            recorded = attempt.with_observation(outcome.to_report(received_at=now, verified=True))
            if not outcome_is_actionable(outcome) and action in MONEY_MOVING_ACTIONS:
                stored = self._repos.attempts.record_outcome(recorded)
                self._record(
                    command,
                    PaymentAction.OPERATION_DUPLICATE,
                    intent_id=intent.id,
                    attempt_id=stored.id,
                    operation_key=operation_key,
                    detail=outcome.status.value,
                )
                uow.stage(self._event(command, stored.id, outcome=outcome.status.value))
                return self._receipt(
                    command.org_id, intent, stored, outcome="replayed", ledger_outcome=""
                )

            trigger = self._svc.normalizer.trigger_for(outcome, action)
            if trigger is None or not self._svc.normalizer.may_apply(recorded, outcome, action):
                stored = self._repos.attempts.record_outcome(recorded)
                self._diverge(
                    command,
                    intent,
                    stored,
                    outcome,
                    ReconciliationOutcome.STATE_DIVERGED,
                    detail="a provider outcome arrived that the attempt cannot legally apply",
                )
                uow.stage(self._event(command, stored.id, outcome="conflicting"))
                return self._receipt(
                    command.org_id,
                    intent,
                    stored,
                    outcome="conflicting",
                    ledger_outcome="",
                    requires_reconciliation=True,
                )

            moved = self._svc.attempt_lifecycle.apply(
                recorded, trigger, at=now, outcome=outcome, actor_id=command.actor_id, reason=reason
            )
            entry_id, ledger_outcome = "", ""
            # **The Ledger is reached only when money actually moved.** A void that reaches
            # `SUCCEEDED` is impossible now (the cancel table maps it to `CANCEL`), and this second
            # gate makes that structural rather than dependent on one table staying right.
            if moved.state is AttemptState.SUCCEEDED and self._svc.normalizer.asserts_money_moved(
                outcome, action
            ):
                entry_id, ledger_outcome = self._post_capture(command, intent, moved)
                moved = replace(moved, journal_entry_id=entry_id)
            stored = self._repos.attempts.record_outcome(moved)
            self._factory.emit_attempt_state(stored, actor_id=command.actor_id, reason=reason)

            intent = self._advance_intent(command, intent, stored, at=now)
            self._record(
                command,
                self._action_for(stored.state),
                account_id=intent.account_id,
                intent_id=intent.id,
                attempt_id=stored.id,
                operation_key=operation_key,
                provider_code=stored.provider_code,
                amount=stored.amount,
                reason=reason,
                detail=stored.failure_code,
            )
            uow.stage(self._event(command, stored.id, state=stored.state.value))
            return self._receipt(
                command.org_id,
                intent,
                stored,
                outcome=stored.state.value,
                ledger_outcome=ledger_outcome,
                journal_entry_id=entry_id,
                requires_reconciliation=stored.is_unresolved,
            )

    def _advance_intent(self, command, intent: PaymentIntent, attempt, *, at: float):
        """Move the objective to match the facts. **Position decides, not the attempt.**

        A successful attempt does not by itself mean the objective is met: a partial collection
        leaves an outstanding amount, and reading "an attempt succeeded" as "the invoice is paid"
        is how a half-paid document is reported as settled."""
        if attempt.state is AttemptState.SUCCEEDED:
            # One attempt is always opened for the whole intent amount, so a successful attempt
            # settles the objective. Partial collection is a *second objective* against the same
            # document — the shape M9.16-R1 kept after removing the unreachable `PARTIALLY_PAID`
            # (finding P-5).
            trigger = IntentTrigger.SETTLE
        else:
            from billing.foundation import ATTEMPT_TO_INTENT

            trigger = ATTEMPT_TO_INTENT.get(attempt.state)
        if trigger is None or not self._svc.intent_lifecycle.can(intent.state, trigger):
            return intent
        moved = self._repos.intents.mark_state(
            self._svc.intent_lifecycle.apply(intent, trigger, at=at)
        )
        self._factory.emit_intent_state(moved, actor_id=command.actor_id)
        return moved

    # ── the ledger boundary ─────────────────────────────────────────────────────

    def _post_capture(self, command, intent: PaymentIntent, attempt) -> tuple[str, str]:
        """Cross into M9.13 through **its own command**. Payments writes no ledger row.

        Idempotent end to end: the Ledger derives its entry id from the cause, and the cause is the
        attempt id — so posting the same capture twice produces one financial fact and a recorded
        duplicate.

        **A refused posting does not unmake a capture.** The Ledger can legitimately decline — the
        commonest reason being that no accounting period is open — and the naive shape lets that
        rejection roll back the transaction that was recording money which has *already left the
        customer's account*. That is the "provider succeeded, local write failed" case, and losing
        the capture is the worst available answer to it: the money is gone and nothing in Billing
        says so.

        So the rejection is caught here, exactly once, at the one boundary where an exception means
        "the financial fact could not be recorded" rather than "the operation failed". The capture
        stands, an `UNPOSTED_CAPTURE` divergence is raised against it, and `PostPaymentToLedger`
        repairs it when the period is open. Found by the end-to-end smoke, which captured ₹1,000
        against an account with no open period and got an exception instead of a payment.

        **Narrowed to `BillingError` at M9.16-R1 (finding P-6).** The original `except Exception`
        turned a programming mistake — an `AttributeError`, a `TypeError` — into a financial
        divergence, which is the one place a bug should never be able to hide: it would be recorded
        as "the ledger refused" and queued for an operator who could do nothing about it. A domain
        refusal is a `BillingError`; anything else is a bug and now propagates as one."""
        posting = self._svc.planner.for_capture(intent, attempt)
        try:
            return self._dispatch_posting(command, posting)
        except BillingError as refusal:
            self._diverge(
                command,
                intent,
                attempt,
                ProviderOutcome(
                    status=ProviderStatus.SUCCEEDED,
                    provider_code=attempt.provider_code,
                    reference=attempt.provider_reference,
                    amount=attempt.amount,
                ),
                ReconciliationOutcome.UNPOSTED_CAPTURE,
                detail=f"the ledger refused the posting: {refusal}",
            )
            return "", "refused"

    def _dispatch_posting(self, command, posting) -> tuple[str, str]:
        if self._ledger_post is None:
            return "", "unwired"
        receipt = self._ledger_post(
            PostJournalEntry(
                actor_id=command.actor_id,
                org_id=posting.org_id,
                account_id=posting.account_id,
                entry_type=(
                    LedgerEntryType.REFUND if posting.is_refund else LedgerEntryType.PAYMENT
                ),
                direction=(
                    LedgerDirection.DEBIT if posting.is_refund else LedgerDirection.CREDIT
                ),
                amount_minor=posting.amount.minor,
                currency=posting.amount.currency,
                reference=posting.subject_id,
                description=posting.description,
                source=PostingSource.REFUND if posting.is_refund else PostingSource.PAYMENT,
                source_id=posting.source_id,
                payment_id=posting.attempt_id,
            )
        )
        entry_id = getattr(receipt, "journal_entry_id", "")
        outcome = getattr(receipt, "outcome", "")
        self._factory.emit_ledger_posting(
            posting.source_id,
            org_id=posting.org_id,
            entry_id=entry_id,
            duplicate=outcome == "duplicate",
            actor_id=command.actor_id,
        )
        self._record(
            command,
            PaymentAction.LEDGER_DUPLICATE
            if outcome == "duplicate"
            else PaymentAction.LEDGER_POSTED,
            account_id=posting.account_id,
            intent_id=posting.intent_id,
            attempt_id=posting.attempt_id,
            refund_id=posting.refund_id,
            amount=posting.amount,
            detail=entry_id,
        )
        return entry_id, outcome

    def post_to_ledger(self, command: PostPaymentToLedger) -> PaymentReceiptView:
        """Repair a capture whose ledger posting never landed. Idempotent and attributed."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            attempt = self.attempt_or_404(command.org_id, command.attempt_id)
            if not attempt.is_successful:
                raise _Conflict(
                    f"payment attempt {attempt.id} is {attempt.state.value}; only a captured "
                    "payment produces a financial fact"
                )
            intent = self.intent_or_404(command.org_id, attempt.intent_id)
            entry_id, outcome = self._post_capture(command, intent, attempt)
            stored = attempt
            if entry_id and not attempt.journal_entry_id:
                stored = self._repos.attempts.record_outcome(
                    replace(attempt, journal_entry_id=entry_id)
                )
            uow.stage(self._event(command, stored.id, ledger_outcome=outcome))
            return self._receipt(
                command.org_id,
                intent,
                stored,
                outcome=stored.state.value,
                ledger_outcome=outcome,
                journal_entry_id=entry_id,
            )

    # ── provider events ─────────────────────────────────────────────────────────

    def ingest_event(self, command: IngestProviderEvent) -> ProviderEventView:
        """**The trust boundary.** Verify, deduplicate, correlate, and only then decide.

        Note the order: verification happens before anything is read, deduplication before anything
        is correlated, and the disposition is decided before any state is touched. An event that
        fails any of those is *recorded* — with the reason — and applied to nothing. A webhook that
        can move a state without being verified is a webhook anyone can use to mark invoices
        paid."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        provider = self._svc.registry.get(command.provider_code)
        envelope = (
            provider.verify_event(
                payload=command.payload,
                signature=command.signature,
                received_at=self._clock.now(),
            )
            if provider is not None
            else None
        )
        with self._uow.new() as uow:
            if envelope is None:
                # Nothing to correlate and nothing verified: record that something arrived.
                from billing.foundation import ProviderEventEnvelope

                envelope = ProviderEventEnvelope(
                    provider_code=command.provider_code,
                    event_ref=f"unverified:{self._ids.new('pev')}",
                    event_type="unverified",
                    outcome=ProviderOutcome(status=ProviderStatus.PENDING),
                    verified=False,
                    received_at=self._clock.now(),
                )
                record, _ = self._repos.events.append(
                    self._factory.event_record(
                        org_id=command.org_id,
                        envelope=envelope,
                        disposition=EventDisposition.UNVERIFIED,
                        note="signature verification failed",
                    )
                )
                self._record(
                    command,
                    PaymentAction.EVENT_REJECTED,
                    provider_code=command.provider_code,
                    detail="unverified",
                )
                uow.stage(self._event(command, record.id, disposition="unverified"))
                return payment_event_view(record)

            known = self._repos.events.exists(
                command.org_id, envelope.provider_code, envelope.event_ref
            )
            attempt = self._correlate(command.org_id, envelope)
            disposition = self._svc.normalizer.disposition_for(
                attempt, envelope, known=known
            )
            computed = self._factory.event_record(
                org_id=command.org_id,
                envelope=envelope,
                disposition=disposition,
                attempt_id=attempt.id if attempt else "",
                intent_id=attempt.intent_id if attempt else "",
                note=self._event_note(disposition),
            )
            stored, is_new = self._repos.events.append(computed)
            # **The caller is told what *this* delivery was, not what the first one was.**
            # `append` is idempotent on the provider's own event id, so a redelivery returns the
            # original row — whose disposition still reads `applied`, because it was. Reporting
            # that back would make a redelivery indistinguishable from a first delivery at exactly
            # the surface built to distinguish them. The stored evidence is left untouched
            # (append-only); only the response carries this delivery's disposition.
            record = stored if is_new else replace(computed, id=stored.id)
            self._record(
                command,
                PaymentAction.EVENT_APPLIED
                if disposition is EventDisposition.APPLIED
                else PaymentAction.EVENT_REJECTED,
                attempt_id=attempt.id if attempt else "",
                provider_code=envelope.provider_code,
                detail=disposition.value,
            )
            if disposition is EventDisposition.CONFLICTING and attempt is not None:
                intent = self.intent_or_404(command.org_id, attempt.intent_id)
                self._diverge(
                    command,
                    intent,
                    attempt,
                    envelope.outcome,
                    ReconciliationOutcome.STATE_DIVERGED,
                    detail="a provider event contradicts a settled payment fact",
                )
            uow.stage(self._event(command, record.id, disposition=disposition.value))
            if disposition is not EventDisposition.APPLIED or not is_new or attempt is None:
                return payment_event_view(record)

        # Applying an event settles the attempt through exactly the same phase-3 path a direct
        # provider answer takes — one settlement path, so a webhook and an API response cannot
        # produce two different shapes of truth.
        # The event settles the attempt through the same phase-3 path a direct answer takes — and
        # with the **action the operation actually performed**, read from the operation the key
        # belongs to. A callback about a void must be interpreted as a void (P-1); defaulting every
        # webhook to money-movement semantics is the same defect arriving by a different door.
        settle_key = envelope.outcome.operation_key or attempt.operation_key
        operation = self._repos.operations.get(command.org_id, settle_key)
        self._settle_attempt(
            command,
            attempt.id,
            settle_key,
            envelope.outcome,
            action=operation.action if operation is not None else ProviderAction.INITIATE,
        )
        return payment_event_view(record)

    def _correlate(self, org_id: str, envelope):
        outcome = envelope.outcome
        if outcome.operation_key:
            found = self._repos.attempts.by_operation_key(org_id, outcome.operation_key)
            if found is not None:
                return found
        if outcome.reference:
            return self._repos.attempts.by_provider_reference(
                org_id, envelope.provider_code, outcome.reference
            )
        return None

    @staticmethod
    def _event_note(disposition: EventDisposition) -> str:
        return {
            EventDisposition.DUPLICATE: "this provider event id was already recorded",
            EventDisposition.STALE: "the attempt has already moved past this event",
            EventDisposition.UNCORRELATED: "no payment attempt matches this event",
            EventDisposition.UNSUPPORTED: "this provider event type is not acted on",
            EventDisposition.CONFLICTING: "this event contradicts a settled payment fact",
            EventDisposition.UNVERIFIED: "signature verification failed",
        }.get(disposition, "")

    # ── reconciliation ──────────────────────────────────────────────────────────

    def reconcile_attempt(self, command: ReconcilePaymentAttempt) -> PaymentDivergenceView:
        """Ask the provider about **this exact operation key** and record the comparison.

        The only path that resolves an ambiguous attempt, and it resolves it with the provider's
        answer about the operation already sent — never by sending another one."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new():
            attempt = self.attempt_or_404(command.org_id, command.attempt_id)
            intent = self.intent_or_404(command.org_id, attempt.intent_id)
            provider = self.provider_for(attempt)
            operation = self._repos.operations.get(command.org_id, attempt.operation_key)
            request = self._svc.operation_rules.open(
                intent=intent,
                attempt=attempt,
                action=ProviderAction.RETRIEVE,
                at=self._clock.now(),
                reference=attempt.provider_reference,
                operation_key=attempt.operation_key,
            )[1]

        observed = self._call_provider(provider.retrieve, request)

        with self._uow.new() as uow:
            attempt = self.attempt_or_404(command.org_id, command.attempt_id)
            now = self._clock.now()
            # **Any externally-open attempt is resolvable, not only an ambiguous one** (P-2).
            # An attempt left in `started` by a crashed or raised provider call is exactly as stuck
            # as an ambiguous one — open, so no retry and no cancel — and the previous shape
            # ignored it, which made one connection reset permanently uncollectable.
            was_open = attempt.is_unresolved or attempt.is_open
            outcome = self._svc.reconciliation.compare(
                attempt, observed, at=now, posted=attempt.is_posted
            )
            recorded = attempt.with_observation(
                observed.to_report(received_at=now, verified=True)
            )
            entry_id = ""
            if was_open:
                trigger = self._svc.reconciliation.resolve_open(attempt, observed)
                moved = self._svc.attempt_lifecycle.apply(
                    recorded,
                    trigger,
                    at=now,
                    outcome=observed,
                    actor_id=command.actor_id or "reconciliation",
                    reason="retrieved by operation key",
                )
                if moved.state is AttemptState.SUCCEEDED:
                    entry_id, _ = self._post_capture(command, intent, moved)
                    moved = replace(moved, journal_entry_id=entry_id)
                attempt = self._repos.attempts.record_outcome(moved)
                self._factory.emit_attempt_state(attempt, actor_id=command.actor_id)
                self._factory.emit_resolution(
                    attempt, actor_id=command.actor_id, reason="retrieved by operation key"
                )
                intent = self._advance_intent(command, intent, attempt, at=now)
            else:
                attempt = self._repos.attempts.record_outcome(recorded)
            if operation is not None and operation.is_open:
                closed = (
                    self._svc.operation_rules.close(operation, observed, at=now)
                    if observed.status is not ProviderStatus.FAILED
                    else self._svc.operation_rules.abandon(
                        operation, at=now, reason="the provider has no record of this operation"
                    )
                )
                self._repos.operations.complete(closed)
            finding = self._repos.reconciliations.append(
                self._factory.finding(
                    org_id=command.org_id,
                    outcome=outcome,
                    provider_code=attempt.provider_code,
                    intent_id=intent.id,
                    attempt_id=attempt.id,
                    operation_key=attempt.operation_key,
                    provider_reference=observed.reference,
                    billing_state=attempt.state.value,
                    provider_state=observed.raw_status or observed.status.value,
                    billing_amount=attempt.amount,
                    provider_amount=observed.amount,
                    journal_entry_id=entry_id or attempt.journal_entry_id,
                    detail="retrieved by operation key",
                )
            )
            self._record(
                command,
                PaymentAction.RECONCILED,
                account_id=attempt.account_id,
                intent_id=intent.id,
                attempt_id=attempt.id,
                operation_key=attempt.operation_key,
                detail=outcome.value,
            )
            uow.stage(self._event(command, finding.id, outcome=outcome.value))
            return divergence_view(finding)

    def reconcile_operations(self, command: ReconcileProviderOperations) -> tuple:
        """The crash-recovery sweep. Reads the `SENDING` rows and asks about each one."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new():
            now = self._clock.now()
            stale = self._svc.reconciliation.stale_operations(
                self._repos.operations.open_operations(command.org_id, command.limit),
                now=now,
                older_than=command.older_than_seconds,
            )
            attempt_ids = tuple(o.attempt_id for o in stale)
        results = []
        for attempt_id in attempt_ids:
            results.append(
                self.reconcile_attempt(
                    ReconcilePaymentAttempt(
                        actor_id=command.actor_id, org_id=command.org_id, attempt_id=attempt_id
                    )
                )
            )
        return tuple(results)

    def resolve_divergence(self, command: ResolvePaymentDivergence) -> PaymentDivergenceView:
        """**Always a person.** ADR-0023 PY5 — there is no automatic path to this method."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            found = self._repos.reconciliations.get(command.org_id, command.finding_id)
            if found is None:
                raise _NotFound(f"reconciliation finding {command.finding_id!r} not found")
            self._svc.reconciliation.guard_manual_resolution(found, command.actor_id)
            moved = (
                self._factory.acknowledge_finding(found, actor_id=command.actor_id)
                if command.acknowledge_only
                else self._factory.resolve_finding(
                    found, actor_id=command.actor_id, note=command.note
                )
            )
            stored = self._repos.reconciliations.resolve(moved)
            self._record(
                command,
                PaymentAction.DIVERGENCE_RESOLVED,
                intent_id=stored.intent_id,
                attempt_id=stored.attempt_id,
                reason=command.note,
                detail=stored.outcome.value,
            )
            uow.stage(self._event(command, stored.id, status=stored.status.value))
            return divergence_view(stored)

    # ── refunds ─────────────────────────────────────────────────────────────────

    def _claim_refund_slot(self, command, intent, attempt, amount: Money):
        """**Take this attempt's next refund slot, or lose the race and re-check** (P-3).

        The ceiling — captured minus already-refunded — used to be a read-modify-write, so two
        transactions reading before either committed could each be individually valid and jointly
        excessive. `(org, attempt, sequence)` is now unique, which makes the *second* concurrent
        refund an impossible row until the first commits.

        Losing is therefore not an error: it means somebody else's refund is now committed, so the
        loser re-reads, re-guards against the new ceiling, and takes the next slot — or is rejected
        by the guard, which is the correct outcome when the two together would exceed the capture.
        Bounded retries, because the loop terminates: each round either consumes a slot or the
        ceiling refuses."""
        for _ in range(_REFUND_SLOT_ATTEMPTS):
            existing = self._repos.refunds.for_attempt(command.org_id, attempt.id)
            self._svc.refund_rules.guard_refundable(attempt, existing, amount)
            candidate = self._factory.refund(
                intent=intent,
                attempt=attempt,
                amount=amount,
                sequence=len(existing) + 1,
                reason=command.reason,
                request_key=command.request_key,
                refundable=self._svc.refund_rules.refundable(attempt, existing),
                note=command.note,
                actor_id=command.actor_id,
            )
            try:
                return self._repos.refunds.append(candidate)
            except RefundSequenceConflict:
                continue
        raise _Conflict(
            f"could not take a refund slot on attempt {attempt.id} after "
            f"{_REFUND_SLOT_ATTEMPTS} attempts; retry the request"
        )

    def refund_payment(self, command: RefundPayment) -> CommercialRefundView:
        """Return money — three phases again, because a refund is an external movement too."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        amount = Money(command.amount_minor, command.currency)
        with self._uow.new() as uow:
            attempt = self.attempt_or_404(command.org_id, command.attempt_id)
            intent = self.intent_or_404(command.org_id, attempt.intent_id)
            provider = self.provider_for(attempt)
            refund = self._claim_refund_slot(command, intent, attempt, amount)
            if refund.is_complete or refund.state.value == "failed":
                # Idempotent replay: this request key already ran to completion.
                return refund_view(refund)
            operation, request = self._svc.operation_rules.open(
                intent=intent,
                attempt=attempt,
                action=ProviderAction.REFUND,
                at=self._clock.now(),
                reference=attempt.provider_reference,
                amount=amount,
                operation_key=refund.operation_key,
            )
            operation, claimed = self._repos.operations.claim(operation)
            self._record(
                command,
                PaymentAction.REFUND_REQUESTED,
                account_id=intent.account_id,
                intent_id=intent.id,
                attempt_id=attempt.id,
                refund_id=refund.id,
                amount=amount,
                operation_key=refund.operation_key,
            )
            uow.stage(self._event(command, refund.id, claimed=claimed))

        outcome = self._call_provider(
            provider.refund if claimed else provider.retrieve, request, claimed=claimed
        )

        with self._uow.new() as uow:
            now = self._clock.now()
            operation = self._repos.operations.get(command.org_id, refund.operation_key)
            if operation is not None:
                self._repos.operations.complete(
                    self._svc.operation_rules.close(operation, outcome, at=now)
                )
            if outcome.status in (ProviderStatus.SUCCEEDED, ProviderStatus.ALREADY_PROCESSED):
                completed = self._svc.refund_rules.complete(
                    refund, at=now, reference=outcome.reference
                )
                posting = self._svc.planner.for_refund(completed)
                entry_id, _ = self._dispatch_posting(command, posting)
                completed = replace(completed, journal_entry_id=entry_id)
                stored = self._repos.refunds.mark_state(completed)
                self._factory.emit_refund_state(stored, actor_id=command.actor_id)
                self._record(
                    command,
                    PaymentAction.REFUND_COMPLETED,
                    intent_id=stored.intent_id,
                    attempt_id=stored.attempt_id,
                    refund_id=stored.id,
                    amount=stored.amount,
                    detail=entry_id,
                )
            else:
                stored = self._repos.refunds.mark_state(
                    self._svc.refund_rules.fail(
                        refund, code=outcome.failure_code, message=outcome.message
                    )
                )
                self._factory.emit_refund_state(stored, actor_id=command.actor_id)
                self._record(
                    command,
                    PaymentAction.REFUND_FAILED,
                    intent_id=stored.intent_id,
                    refund_id=stored.id,
                    detail=outcome.failure_code,
                )
            uow.stage(self._event(command, stored.id, state=stored.state.value))
            return refund_view(stored)

    # ── internals ───────────────────────────────────────────────────────────────

    def _diverge(self, command, intent, attempt, outcome, verdict, *, detail: str) -> None:
        finding = self._repos.reconciliations.append(
            self._factory.finding(
                org_id=command.org_id,
                outcome=verdict,
                provider_code=attempt.provider_code,
                intent_id=intent.id,
                attempt_id=attempt.id,
                operation_key=attempt.operation_key,
                provider_reference=outcome.reference,
                billing_state=attempt.state.value,
                provider_state=outcome.raw_status or outcome.status.value,
                billing_amount=attempt.amount,
                provider_amount=outcome.amount,
                detail=detail,
            )
        )
        self._record(
            command,
            PaymentAction.DIVERGENCE_RECORDED,
            intent_id=intent.id,
            attempt_id=attempt.id,
            detail=finding.outcome.value,
        )

    @staticmethod
    def _action_for(state: AttemptState) -> PaymentAction:
        return {
            AttemptState.SUCCEEDED: PaymentAction.ATTEMPT_SUCCEEDED,
            AttemptState.FAILED: PaymentAction.ATTEMPT_FAILED,
            AttemptState.AMBIGUOUS: PaymentAction.ATTEMPT_AMBIGUOUS,
            AttemptState.CANCELLED: PaymentAction.ATTEMPT_CANCELLED,
            AttemptState.AUTHORIZED: PaymentAction.ATTEMPT_AUTHORIZED,
        }.get(state, PaymentAction.ATTEMPT_STARTED)

    def _receipt(
        self,
        org_id: str,
        intent: PaymentIntent,
        attempt,
        *,
        outcome: str,
        ledger_outcome: str,
        journal_entry_id: str = "",
        requires_reconciliation: bool = False,
    ) -> PaymentReceiptView:
        return PaymentReceiptView(
            intent_id=intent.id,
            attempt_id=attempt.id,
            outcome=outcome,
            intent_state=intent.state.value,
            attempt_state=attempt.state.value,
            provider_code=attempt.provider_code,
            provider_reference=attempt.provider_reference,
            action_url=attempt.action_url,
            failure_code=attempt.failure_code,
            message=attempt.failure_message,
            journal_entry_id=journal_entry_id or attempt.journal_entry_id,
            ledger_outcome=ledger_outcome,
            position=payment_position_view(self.position_for(org_id, intent)),
            requires_reconciliation=requires_reconciliation or attempt.is_unresolved,
        )

    def _record(self, command, action: PaymentAction, *, amount: Money | None = None, **fields):
        self._repos.activity.append(
            self._factory.activity(
                org_id=command.org_id,
                action=action,
                actor_id=command.actor_id,
                amount_minor=amount.minor if amount else 0,
                currency=amount.currency if amount else None,
                **fields,
            )
        )

    def _event(self, command, subject_id: str, **data) -> ApplicationEvent:
        return ApplicationEvent(
            id=self._ids.new(PREFIX_APP_EVENT),
            type=ApplicationEventType.PAYMENT_ORCHESTRATED,
            ts=self._clock.now(),
            use_case=type(command).__name__,
            outcome="completed",
            org_id=command.org_id,
            actor=command.actor_id,
            subject_id=subject_id,
            data=dict(data),
        )


class PaymentQueries(_PaymentSupport):
    """Read side. Computes positions from facts and reaches no provider — with one exception that
    is not one: `PreviewPaymentRoute` evaluates the routing table, which is Billing's own."""

    def __init__(self, *, context, repos: PaymentRepositories, services: PaymentServices,
                 authz: AuthorizationOrchestrator, clock) -> None:
        super().__init__(context=context, repos=repos, services=services)
        self._authz = authz
        self._clock = clock

    def _read(self, query) -> None:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)

    def get_intent(self, query: GetPaymentIntent) -> PaymentIntentView:
        self._read(query)
        intent = self.intent_or_404(query.org_id, query.intent_id)
        return intent_view(intent, self._svc.intent_lifecycle.allowed(intent.state))

    def list_intents(self, query: ListPaymentIntents) -> tuple[PaymentIntentView, ...]:
        self._read(query)
        if query.subject_id:
            found = self._repos.intents.for_subject(query.org_id, query.subject_id)
        elif query.account_id:
            found = self._repos.intents.for_account(query.org_id, query.account_id, query.limit)
        else:
            found = self._repos.intents.all(query.org_id, query.limit)
        return tuple(intent_view(i, self._svc.intent_lifecycle.allowed(i.state)) for i in found)

    def get_attempt(self, query: GetPaymentAttempt) -> PaymentAttemptView:
        self._read(query)
        attempt = self.attempt_or_404(query.org_id, query.attempt_id)
        return payment_attempt_view(attempt, self._svc.attempt_lifecycle.allowed(attempt.state))

    def list_attempts(self, query: ListPaymentAttempts) -> tuple[PaymentAttemptView, ...]:
        self._read(query)
        return tuple(
            payment_attempt_view(a, self._svc.attempt_lifecycle.allowed(a.state))
            for a in self.attempts_for(query.org_id, query.intent_id)
        )

    def explain(self, query: ExplainPayment) -> PaymentExplanationView:
        self._read(query)
        intent = self.intent_or_404(query.org_id, query.intent_id)
        attempts = self.attempts_for(query.org_id, intent.id)
        explanation = self._svc.explainer.explain(
            intent,
            attempts,
            operations=self._repos.operations.for_intent(query.org_id, intent.id),
            events=tuple(
                e
                for a in attempts
                for e in self._repos.events.for_attempt(query.org_id, a.id)
            ),
            refunds=self.refunds_for(query.org_id, intent.id),
            findings=tuple(
                f
                for a in attempts
                for f in self._repos.reconciliations.for_attempt(query.org_id, a.id)
            ),
        )
        return PaymentExplanationView(
            intent_id=explanation.intent_id,
            state=explanation.state,
            position=payment_position_view(explanation.position),
            provider_code=explanation.provider_code,
            routing_reason=explanation.routing_reason,
            subject_id=explanation.subject_id,
            subject_digest=explanation.subject_digest,
            attempts=tuple(payment_attempt_view(a) for a in explanation.attempts),
            operations=tuple(operation_view(o) for o in explanation.operations),
            events=tuple(payment_event_view(e) for e in explanation.events),
            findings=tuple(divergence_view(f) for f in explanation.findings),
            journal_entry_ids=explanation.journal_entry_ids,
            notes=explanation.notes,
            is_self_contained=explanation.is_self_contained,
        )

    def inspect(self, query: InspectPayment) -> PaymentInspectionView:
        self._read(query)
        intent = self.intent_or_404(query.org_id, query.intent_id)
        attempts = self.attempts_for(query.org_id, intent.id)
        inspection = self._svc.inspector.inspect(
            intent,
            attempts,
            operations=self._repos.operations.for_intent(query.org_id, intent.id),
            refunds=self.refunds_for(query.org_id, intent.id),
            findings=tuple(
                f
                for a in attempts
                for f in self._repos.reconciliations.for_attempt(query.org_id, a.id)
            ),
        )
        return PaymentInspectionView(
            intent_id=inspection.intent_id,
            state=inspection.state,
            position=payment_position_view(inspection.position),
            is_blocked=inspection.is_blocked,
            has_open_divergence=inspection.has_open_divergence,
            unposted_captures=inspection.unposted_captures,
            open_operations=inspection.open_operations,
            notes=inspection.notes,
            is_clean=inspection.is_clean,
        )

    def diagnose(self, query: DiagnosePayment) -> PaymentDiagnosticsView:
        self._read(query)
        intent = self.intent_or_404(query.org_id, query.intent_id)
        diagnostics = self._svc.validator.validate_intent(intent)
        for attempt in self.attempts_for(query.org_id, intent.id):
            diagnostics = diagnostics.merged(self._svc.validator.validate_attempt(attempt))
        return diagnostics_view(intent.id, diagnostics)

    def position(self, query: GetPaymentPosition) -> PaymentPositionView:
        self._read(query)
        intent = self.intent_or_404(query.org_id, query.intent_id)
        return payment_position_view(self.position_for(query.org_id, intent))

    def list_operations(self, query: ListProviderOperations) -> tuple[ProviderOperationView, ...]:
        self._read(query)
        found = (
            self._repos.operations.open_operations(query.org_id, query.limit)
            if query.open_only
            else self._repos.operations.for_intent(query.org_id, query.intent_id)
        )
        return tuple(operation_view(o) for o in found)

    def list_events(self, query: ListProviderEvents) -> tuple[ProviderEventView, ...]:
        self._read(query)
        found = (
            self._repos.events.for_attempt(query.org_id, query.attempt_id)
            if query.attempt_id
            else self._repos.events.recent(query.org_id, query.limit)
        )
        return tuple(payment_event_view(e) for e in found)

    def list_refunds(self, query: ListPaymentRefunds) -> tuple[CommercialRefundView, ...]:
        self._read(query)
        if query.attempt_id:
            found = self._repos.refunds.for_attempt(query.org_id, query.attempt_id)
        elif query.intent_id:
            found = self._repos.refunds.for_intent(query.org_id, query.intent_id)
        else:
            found = self._repos.refunds.all(query.org_id, query.limit)
        return tuple(refund_view(r) for r in found)

    def refundable(self, query: GetRefundableAmount) -> MoneyView:
        self._read(query)
        attempt = self.attempt_or_404(query.org_id, query.attempt_id)
        return _money(
            self._svc.refund_rules.refundable(
                attempt, self._repos.refunds.for_attempt(query.org_id, attempt.id)
            )
        )

    def list_divergences(self, query: ListPaymentDivergences) -> tuple[PaymentDivergenceView, ...]:
        self._read(query)
        return tuple(
            divergence_view(f)
            for f in self._repos.reconciliations.open_findings(query.org_id, query.limit)
            if not query.open_only or f.needs_attention
        )

    def search(self, query: SearchPayments) -> PaymentSearchView:
        self._read(query)
        result = self._svc.search.search(
            self._repos.intents.all(query.org_id, 500),
            PaymentSearchQuery(
                org_id=query.org_id,
                account_id=query.account_id,
                subject_id=query.subject_id,
                provider_code=query.provider_code,
                state=query.state,
                purpose=query.purpose,
                currency=query.currency,
                limit=query.limit,
            ),
        )
        return PaymentSearchView(
            intents=tuple(intent_view(i) for i in result.intents), total=result.total
        )

    def timeline(self, query: PaymentTimeline) -> tuple[PaymentActivityView, ...]:
        self._read(query)
        found = (
            self._repos.activity.for_intent(query.org_id, query.intent_id)
            if query.intent_id
            else self._repos.activity.history(query.org_id, query.account_id, query.limit)
        )
        return tuple(payment_activity_view(a) for a in found)

    def list_routes(self, query: ListPaymentRoutes) -> tuple[PaymentRoutePolicyView, ...]:
        self._read(query)
        return tuple(
            route_policy_view(p)
            for p in self._repos.routes.all(query.org_id, query.include_retired)
        )

    def preview_route(self, query: PreviewPaymentRoute) -> PaymentRouteView:
        self._read(query)
        return route_view(
            self._svc.router.select(
                self._repos.routes.all(query.org_id),
                currency=query.currency,
                country=query.country,
                method=query.method,
                account_id=query.account_id,
                at=self._clock.now(),
            )
        )


def register_payment_use_cases(command_bus, query_bus, uc: PaymentUseCases, q: PaymentQueries):
    """Wire the payment vocabulary onto the shared buses. One handler per command, no exceptions."""
    command_bus.register(DeclarePaymentRoute, uc.declare_route)
    command_bus.register(RetirePaymentRoute, uc.retire_route)
    command_bus.register(CreatePaymentIntent, uc.create_intent)
    command_bus.register(CancelPaymentIntent, uc.cancel_intent)
    command_bus.register(ExpirePaymentIntent, uc.expire_intent)
    command_bus.register(StartPaymentAttempt, uc.start_attempt)
    command_bus.register(CapturePaymentAttempt, uc.capture_attempt)
    command_bus.register(CancelPaymentAttempt, uc.cancel_attempt)
    command_bus.register(IngestProviderEvent, uc.ingest_event)
    command_bus.register(ReconcilePaymentAttempt, uc.reconcile_attempt)
    command_bus.register(ReconcileProviderOperations, uc.reconcile_operations)
    command_bus.register(ResolvePaymentDivergence, uc.resolve_divergence)
    command_bus.register(RefundPayment, uc.refund_payment)
    command_bus.register(PostPaymentToLedger, uc.post_to_ledger)
    query_bus.register(GetPaymentIntent, q.get_intent)
    query_bus.register(ListPaymentIntents, q.list_intents)
    query_bus.register(GetPaymentAttempt, q.get_attempt)
    query_bus.register(ListPaymentAttempts, q.list_attempts)
    query_bus.register(ExplainPayment, q.explain)
    query_bus.register(InspectPayment, q.inspect)
    query_bus.register(DiagnosePayment, q.diagnose)
    query_bus.register(GetPaymentPosition, q.position)
    query_bus.register(ListProviderOperations, q.list_operations)
    query_bus.register(ListProviderEvents, q.list_events)
    query_bus.register(ListPaymentRefunds, q.list_refunds)
    query_bus.register(GetRefundableAmount, q.refundable)
    query_bus.register(ListPaymentDivergences, q.list_divergences)
    query_bus.register(SearchPayments, q.search)
    query_bus.register(PaymentTimeline, q.timeline)
    query_bus.register(ListPaymentRoutes, q.list_routes)
    query_bus.register(PreviewPaymentRoute, q.preview_route)


__all__ = [
    "ALL_PAYMENT_COMMANDS",
    "ALL_PAYMENT_QUERIES",
    "CancelPaymentAttempt",
    "CancelPaymentIntent",
    "CapturePaymentAttempt",
    "CreatePaymentIntent",
    "DeclarePaymentRoute",
    "DiagnosePayment",
    "ExpirePaymentIntent",
    "ExplainPayment",
    "GetPaymentAttempt",
    "GetPaymentIntent",
    "GetPaymentPosition",
    "GetRefundableAmount",
    "IngestProviderEvent",
    "InspectPayment",
    "ListPaymentAttempts",
    "ListPaymentDivergences",
    "ListPaymentIntents",
    "ListPaymentRefunds",
    "ListPaymentRoutes",
    "ListProviderEvents",
    "ListProviderOperations",
    "PaymentActivityView",
    "PaymentAttemptView",
    "PaymentDiagnosticView",
    "PaymentDiagnosticsView",
    "PaymentDivergenceView",
    "PaymentExplanationView",
    "PaymentInspectionView",
    "PaymentIntentView",
    "PaymentPositionView",
    "PaymentQueries",
    "PaymentReceiptView",
    "PaymentRepositories",
    "PaymentRoutePolicyView",
    "PaymentRouteView",
    "PaymentSearchView",
    "PaymentServices",
    "PaymentTimeline",
    "PaymentUseCases",
    "PostPaymentToLedger",
    "PreviewPaymentRoute",
    "ProviderEventView",
    "ProviderObservationView",
    "ProviderOperationView",
    "ReconcilePaymentAttempt",
    "ReconcileProviderOperations",
    "RefundPayment",
    "CommercialRefundView",
    "ResolvePaymentDivergence",
    "SearchPayments",
    "StartPaymentAttempt",
    "payment_activity_view",
    "payment_attempt_view",
    "divergence_view",
    "payment_event_view",
    "intent_view",
    "operation_view",
    "payment_position_view",
    "refund_view",
    "register_payment_use_cases",
    "route_policy_view",
    "route_view",
]
