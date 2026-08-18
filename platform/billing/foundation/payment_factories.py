"""Payment factories + repository contracts (M9.16).

`PaymentFactory` is the one place an intent, an attempt, an operation, an event record, a refund, a
reconciliation finding, a route policy, or an audit line is constructed. Same M9.2 discipline as
every milestone since: validate structurally, fail fast on error-severity findings
(`BillingValidationError`, §A2.6), compose an immutable aggregate from the injected id and clock
seams, and emit the corresponding `billing.*` fact through the injected emitter.

**Exactly one event per business fact.** A rejected command emits nothing. Two are deliberate and
easy to miss:

* re-sending an operation whose key was already taken emits `billing.payment.operation_replayed`,
  not `operation_sent`. "We transmitted the same key again" is a distinct fact from "we started a
  new movement of money", and an integration that reported them identically would make a retry
  storm indistinguishable from a duplicate-charge incident;
* an event that arrives and is *not* applied still emits `billing.payment.event_rejected`. A
  webhook that was ignored is exactly as interesting to an investigation as one that was applied —
  more so, when it was ignored because its signature failed.

No I/O, no persistence, no networking, and **no provider**. Emission is the only effect and it is
injected.

---

## Why identities are derived rather than minted

Every one of this milestone's ids is a hash of its cause, and each derivation answers a specific
race:

* an **intent** is derived from what is being collected plus the caller's request key, so two
  workers answering one "collect this invoice" produce one objective;
* an **attempt** is derived from `(intent, sequence)`, so two workers that both decide "this is
  attempt 2" collide on a primary key instead of opening two external movements;
* an **operation key** is derived from `(intent, sequence, action)` and is what the *provider*
  receives as its idempotency key — the mechanism by which a lost response cannot become a second
  charge, because the retry carries the same key and the provider returns what it already did;
* a **provider event** is derived from the provider's own event id, so duplicate delivery — which
  every real provider does, by design — is one row rather than two financial facts;
* a **refund** is derived from `(attempt, request key)`, so two concurrent refund requests under
  one key are one refund.

## Why there is no provider name anywhere in this package

Routing is operator-declared configuration (`PaymentRoutePolicy` rows), exactly as tax rates are at
M9.15. A fresh installation routes nowhere and collects nothing. `PaymentRoutePolicyRepository`
has no `update`: a route is retired by flag and a new one appended, because an attempt made in
March must stay explainable against the routing table that was in force in March.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace

from .commercial_payments import (
    AttemptState,
    CommercialRefund,
    EventDisposition,
    IntentState,
    OperationState,
    PaymentAction,
    PaymentActivity,
    PaymentAttempt,
    PaymentIntent,
    PaymentPurpose,
    PaymentReconciliation,
    PaymentRoute,
    PaymentRoutePolicy,
    PaymentSubjectKind,
    ProviderAction,
    ProviderEventEnvelope,
    ProviderEventRecord,
    ProviderOperation,
    ProviderOutcome,
    ProviderStatus,
    ReconciliationOutcome,
    ReconciliationStatus,
    derive_attempt_id,
    derive_commercial_refund_id,
    derive_intent_id,
    derive_operation_key,
    derive_provider_event_id,
    derive_reconciliation_id,
)
from .errors import BillingValidationError
from .events import BillingEvent, BillingEventType, EventEmitter, NullEventEmitter
from .money import Currency, Money
from .payment_rules import PaymentValidator, reconciliation_status_for
from .payments import PaymentMethodKind
from .refunds import RefundDestination, RefundReason, RefundState
from .seams import Clock, IdGenerator, SequentialIdGenerator, SystemClock

PREFIX_PAYMENT_ROUTE = "prt"
PREFIX_PAYMENT_ACTIVITY = "pal"

#: Which `billing.*` fact each attempt state publishes. A table so "exactly one event per business
#: fact" is checkable by iteration rather than asserted in prose. `PENDING` is absent: accepting an
#: operation is not a business fact, it is a provider acknowledging receipt.
ATTEMPT_EVENTS: dict[AttemptState, BillingEventType] = {
    AttemptState.REQUIRES_ACTION: BillingEventType.PAYMENT_ACTION_REQUIRED,
    AttemptState.AUTHORIZED: BillingEventType.PAYMENT_AUTHORIZED,
    AttemptState.SUCCEEDED: BillingEventType.PAYMENT_CAPTURED,
    AttemptState.FAILED: BillingEventType.PAYMENT_FAILED,
    AttemptState.CANCELLED: BillingEventType.PAYMENT_ATTEMPT_CANCELLED,
    AttemptState.AMBIGUOUS: BillingEventType.PAYMENT_ATTEMPT_AMBIGUOUS,
    AttemptState.ABANDONED: BillingEventType.PAYMENT_INTENT_EXPIRED,
}

#: Likewise for the intent lifecycle. Only the facts that close an objective are published: an
#: intent moving from `pending` to `requires_action` is the *attempt's* news, not the objective's.
INTENT_EVENTS: dict[IntentState, BillingEventType] = {
    IntentState.PAID: BillingEventType.PAYMENT_SETTLED,
    IntentState.CANCELLED: BillingEventType.PAYMENT_INTENT_CANCELLED,
    IntentState.EXPIRED: BillingEventType.PAYMENT_INTENT_EXPIRED,
}

#: And for refunds. `billing.refund.*` are M9.2's, reused unchanged.
REFUND_EVENTS: dict[RefundState, BillingEventType] = {
    RefundState.REQUESTED: BillingEventType.REFUND_REQUESTED,
    RefundState.APPROVED: BillingEventType.REFUND_APPROVED,
    RefundState.REJECTED: BillingEventType.REFUND_REJECTED,
    RefundState.REFUNDED: BillingEventType.REFUND_COMPLETED,
    RefundState.FAILED: BillingEventType.REFUND_FAILED,
}


@dataclass(frozen=True, slots=True)
class PaymentFactory:
    """Constructs payment aggregates from injected seams. Stateless; holds references only."""

    ids: IdGenerator = SequentialIdGenerator()
    clock: Clock = SystemClock()
    emitter: EventEmitter = NullEventEmitter()
    validator: PaymentValidator = PaymentValidator()

    # ── routing configuration ───────────────────────────────────────────────────

    def route_policy(
        self,
        *,
        org_id: str,
        provider_code: str,
        priority: int = 100,
        currency: Currency | None = None,
        country: str = "",
        method: PaymentMethodKind | None = None,
        account_id: str = "",
        version: str = "",
        notes: str = "",
        actor_id: str = "",
    ) -> PaymentRoutePolicy:
        """Declare which provider serves which payments. **The only place a provider is named**,
        and it is named by an operator, never by this codebase."""
        policy = PaymentRoutePolicy(
            id=self.ids.new(PREFIX_PAYMENT_ROUTE),
            org_id=org_id,
            provider_code=provider_code,
            priority=priority,
            currency=currency,
            country=country.upper(),
            method=method,
            account_id=account_id,
            version=version,
            declared_at=self.clock.now(),
            declared_by=actor_id,
            notes=notes,
        )
        self._fail_fast(self.validator.validate_route_policy(policy))
        self._emit(
            BillingEventType.PAYMENT_ROUTE_DECLARED,
            org_id,
            policy.id,
            actor_id,
            provider=provider_code,
            priority=priority,
        )
        return policy

    def retire_route(
        self, policy: PaymentRoutePolicy, *, actor_id: str = "", reason: str = ""
    ) -> PaymentRoutePolicy:
        """Retire by flag. The row survives, because an attempt routed under it must stay
        explainable after the route is withdrawn."""
        retired = replace(policy, is_active=False)
        self._emit(
            BillingEventType.PAYMENT_ROUTE_RETIRED,
            policy.org_id,
            policy.id,
            actor_id,
            provider=policy.provider_code,
            reason=reason,
        )
        return retired

    # ── the collection objective ────────────────────────────────────────────────

    def intent(
        self,
        *,
        org_id: str,
        account_id: str,
        amount: Money,
        purpose: PaymentPurpose = PaymentPurpose.INVOICE,
        subject_kind: PaymentSubjectKind = PaymentSubjectKind.INVOICE,
        subject_id: str = "",
        subject_digest: str = "",
        method: PaymentMethodKind = PaymentMethodKind.CARD,
        instrument_id: str = "",
        route: PaymentRoute | None = None,
        request_key: str = "",
        country: str = "",
        description: str = "",
        subject_amount: Money | None = None,
        actor_id: str = "",
    ) -> PaymentIntent:
        """Open a collection objective against an **already-decided** amount.

        `subject_amount` is what the immutable document says is payable. It is used to *validate*
        that this intent does not try to collect more than the document owes — never to compute the
        amount, which arrives from the caller who read the document."""
        now = self.clock.now()
        built = PaymentIntent(
            id=derive_intent_id(
                org_id,
                subject_id,
                purpose.value,
                amount.minor,
                amount.currency.value,
                request_key,
            ),
            org_id=org_id,
            account_id=account_id,
            amount=amount,
            purpose=purpose,
            subject_kind=subject_kind,
            subject_id=subject_id,
            subject_digest=subject_digest,
            state=IntentState.DRAFT,
            method=method,
            instrument_id=instrument_id,
            route=route or PaymentRoute(),
            request_key=request_key,
            country=country.upper(),
            description=description,
            created_at=now,
            created_by=actor_id,
            updated_at=now,
        )
        self._fail_fast(self.validator.validate_intent(built, subject_amount=subject_amount))
        self._emit(
            BillingEventType.PAYMENT_INTENT_CREATED,
            org_id,
            built.id,
            actor_id,
            account=account_id,
            amount_minor=amount.minor,
            currency=amount.currency.value,
            subject=subject_id,
            provider=built.route.provider_code,
        )
        return built

    def attempt(
        self,
        *,
        intent: PaymentIntent,
        sequence: int,
        action: ProviderAction = ProviderAction.INITIATE,
        amount: Money | None = None,
        correlation_id: str = "",
        actor_id: str = "",
    ) -> PaymentAttempt:
        """Open **one** external movement of money. Never rewrites the previous one.

        The `operation_key` is derived here rather than passed in: an attempt that accepted its own
        idempotency key from a caller would let two callers hand it two different keys for one
        movement, which is the same thing as having none."""
        built = PaymentAttempt(
            id=derive_attempt_id(intent.id, sequence),
            org_id=intent.org_id,
            intent_id=intent.id,
            account_id=intent.account_id,
            sequence=sequence,
            amount=amount if amount is not None else intent.amount,
            operation_key=derive_operation_key(intent.id, sequence, action.value),
            state=AttemptState.STARTED,
            provider_code=intent.route.provider_code,
            route=intent.route,
            method=intent.method,
            instrument_id=intent.instrument_id,
            correlation_id=correlation_id,
            started_at=self.clock.now(),
            started_by=actor_id,
        )
        self._fail_fast(self.validator.validate_attempt(built))
        self._emit(
            BillingEventType.PAYMENT_ATTEMPT_STARTED,
            intent.org_id,
            built.id,
            actor_id,
            intent=intent.id,
            sequence=sequence,
            provider=built.provider_code,
            amount_minor=built.amount.minor,
            currency=built.amount.currency.value,
        )
        return built

    def emit_attempt_state(
        self, attempt: PaymentAttempt, *, actor_id: str = "", reason: str = ""
    ) -> None:
        """Publish the fact a settled attempt represents — one per state, from the table."""
        event_type = ATTEMPT_EVENTS.get(attempt.state)
        if event_type is None:
            return
        self._emit(
            event_type,
            attempt.org_id,
            attempt.id,
            actor_id,
            intent=attempt.intent_id,
            provider=attempt.provider_code,
            reference=attempt.provider_reference,
            amount_minor=attempt.amount.minor,
            currency=attempt.amount.currency.value,
            failure_code=attempt.failure_code,
            reason=reason,
        )

    def emit_intent_state(
        self, intent: PaymentIntent, *, actor_id: str = "", reason: str = ""
    ) -> None:
        event_type = INTENT_EVENTS.get(intent.state)
        if event_type is None:
            return
        self._emit(
            event_type,
            intent.org_id,
            intent.id,
            actor_id,
            account=intent.account_id,
            amount_minor=intent.amount.minor,
            currency=intent.amount.currency.value,
            reason=reason,
        )

    def emit_resolution(
        self, attempt: PaymentAttempt, *, actor_id: str = "", reason: str = ""
    ) -> None:
        self._emit(
            BillingEventType.PAYMENT_ATTEMPT_RESOLVED,
            attempt.org_id,
            attempt.id,
            actor_id,
            intent=attempt.intent_id,
            state=attempt.state.value,
            reason=reason,
        )

    def emit_ledger_posting(
        self,
        attempt_or_refund_id: str,
        *,
        org_id: str,
        entry_id: str,
        duplicate: bool,
        actor_id: str = "",
    ) -> None:
        """One fact per posting, including the duplicate — because "we saw this and did nothing"
        is the one outcome that would make the payment-to-ledger path unauditable (M9.13's rule,
        applied at this boundary)."""
        self._emit(
            BillingEventType.PAYMENT_POSTED_TO_LEDGER,
            org_id,
            attempt_or_refund_id,
            actor_id,
            journal_entry=entry_id,
            duplicate=duplicate,
        )

    # ── the provider edge ───────────────────────────────────────────────────────

    def emit_operation(
        self, operation: ProviderOperation, *, replayed: bool = False, actor_id: str = ""
    ) -> None:
        """`operation_sent` versus `operation_replayed`: the same key twice is a different fact
        from a new movement, and an integration that conflated them could not tell a retry storm
        from a duplicate-charge incident."""
        self._emit(
            BillingEventType.PAYMENT_OPERATION_REPLAYED
            if replayed
            else BillingEventType.PAYMENT_OPERATION_SENT,
            operation.org_id,
            operation.attempt_id,
            actor_id,
            operation_key=operation.operation_key,
            action=operation.action.value,
            provider=operation.provider_code,
            attempts_sent=operation.attempts_sent,
        )

    def operation_for(
        self,
        *,
        org_id: str,
        operation_key: str,
        intent_id: str,
        attempt_id: str,
        action: ProviderAction,
        provider_code: str,
        amount: Money,
    ) -> ProviderOperation:
        return ProviderOperation(
            org_id=org_id,
            operation_key=operation_key,
            intent_id=intent_id,
            attempt_id=attempt_id,
            action=action,
            provider_code=provider_code,
            amount=amount,
            state=OperationState.SENDING,
            sent_at=self.clock.now(),
            attempts_sent=1,
        )

    def event_record(
        self,
        *,
        org_id: str,
        envelope: ProviderEventEnvelope,
        disposition: EventDisposition,
        attempt_id: str = "",
        intent_id: str = "",
        note: str = "",
    ) -> ProviderEventRecord:
        """Record what arrived, whatever it was and whatever was done with it.

        The identity is the provider's own event id, so a duplicate delivery derives the same
        primary key and never becomes a second financial fact."""
        outcome = envelope.outcome
        record = ProviderEventRecord(
            id=derive_provider_event_id(org_id, envelope.provider_code, envelope.event_ref),
            org_id=org_id,
            provider_code=envelope.provider_code,
            event_ref=envelope.event_ref,
            event_type=envelope.event_type,
            disposition=disposition,
            verified=envelope.verified,
            attempt_id=attempt_id,
            intent_id=intent_id,
            outcome_status=outcome.status,
            provider_reference=outcome.reference,
            raw_status=outcome.raw_status,
            amount_minor=outcome.amount.minor if outcome.amount else 0,
            currency=outcome.amount.currency if outcome.amount else None,
            provider_ts=envelope.provider_ts,
            received_at=envelope.received_at or self.clock.now(),
            applied_at=(
                envelope.received_at or self.clock.now()
                if disposition is EventDisposition.APPLIED
                else None
            ),
            note=note,
        )
        self._emit(
            BillingEventType.PAYMENT_EVENT_RECEIVED,
            org_id,
            record.id,
            "",
            provider=record.provider_code,
            event_ref=record.event_ref,
            disposition=disposition.value,
        )
        if disposition is EventDisposition.APPLIED:
            self._emit(
                BillingEventType.PAYMENT_EVENT_APPLIED,
                org_id,
                record.id,
                "",
                attempt=attempt_id,
                status=outcome.status.value,
            )
        else:
            self._emit(
                BillingEventType.PAYMENT_EVENT_REJECTED,
                org_id,
                record.id,
                "",
                attempt=attempt_id,
                disposition=disposition.value,
                note=note,
            )
        return record

    # ── refunds ─────────────────────────────────────────────────────────────────

    def refund(
        self,
        *,
        intent: PaymentIntent,
        attempt: PaymentAttempt,
        amount: Money,
        sequence: int = 1,
        reason: RefundReason = RefundReason.CUSTOMER_REQUEST,
        destination: RefundDestination = RefundDestination.ORIGINAL_INSTRUMENT,
        request_key: str = "",
        refundable: Money | None = None,
        note: str = "",
        actor_id: str = "",
    ) -> CommercialRefund:
        """A **new financial fact**, never a state on the payment it reverses."""
        built = CommercialRefund(
            id=derive_commercial_refund_id(intent.org_id, attempt.id, request_key),
            org_id=intent.org_id,
            account_id=intent.account_id,
            intent_id=intent.id,
            attempt_id=attempt.id,
            amount=amount,
            sequence=sequence,
            reason=reason,
            destination=destination,
            state=RefundState.REQUESTED,
            operation_key=derive_operation_key(
                attempt.id, attempt.sequence, f"{ProviderAction.REFUND.value}:{request_key}"
            ),
            provider_code=attempt.provider_code,
            request_key=request_key,
            requested_at=self.clock.now(),
            requested_by=actor_id,
            subject_id=intent.subject_id,
            note=note,
        )
        self._fail_fast(self.validator.validate_refund(built, refundable=refundable))
        self._emit(
            BillingEventType.REFUND_REQUESTED,
            intent.org_id,
            built.id,
            actor_id,
            attempt=attempt.id,
            amount_minor=amount.minor,
            currency=amount.currency.value,
            reason=reason.value,
        )
        return built

    def emit_refund_state(
        self, refund: CommercialRefund, *, actor_id: str = "", reason: str = ""
    ) -> None:
        event_type = REFUND_EVENTS.get(refund.state)
        if event_type is None or refund.state is RefundState.REQUESTED:
            return  # requested is published by `refund()` itself; never twice
        self._emit(
            event_type,
            refund.org_id,
            refund.id,
            actor_id,
            attempt=refund.attempt_id,
            amount_minor=refund.amount.minor,
            currency=refund.amount.currency.value,
            reason=reason,
        )

    # ── reconciliation ──────────────────────────────────────────────────────────

    def finding(
        self,
        *,
        org_id: str,
        outcome: ReconciliationOutcome,
        provider_code: str = "",
        intent_id: str = "",
        attempt_id: str = "",
        operation_key: str = "",
        provider_reference: str = "",
        billing_state: str = "",
        provider_state: str = "",
        billing_amount: Money | None = None,
        provider_amount: Money | None = None,
        journal_entry_id: str = "",
        detail: str = "",
    ) -> PaymentReconciliation:
        """One immutable observation. A divergence is born `OPEN` and there is no argument that
        would let a caller construct it resolved."""
        observed_at = self.clock.now()
        basis = "|".join(
            (
                outcome.value,
                attempt_id,
                operation_key,
                billing_state,
                provider_state,
                str(billing_amount.minor if billing_amount else 0),
                str(provider_amount.minor if provider_amount else 0),
            )
        )
        built = PaymentReconciliation(
            id=derive_reconciliation_id(org_id, attempt_id or intent_id or operation_key, basis),
            org_id=org_id,
            outcome=outcome,
            provider_code=provider_code,
            intent_id=intent_id,
            attempt_id=attempt_id,
            operation_key=operation_key,
            provider_reference=provider_reference,
            billing_state=billing_state,
            provider_state=provider_state,
            billing_amount=billing_amount,
            provider_amount=provider_amount,
            journal_entry_id=journal_entry_id,
            status=reconciliation_status_for(outcome),
            observed_at=observed_at,
            detail=detail,
        )
        self._emit(
            BillingEventType.RECONCILIATION_DIVERGED
            if built.is_divergent
            else BillingEventType.RECONCILIATION_MATCHED,
            org_id,
            built.id,
            "",
            outcome=outcome.value,
            attempt=attempt_id,
            provider=provider_code,
        )
        return built

    def resolve_finding(
        self, finding: PaymentReconciliation, *, actor_id: str, note: str
    ) -> PaymentReconciliation:
        """Resolution is always attributed. ADR-0023 PY5: a divergence is closed by a person."""
        resolved = replace(
            finding,
            status=ReconciliationStatus.RESOLVED,
            resolved_at=self.clock.now(),
            resolved_by=actor_id,
            resolution_note=note,
        )
        self._emit(
            BillingEventType.RECONCILIATION_RESOLVED,
            finding.org_id,
            finding.id,
            actor_id,
            outcome=finding.outcome.value,
            note=note,
        )
        return resolved

    def acknowledge_finding(
        self, finding: PaymentReconciliation, *, actor_id: str
    ) -> PaymentReconciliation:
        return replace(finding, status=ReconciliationStatus.ACKNOWLEDGED, resolved_by=actor_id)

    # ── audit trail ─────────────────────────────────────────────────────────────

    def activity(
        self,
        *,
        org_id: str,
        action: PaymentAction,
        actor_id: str = "",
        account_id: str = "",
        intent_id: str = "",
        attempt_id: str = "",
        refund_id: str = "",
        provider_code: str = "",
        operation_key: str = "",
        amount_minor: int = 0,
        currency: Currency | None = None,
        reason: str = "",
        detail: str = "",
    ) -> PaymentActivity:
        return PaymentActivity(
            id=self.ids.new(PREFIX_PAYMENT_ACTIVITY),
            org_id=org_id,
            action=action,
            actor_id=actor_id,
            at=self.clock.now(),
            account_id=account_id,
            intent_id=intent_id,
            attempt_id=attempt_id,
            refund_id=refund_id,
            provider_code=provider_code,
            operation_key=operation_key,
            amount_minor=amount_minor,
            currency=currency,
            reason=reason,
            detail=detail,
        )

    # ── internals ───────────────────────────────────────────────────────────────

    def _fail_fast(self, diagnostics) -> None:
        if diagnostics.has_errors:
            raise BillingValidationError(diagnostics)

    def _emit(
        self, event_type: BillingEventType, org_id: str, subject_id: str, actor_id: str, **data
    ) -> None:
        self.emitter.emit(
            BillingEvent(
                id=self.ids.new("bev"),
                type=event_type,
                ts=self.clock.now(),
                org_id=org_id,
                subject_id=subject_id,
                actor_id=actor_id,
                data=dict(data),
            )
        )


# ── repository contracts (additive to the M9.2 seam) ────────────────────────────


class PaymentRoutePolicyRepository(ABC):
    """Operator-declared routing. No `update`: a policy is retired by flag and a new one appended,
    because an attempt routed under a policy must stay explainable after the policy changes."""

    @abstractmethod
    def add(self, policy: PaymentRoutePolicy) -> PaymentRoutePolicy: ...
    @abstractmethod
    def get(self, org_id: str, policy_id: str) -> PaymentRoutePolicy | None: ...
    @abstractmethod
    def all(self, org_id: str, include_retired: bool = False) -> tuple[PaymentRoutePolicy, ...]: ...
    @abstractmethod
    def replace(self, policy: PaymentRoutePolicy) -> PaymentRoutePolicy:
        """Retire. The row survives; only `is_active` moves."""


class PaymentIntentRepository(ABC):
    """Collection objectives. `save` is a whole-aggregate upsert on a **derived** id, so two
    workers opening the same objective write one row.

    There is no delete. An intent that was cancelled is evidence that somebody tried to collect."""

    @abstractmethod
    def save(self, intent: PaymentIntent) -> PaymentIntent:
        """Store, or return what is already stored for this cause. Never raises on a duplicate."""

    @abstractmethod
    def get(self, org_id: str, intent_id: str) -> PaymentIntent | None: ...
    @abstractmethod
    def for_subject(self, org_id: str, subject_id: str) -> tuple[PaymentIntent, ...]:
        """Every objective opened against one document — including the ones that failed."""

    @abstractmethod
    def for_account(
        self, org_id: str, account_id: str, limit: int = 200
    ) -> tuple[PaymentIntent, ...]: ...
    @abstractmethod
    def all(self, org_id: str, limit: int = 500) -> tuple[PaymentIntent, ...]: ...
    @abstractmethod
    def mark_state(self, intent: PaymentIntent) -> PaymentIntent:
        """Lifecycle state only. The amount, the subject, and the route are never amended."""


class PaymentAttemptRepository(ABC):
    """**Append-only attempts.**

    `append` writes a new attempt and never overwrites one: the id is derived from
    `(intent, sequence)`, so a second worker computing the same attempt number loses a primary-key
    race rather than replacing the first worker's record of what a provider was told.

    `record_outcome` is the one narrow mutation and it moves the attempt along its FSM — which has
    no edge out of a settled state, so what it can change is bounded by the machine rather than by
    whoever calls it. There is no delete."""

    @abstractmethod
    def append(self, attempt: PaymentAttempt) -> PaymentAttempt:
        """Store, or return what is already stored for this `(intent, sequence)`."""

    @abstractmethod
    def get(self, org_id: str, attempt_id: str) -> PaymentAttempt | None: ...
    @abstractmethod
    def for_intent(self, org_id: str, intent_id: str) -> tuple[PaymentAttempt, ...]:
        """Every attempt for an objective, oldest first — the history a retry never rewrites."""

    @abstractmethod
    def by_provider_reference(
        self, org_id: str, provider_code: str, reference: str
    ) -> PaymentAttempt | None:
        """Correlate a provider callback back to the attempt it describes."""

    @abstractmethod
    def by_operation_key(self, org_id: str, operation_key: str) -> PaymentAttempt | None: ...
    @abstractmethod
    def unresolved(self, org_id: str, limit: int = 200) -> tuple[PaymentAttempt, ...]:
        """Attempts nobody knows the outcome of. The reconciliation work queue."""

    @abstractmethod
    def record_outcome(self, attempt: PaymentAttempt) -> PaymentAttempt:
        """Lifecycle, provider references, and appended observations. Never the amount."""


class ProviderOperationRepository(ABC):
    """**The durable record that a provider call is happening.**

    `claim` is the load-bearing method of the milestone: it takes the operation key or reports that
    somebody else already has it. The uniqueness is `(org_id, operation_key)` as a composite
    primary key — a database constraint, not an application check, because an `if not exists`
    between two workers is a race with a comfortable name.

    There is no delete, and `complete` never removes the row. An operation that was sent is evidence
    that it was sent, whatever came back."""

    @abstractmethod
    def claim(self, operation: ProviderOperation) -> tuple[ProviderOperation, bool]:
        """Take the key. Returns `(operation, claimed)` — `claimed=False` means it was already
        taken and the returned operation is the existing one, which must be *retrieved* rather
        than re-sent."""

    @abstractmethod
    def get(self, org_id: str, operation_key: str) -> ProviderOperation | None: ...
    @abstractmethod
    def for_intent(self, org_id: str, intent_id: str) -> tuple[ProviderOperation, ...]: ...
    @abstractmethod
    def open_operations(self, org_id: str, limit: int = 200) -> tuple[ProviderOperation, ...]:
        """Everything still `SENDING` — the crash-recovery queue."""

    @abstractmethod
    def complete(self, operation: ProviderOperation) -> ProviderOperation:
        """Record what came back. Never removes the row."""


class ProviderEventRepository(ABC):
    """**Append-only provider callbacks.** Identity is the provider's own event id, so duplicate
    delivery is one row. No update and no delete: what arrived, arrived."""

    @abstractmethod
    def append(self, record: ProviderEventRecord) -> tuple[ProviderEventRecord, bool]:
        """Store, or report the duplicate. Returns `(record, is_new)`."""

    @abstractmethod
    def get(self, org_id: str, event_id: str) -> ProviderEventRecord | None: ...
    @abstractmethod
    def exists(self, org_id: str, provider_code: str, event_ref: str) -> bool: ...
    @abstractmethod
    def for_attempt(self, org_id: str, attempt_id: str) -> tuple[ProviderEventRecord, ...]: ...
    @abstractmethod
    def recent(self, org_id: str, limit: int = 100) -> tuple[ProviderEventRecord, ...]: ...


class CommercialRefundRepository(ABC):
    """**Append-only refunds.** A refund is a new fact; the payment it reverses is untouched.
    `mark_state` moves the refund's own lifecycle and amends no figure. No delete."""

    @abstractmethod
    def append(self, refund: CommercialRefund) -> CommercialRefund:
        """Store, or return what is already stored for this `(attempt, request key)`."""

    @abstractmethod
    def get(self, org_id: str, refund_id: str) -> CommercialRefund | None: ...
    @abstractmethod
    def for_attempt(self, org_id: str, attempt_id: str) -> tuple[CommercialRefund, ...]:
        """Every refund against one capture — what bounds the next one."""

    @abstractmethod
    def for_intent(self, org_id: str, intent_id: str) -> tuple[CommercialRefund, ...]: ...
    @abstractmethod
    def all(self, org_id: str, limit: int = 200) -> tuple[CommercialRefund, ...]: ...
    @abstractmethod
    def mark_state(self, refund: CommercialRefund) -> CommercialRefund: ...


