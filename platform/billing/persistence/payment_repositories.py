"""Payment Engine repositories (M9.16) — additive to the M9.4 persistence layer.

Eight SQL implementations of the M9.16 Foundation contracts, reusing the M9.4 machinery unchanged:
the same `_Repo` session handle, the same codec, the same projection-plus-payload mapping, the same
"never commit, never roll back" rule, and the same org-scoped-by-construction reads (§A11.2).

Four expose **no update or delete method at all**: attempts (beyond the FSM-bounded
`record_outcome`), provider events, refunds (beyond `mark_state`), and the audit trail.

## The two race-critical writes

`SqlProviderOperationRepository.claim` is the load-bearing method of the milestone. It is defended
the same three ways every idempotent write since M9.11 has been — a **derived key** (here a
composite primary key on `(org_id, operation_key)`, so two workers computing the same operation
compute the same row), a **read-before-write**, and a **`SAVEPOINT`** whose `IntegrityError` becomes
a read of the winner rather than a destroyed transaction. What makes it different from every earlier
one is the consequence of getting it wrong: the loser of this race must not call the provider, and
`claim` returns `claimed=False` precisely so that it does not.

`SqlPaymentAttemptRepository.append` is defended identically, one layer up: the id is derived from
`(intent, sequence)`, so two workers that both decide "this is attempt 2" write one attempt.

## What no repository here does

None decides a state, chooses a provider, computes a position, bounds a refund, or resolves a
divergence. A repository that made one of those decisions would put the duplicate-charge defence
somewhere nobody would look for it.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from billing.foundation import (
    AttemptState,
    CommercialRefund,
    CommercialRefundRepository,
    OperationState,
    PaymentActivity,
    PaymentActivityRepository,
    PaymentAttempt,
    PaymentAttemptRepository,
    PaymentIntent,
    PaymentIntentRepository,
    PaymentReconciliation,
    PaymentReconciliationRepository,
    PaymentRoutePolicy,
    PaymentRoutePolicyRepository,
    ProviderEventRecord,
    ProviderEventRepository,
    ProviderOperation,
    ProviderOperationRepository,
    ReconciliationStatus,
    RefundSequenceConflict,
)

from . import payment_models as rows
from .codec import encode
from .mapping import from_row, update_row
from .repositories import _Repo


def _route_projection(p: PaymentRoutePolicy) -> dict:
    return {
        "id": p.id,
        "org_id": p.org_id,
        "provider_code": p.provider_code,
        "priority": p.priority,
        "currency": p.currency.value if p.currency else "",
        "country": p.country,
        "method": p.method.value if p.method else "",
        "account_id": p.account_id,
        "version": p.version,
        "is_active": p.is_active,
        "declared_at": p.declared_at,
        "declared_by": p.declared_by,
    }


def _intent_projection(i: PaymentIntent) -> dict:
    return {
        "id": i.id,
        "org_id": i.org_id,
        "account_id": i.account_id,
        "subject_id": i.subject_id,
        "subject_kind": i.subject_kind.value,
        "purpose": i.purpose.value,
        "state": i.state.value,
        "amount_minor": i.amount.minor,
        "currency": i.amount.currency.value,
        "method": i.method.value,
        "provider_code": i.route.provider_code,
        "request_key": i.request_key,
        "attempts_opened": i.attempts_opened,
        "country": i.country,
        "created_at": i.created_at,
        "created_by": i.created_by,
        "updated_at": i.updated_at,
        "closed_at": i.closed_at,
    }


def _attempt_projection(a: PaymentAttempt) -> dict:
    return {
        "id": a.id,
        "org_id": a.org_id,
        "intent_id": a.intent_id,
        "account_id": a.account_id,
        "sequence": a.sequence,
        "state": a.state.value,
        "amount_minor": a.amount.minor,
        "currency": a.amount.currency.value,
        "operation_key": a.operation_key,
        "provider_code": a.provider_code,
        "provider_reference": a.provider_reference,
        "provider_status": a.provider_status,
        "failure_code": a.failure_code,
        "journal_entry_id": a.journal_entry_id,
        "started_at": a.started_at,
        "settled_at": a.settled_at,
    }


def _operation_projection(o: ProviderOperation) -> dict:
    return {
        "org_id": o.org_id,
        "operation_key": o.operation_key,
        "intent_id": o.intent_id,
        "attempt_id": o.attempt_id,
        "action": o.action.value,
        "provider_code": o.provider_code,
        "state": o.state.value,
        "amount_minor": o.amount.minor,
        "currency": o.amount.currency.value,
        "sent_at": o.sent_at,
        "completed_at": o.completed_at,
        "provider_reference": o.provider_reference,
        "provider_status": o.provider_status,
        "outcome_status": o.outcome_status.value if o.outcome_status else "",
        "attempts_sent": o.attempts_sent,
        "last_error": o.last_error,
    }


def _event_projection(e: ProviderEventRecord) -> dict:
    return {
        "id": e.id,
        "org_id": e.org_id,
        "provider_code": e.provider_code,
        "event_ref": e.event_ref,
        "event_type": e.event_type,
        "disposition": e.disposition.value,
        "verified": e.verified,
        "attempt_id": e.attempt_id,
        "intent_id": e.intent_id,
        "outcome_status": e.outcome_status.value if e.outcome_status else "",
        "provider_reference": e.provider_reference,
        "amount_minor": e.amount_minor,
        "currency": e.currency.value if e.currency else "",
        "provider_ts": e.provider_ts,
        "received_at": e.received_at,
        "applied_at": e.applied_at,
    }


def _refund_projection(r: CommercialRefund) -> dict:
    return {
        "id": r.id,
        "org_id": r.org_id,
        "account_id": r.account_id,
        "intent_id": r.intent_id,
        "attempt_id": r.attempt_id,
        "sequence": r.sequence,
        "state": r.state.value,
        "amount_minor": r.amount.minor,
        "currency": r.amount.currency.value,
        "reason": r.reason.value,
        "destination": r.destination.value,
        "operation_key": r.operation_key,
        "provider_code": r.provider_code,
        "provider_reference": r.provider_reference,
        "request_key": r.request_key,
        "requested_at": r.requested_at,
        "requested_by": r.requested_by,
        "completed_at": r.completed_at,
        "journal_entry_id": r.journal_entry_id,
    }


def _finding_projection(f: PaymentReconciliation) -> dict:
    return {
        "id": f.id,
        "org_id": f.org_id,
        "outcome": f.outcome.value,
        "status": f.status.value,
        "provider_code": f.provider_code,
        "intent_id": f.intent_id,
        "attempt_id": f.attempt_id,
        "operation_key": f.operation_key,
        "provider_reference": f.provider_reference,
        "billing_state": f.billing_state,
        "provider_state": f.provider_state,
        "billing_amount_minor": f.billing_amount.minor if f.billing_amount else 0,
        "provider_amount_minor": f.provider_amount.minor if f.provider_amount else 0,
        "currency": (
            f.billing_amount.currency.value
            if f.billing_amount
            else (f.provider_amount.currency.value if f.provider_amount else "")
        ),
        "journal_entry_id": f.journal_entry_id,
        "observed_at": f.observed_at,
        "resolved_at": f.resolved_at,
        "resolved_by": f.resolved_by,
    }


def _activity_projection(a: PaymentActivity) -> dict:
    return {
        "id": a.id,
        "org_id": a.org_id,
        "action": a.action.value,
        "actor_id": a.actor_id,
        "at": a.at,
        "account_id": a.account_id,
        "intent_id": a.intent_id,
        "attempt_id": a.attempt_id,
        "refund_id": a.refund_id,
        "provider_code": a.provider_code,
        "operation_key": a.operation_key,
        "amount_minor": a.amount_minor,
        "currency": a.currency.value if a.currency else "",
    }


class SqlPaymentRoutePolicyRepository(_Repo, PaymentRoutePolicyRepository):
    """Routing configuration. `replace` retires by flag; no row is ever removed."""

    def _row(self, org_id: str, policy_id: str):
        return self._one(
            select(rows.BillingPaymentRouteRow).where(
                rows.BillingPaymentRouteRow.org_id == org_id,
                rows.BillingPaymentRouteRow.id == policy_id,
            )
        )

    def add(self, policy: PaymentRoutePolicy) -> PaymentRoutePolicy:
        self._add(
            rows.BillingPaymentRouteRow(payload=encode(policy), **_route_projection(policy))
        )
        return policy

    def get(self, org_id: str, policy_id: str) -> PaymentRoutePolicy | None:
        row = self._row(org_id, policy_id)
        return from_row(PaymentRoutePolicy, row) if row else None

    def all(self, org_id: str, include_retired: bool = False) -> tuple[PaymentRoutePolicy, ...]:
        statement = select(rows.BillingPaymentRouteRow).where(
            rows.BillingPaymentRouteRow.org_id == org_id
        )
        if not include_retired:
            statement = statement.where(rows.BillingPaymentRouteRow.is_active.is_(True))
        found = self._all(statement.order_by(rows.BillingPaymentRouteRow.priority))
        return tuple(from_row(PaymentRoutePolicy, r) for r in found)

    def replace(self, policy: PaymentRoutePolicy) -> PaymentRoutePolicy:
        row = self._row(policy.org_id, policy.id)
        if row is None:
            return self.add(policy)
        update_row(row, policy, _route_projection(policy))
        self._session.flush()
        return policy


class SqlPaymentIntentRepository(_Repo, PaymentIntentRepository):
    """Collection objectives, keyed by a derived id so one request is one objective."""

    def _row(self, org_id: str, intent_id: str):
        return self._one(
            select(rows.BillingPaymentIntentRow).where(
                rows.BillingPaymentIntentRow.org_id == org_id,
                rows.BillingPaymentIntentRow.id == intent_id,
            )
        )

    def save(self, intent: PaymentIntent) -> PaymentIntent:
        existing = self._row(intent.org_id, intent.id)
        if existing is not None:
            return from_row(PaymentIntent, existing)
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingPaymentIntentRow(
                        payload=encode(intent), **_intent_projection(intent)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._row(intent.org_id, intent.id)
            if winner is None:  # pragma: no cover — the primary key guarantees a row
                raise
            return from_row(PaymentIntent, winner)
        return intent

    def get(self, org_id: str, intent_id: str) -> PaymentIntent | None:
        row = self._row(org_id, intent_id)
        return from_row(PaymentIntent, row) if row else None

    def for_subject(self, org_id: str, subject_id: str) -> tuple[PaymentIntent, ...]:
        found = self._all(
            select(rows.BillingPaymentIntentRow)
            .where(
                rows.BillingPaymentIntentRow.org_id == org_id,
                rows.BillingPaymentIntentRow.subject_id == subject_id,
            )
            .order_by(rows.BillingPaymentIntentRow.created_at)
        )
        return tuple(from_row(PaymentIntent, r) for r in found)

    def for_account(
        self, org_id: str, account_id: str, limit: int = 200
    ) -> tuple[PaymentIntent, ...]:
        found = self._all(
            select(rows.BillingPaymentIntentRow)
            .where(
                rows.BillingPaymentIntentRow.org_id == org_id,
                rows.BillingPaymentIntentRow.account_id == account_id,
            )
            .order_by(rows.BillingPaymentIntentRow.created_at.desc())
            .limit(limit)
        )
        return tuple(from_row(PaymentIntent, r) for r in found)

    def all(self, org_id: str, limit: int = 500) -> tuple[PaymentIntent, ...]:
        found = self._all(
            select(rows.BillingPaymentIntentRow)
            .where(rows.BillingPaymentIntentRow.org_id == org_id)
            .order_by(rows.BillingPaymentIntentRow.created_at.desc())
            .limit(limit)
        )
        return tuple(from_row(PaymentIntent, r) for r in found)

    def mark_state(self, intent: PaymentIntent) -> PaymentIntent:
        row = self._row(intent.org_id, intent.id)
        if row is None:
            return self.save(intent)
        update_row(row, intent, _intent_projection(intent))
        self._session.flush()
        return intent


class SqlPaymentAttemptRepository(_Repo, PaymentAttemptRepository):
    """**Append-only attempts.** `record_outcome` moves the FSM and appends observations; it has no
    path to the amount, the sequence, or the operation key."""

    def _row(self, org_id: str, attempt_id: str):
        return self._one(
            select(rows.BillingPaymentAttemptRow).where(
                rows.BillingPaymentAttemptRow.org_id == org_id,
                rows.BillingPaymentAttemptRow.id == attempt_id,
            )
        )

    def append(self, attempt: PaymentAttempt) -> PaymentAttempt:
        existing = self._row(attempt.org_id, attempt.id)
        if existing is not None:
            return from_row(PaymentAttempt, existing)
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingPaymentAttemptRow(
                        payload=encode(attempt), **_attempt_projection(attempt)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._row(attempt.org_id, attempt.id)
            if winner is None:  # pragma: no cover — the constraint guarantees a row
                raise
            return from_row(PaymentAttempt, winner)
        return attempt

    def get(self, org_id: str, attempt_id: str) -> PaymentAttempt | None:
        row = self._row(org_id, attempt_id)
        return from_row(PaymentAttempt, row) if row else None

    def for_intent(self, org_id: str, intent_id: str) -> tuple[PaymentAttempt, ...]:
        found = self._all(
            select(rows.BillingPaymentAttemptRow)
            .where(
                rows.BillingPaymentAttemptRow.org_id == org_id,
                rows.BillingPaymentAttemptRow.intent_id == intent_id,
            )
            .order_by(rows.BillingPaymentAttemptRow.sequence)
        )
        return tuple(from_row(PaymentAttempt, r) for r in found)

    def by_provider_reference(
        self, org_id: str, provider_code: str, reference: str
    ) -> PaymentAttempt | None:
        row = self._one(
            select(rows.BillingPaymentAttemptRow).where(
                rows.BillingPaymentAttemptRow.org_id == org_id,
                rows.BillingPaymentAttemptRow.provider_code == provider_code,
                rows.BillingPaymentAttemptRow.provider_reference == reference,
            )
        )
        return from_row(PaymentAttempt, row) if row else None

    def by_operation_key(self, org_id: str, operation_key: str) -> PaymentAttempt | None:
        row = self._one(
            select(rows.BillingPaymentAttemptRow).where(
                rows.BillingPaymentAttemptRow.org_id == org_id,
                rows.BillingPaymentAttemptRow.operation_key == operation_key,
            )
        )
        return from_row(PaymentAttempt, row) if row else None

    def unresolved(self, org_id: str, limit: int = 200) -> tuple[PaymentAttempt, ...]:
        found = self._all(
            select(rows.BillingPaymentAttemptRow)
            .where(
                rows.BillingPaymentAttemptRow.org_id == org_id,
                rows.BillingPaymentAttemptRow.state == AttemptState.AMBIGUOUS.value,
            )
            .order_by(rows.BillingPaymentAttemptRow.started_at)
            .limit(limit)
        )
        return tuple(from_row(PaymentAttempt, r) for r in found)

    def record_outcome(self, attempt: PaymentAttempt) -> PaymentAttempt:
        row = self._row(attempt.org_id, attempt.id)
        if row is None:
            return self.append(attempt)
        update_row(row, attempt, _attempt_projection(attempt))
        self._session.flush()
        return attempt


class SqlProviderOperationRepository(_Repo, ProviderOperationRepository):
    """**The claim.** One key, one winner, decided by a composite primary key.

    `claim` returning `claimed=False` is not a failure — it is the instruction not to call the
    provider. Everything else in the milestone rests on this method being a database constraint
    rather than a read followed by a write."""

    def _row(self, org_id: str, operation_key: str):
        return self._one(
            select(rows.BillingPaymentOperationRow).where(
                rows.BillingPaymentOperationRow.org_id == org_id,
                rows.BillingPaymentOperationRow.operation_key == operation_key,
            )
        )

    def claim(self, operation: ProviderOperation) -> tuple[ProviderOperation, bool]:
        existing = self._row(operation.org_id, operation.operation_key)
        if existing is not None:
            return from_row(ProviderOperation, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingPaymentOperationRow(
                        payload=encode(operation), **_operation_projection(operation)
                    )
                )
                self._session.flush()
        except IntegrityError:
            # Another worker took the key between the read and the insert. It is sending, or has
            # sent; this caller must retrieve rather than transmit a second time.
            winner = self._row(operation.org_id, operation.operation_key)
            if winner is None:  # pragma: no cover — the primary key guarantees a row
                raise
            return from_row(ProviderOperation, winner), False
        return operation, True

    def get(self, org_id: str, operation_key: str) -> ProviderOperation | None:
        row = self._row(org_id, operation_key)
        return from_row(ProviderOperation, row) if row else None

    def for_intent(self, org_id: str, intent_id: str) -> tuple[ProviderOperation, ...]:
        found = self._all(
            select(rows.BillingPaymentOperationRow)
            .where(
                rows.BillingPaymentOperationRow.org_id == org_id,
                rows.BillingPaymentOperationRow.intent_id == intent_id,
            )
            .order_by(rows.BillingPaymentOperationRow.sent_at)
        )
        return tuple(from_row(ProviderOperation, r) for r in found)

    def open_operations(self, org_id: str, limit: int = 200) -> tuple[ProviderOperation, ...]:
        found = self._all(
            select(rows.BillingPaymentOperationRow)
            .where(
                rows.BillingPaymentOperationRow.org_id == org_id,
                rows.BillingPaymentOperationRow.state == OperationState.SENDING.value,
            )
            .order_by(rows.BillingPaymentOperationRow.sent_at)
            .limit(limit)
        )
        return tuple(from_row(ProviderOperation, r) for r in found)

    def complete(self, operation: ProviderOperation) -> ProviderOperation:
        row = self._row(operation.org_id, operation.operation_key)
        if row is None:
            return self.claim(operation)[0]
        update_row(row, operation, _operation_projection(operation))
        self._session.flush()
        return operation


class SqlProviderEventRepository(_Repo, ProviderEventRepository):
    """**Append-only callbacks.** `append` returns `is_new=False` for a duplicate delivery rather
    than raising — a provider retrying its own webhook is the normal path, not an error."""

    def _row(self, org_id: str, event_id: str):
        return self._one(
            select(rows.BillingProviderEventRow).where(
                rows.BillingProviderEventRow.org_id == org_id,
                rows.BillingProviderEventRow.id == event_id,
            )
        )

    def append(self, record: ProviderEventRecord) -> tuple[ProviderEventRecord, bool]:
        existing = self._row(record.org_id, record.id)
        if existing is not None:
            return from_row(ProviderEventRecord, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingProviderEventRow(
                        payload=encode(record), **_event_projection(record)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._row(record.org_id, record.id)
            if winner is None:  # pragma: no cover — the constraint guarantees a row
                raise
            return from_row(ProviderEventRecord, winner), False
        return record, True

    def get(self, org_id: str, event_id: str) -> ProviderEventRecord | None:
        row = self._row(org_id, event_id)
        return from_row(ProviderEventRecord, row) if row else None

    def exists(self, org_id: str, provider_code: str, event_ref: str) -> bool:
        return (
            self._one(
                select(rows.BillingProviderEventRow).where(
                    rows.BillingProviderEventRow.org_id == org_id,
                    rows.BillingProviderEventRow.provider_code == provider_code,
                    rows.BillingProviderEventRow.event_ref == event_ref,
                )
            )
            is not None
        )

    def for_attempt(self, org_id: str, attempt_id: str) -> tuple[ProviderEventRecord, ...]:
        found = self._all(
            select(rows.BillingProviderEventRow)
            .where(
                rows.BillingProviderEventRow.org_id == org_id,
                rows.BillingProviderEventRow.attempt_id == attempt_id,
            )
            .order_by(rows.BillingProviderEventRow.received_at)
        )
        return tuple(from_row(ProviderEventRecord, r) for r in found)

    def recent(self, org_id: str, limit: int = 100) -> tuple[ProviderEventRecord, ...]:
        found = self._all(
            select(rows.BillingProviderEventRow)
            .where(rows.BillingProviderEventRow.org_id == org_id)
            .order_by(rows.BillingProviderEventRow.received_at.desc())
            .limit(limit)
        )
        return tuple(from_row(ProviderEventRecord, r) for r in found)


class SqlCommercialRefundRepository(_Repo, CommercialRefundRepository):
    """**Append-only refunds.** Nothing here touches the attempt a refund reverses."""

    def _row(self, org_id: str, refund_id: str):
        return self._one(
            select(rows.BillingCommercialRefundRow).where(
                rows.BillingCommercialRefundRow.org_id == org_id,
                rows.BillingCommercialRefundRow.id == refund_id,
            )
        )

    def append(self, refund: CommercialRefund) -> CommercialRefund:
        """Store, or resolve the collision — and the two collisions mean different things.

        A **primary-key** collision is the same request key twice: one refund, returned
        idempotently.
        A **sequence** collision is a different request key that lost the race for this attempt's
        next refund slot, and it must *not* be treated as a replay — the amount may differ and the
        ceiling has moved. It raises `RefundSequenceConflict` so the caller re-reads and re-checks
        against the winner (M9.16-R1, finding P-3)."""
        existing = self._row(refund.org_id, refund.id)
        if existing is not None:
            return from_row(CommercialRefund, existing)
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingCommercialRefundRow(
                        payload=encode(refund), **_refund_projection(refund)
                    )
                )
                self._session.flush()
        except IntegrityError as clash:
            winner = self._row(refund.org_id, refund.id)
            if winner is not None:
                return from_row(CommercialRefund, winner)
            raise RefundSequenceConflict(
                f"refund sequence {refund.sequence} for attempt {refund.attempt_id} was taken by "
                "another worker; re-read the refunds and re-check the ceiling"
            ) from clash
        return refund

    def get(self, org_id: str, refund_id: str) -> CommercialRefund | None:
        row = self._row(org_id, refund_id)
        return from_row(CommercialRefund, row) if row else None

    def for_attempt(self, org_id: str, attempt_id: str) -> tuple[CommercialRefund, ...]:
        found = self._all(
            select(rows.BillingCommercialRefundRow)
            .where(
                rows.BillingCommercialRefundRow.org_id == org_id,
                rows.BillingCommercialRefundRow.attempt_id == attempt_id,
            )
            .order_by(rows.BillingCommercialRefundRow.requested_at)
        )
        return tuple(from_row(CommercialRefund, r) for r in found)

    def for_intent(self, org_id: str, intent_id: str) -> tuple[CommercialRefund, ...]:
        found = self._all(
            select(rows.BillingCommercialRefundRow)
            .where(
                rows.BillingCommercialRefundRow.org_id == org_id,
                rows.BillingCommercialRefundRow.intent_id == intent_id,
            )
            .order_by(rows.BillingCommercialRefundRow.requested_at)
        )
        return tuple(from_row(CommercialRefund, r) for r in found)

    def all(self, org_id: str, limit: int = 200) -> tuple[CommercialRefund, ...]:
        found = self._all(
            select(rows.BillingCommercialRefundRow)
            .where(rows.BillingCommercialRefundRow.org_id == org_id)
            .order_by(rows.BillingCommercialRefundRow.requested_at.desc())
            .limit(limit)
        )
        return tuple(from_row(CommercialRefund, r) for r in found)

    def mark_state(self, refund: CommercialRefund) -> CommercialRefund:
        row = self._row(refund.org_id, refund.id)
        if row is None:
            return self.append(refund)
        update_row(row, refund, _refund_projection(refund))
        self._session.flush()
        return refund


class SqlPaymentReconciliationRepository(_Repo, PaymentReconciliationRepository):
    """**Append-only findings.** `resolve` sets a status, an actor, and a note — never a figure."""

    def _row(self, org_id: str, finding_id: str):
        return self._one(
            select(rows.BillingPaymentReconciliationRow).where(
                rows.BillingPaymentReconciliationRow.org_id == org_id,
                rows.BillingPaymentReconciliationRow.id == finding_id,
            )
        )

    def append(self, finding: PaymentReconciliation) -> PaymentReconciliation:
        existing = self._row(finding.org_id, finding.id)
        if existing is not None:
            return from_row(PaymentReconciliation, existing)
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingPaymentReconciliationRow(
                        payload=encode(finding), **_finding_projection(finding)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._row(finding.org_id, finding.id)
            if winner is None:  # pragma: no cover — the primary key guarantees a row
                raise
            return from_row(PaymentReconciliation, winner)
        return finding

    def get(self, org_id: str, finding_id: str) -> PaymentReconciliation | None:
        row = self._row(org_id, finding_id)
        return from_row(PaymentReconciliation, row) if row else None

    def open_findings(self, org_id: str, limit: int = 200) -> tuple[PaymentReconciliation, ...]:
        found = self._all(
            select(rows.BillingPaymentReconciliationRow)
            .where(
                rows.BillingPaymentReconciliationRow.org_id == org_id,
                rows.BillingPaymentReconciliationRow.status != ReconciliationStatus.RESOLVED.value,
            )
            .order_by(rows.BillingPaymentReconciliationRow.observed_at.desc())
            .limit(limit)
        )
        return tuple(from_row(PaymentReconciliation, r) for r in found)

    def for_attempt(self, org_id: str, attempt_id: str) -> tuple[PaymentReconciliation, ...]:
        found = self._all(
            select(rows.BillingPaymentReconciliationRow)
            .where(
                rows.BillingPaymentReconciliationRow.org_id == org_id,
                rows.BillingPaymentReconciliationRow.attempt_id == attempt_id,
            )
            .order_by(rows.BillingPaymentReconciliationRow.observed_at)
        )
        return tuple(from_row(PaymentReconciliation, r) for r in found)

    def resolve(self, finding: PaymentReconciliation) -> PaymentReconciliation:
        row = self._row(finding.org_id, finding.id)
        if row is None:
            return self.append(finding)
        update_row(row, finding, _finding_projection(finding))
        self._session.flush()
        return finding


class SqlPaymentActivityRepository(_Repo, PaymentActivityRepository):
    """Append-only payment audit trail. No update, no delete."""

    def append(self, activity: PaymentActivity) -> PaymentActivity:
        self._add(
            rows.BillingPaymentActivityRow(
                payload=encode(activity), **_activity_projection(activity)
            )
        )
        return activity

    def history(
        self, org_id: str, account_id: str = "", limit: int = 100
    ) -> tuple[PaymentActivity, ...]:
        statement = select(rows.BillingPaymentActivityRow).where(
            rows.BillingPaymentActivityRow.org_id == org_id
        )
        if account_id:
            statement = statement.where(
                rows.BillingPaymentActivityRow.account_id == account_id
            )
        found = self._all(
            statement.order_by(rows.BillingPaymentActivityRow.at.desc()).limit(limit)
        )
        return tuple(from_row(PaymentActivity, r) for r in found)

    def for_intent(self, org_id: str, intent_id: str) -> tuple[PaymentActivity, ...]:
        found = self._all(
            select(rows.BillingPaymentActivityRow)
            .where(
                rows.BillingPaymentActivityRow.org_id == org_id,
                rows.BillingPaymentActivityRow.intent_id == intent_id,
            )
            .order_by(rows.BillingPaymentActivityRow.at)
        )
        return tuple(from_row(PaymentActivity, r) for r in found)


__all__ = [
    "SqlCommercialRefundRepository",
    "SqlPaymentActivityRepository",
    "SqlPaymentAttemptRepository",
    "SqlPaymentIntentRepository",
    "SqlPaymentReconciliationRepository",
    "SqlPaymentRoutePolicyRepository",
    "SqlProviderEventRepository",
    "SqlProviderOperationRepository",
]