class PaymentReconciliationRepository(ABC):
    """**Append-only findings.** `resolve` sets a status, an actor, and a note; it amends no
    observed figure, because the point of a finding is what was seen at the time."""

    @abstractmethod
    def append(self, finding: PaymentReconciliation) -> PaymentReconciliation:
        """Store, or return the existing finding for this same observation — so re-sweeping an
        unchanged divergence does not produce ten thousand rows of one problem."""

    @abstractmethod
    def get(self, org_id: str, finding_id: str) -> PaymentReconciliation | None: ...
    @abstractmethod
    def open_findings(self, org_id: str, limit: int = 200) -> tuple[PaymentReconciliation, ...]: ...
    @abstractmethod
    def for_attempt(self, org_id: str, attempt_id: str) -> tuple[PaymentReconciliation, ...]: ...
    @abstractmethod
    def resolve(self, finding: PaymentReconciliation) -> PaymentReconciliation: ...


class PaymentActivityRepository(ABC):
    """Append-only payment audit trail."""

    @abstractmethod
    def append(self, activity: PaymentActivity) -> PaymentActivity: ...
    @abstractmethod
    def history(
        self, org_id: str, account_id: str = "", limit: int = 100
    ) -> tuple[PaymentActivity, ...]:
        """Newest first. `account_id=""` spans the whole organization."""

    @abstractmethod
    def for_intent(self, org_id: str, intent_id: str) -> tuple[PaymentActivity, ...]: ...


def outcome_is_actionable(outcome: ProviderOutcome) -> bool:
    """Whether an outcome says anything Billing can act on. `ALREADY_PROCESSED` does not — it is an
    instruction to retrieve, and treating it as success is how a duplicate becomes a capture."""
    return outcome.status not in (
        ProviderStatus.ALREADY_PROCESSED,
        ProviderStatus.UNAVAILABLE,
    )


__all__ = [
    "ATTEMPT_EVENTS",
    "INTENT_EVENTS",
    "PREFIX_PAYMENT_ACTIVITY",
    "PREFIX_PAYMENT_ROUTE",
    "REFUND_EVENTS",
    "CommercialRefundRepository",
    "PaymentActivityRepository",
    "PaymentAttemptRepository",
    "PaymentFactory",
    "PaymentIntentRepository",
    "PaymentReconciliationRepository",
    "PaymentRoutePolicyRepository",
    "ProviderEventRepository",
    "ProviderOperationRepository",
    "outcome_is_actionable",
]
