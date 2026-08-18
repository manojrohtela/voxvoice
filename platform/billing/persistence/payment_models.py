"""Payment Engine tables (M9.16) — additive to the M9.4–M9.15 schema.

Eight new `cp_billing_*` tables in the same projection-plus-payload row shape M9.4 established. No
existing table is altered, no column is dropped, and no Epic 5–8 table is touched — including
M9.2's `cp_billing_payment`, `cp_billing_payment_instrument`, `cp_billing_refund`,
`cp_billing_dunning`, and `cp_billing_reconciliation`, which stay exactly as they are.

Four are **append-only by construction**: attempts, provider events, refunds, and the audit trail.
There is no update path to any of them beyond the narrow lifecycle column each declares.

## The row this milestone is actually built on

`cp_billing_payment_operation` has a **composite primary key** on `(org_id, operation_key)`, and
that is the entire duplicate-charge defence expressed as a constraint. Two workers that both decide
to move the same money compute the same key, both try to insert, and the database picks exactly one
winner. The loser does not send anything — it retrieves what the winner did. An application-level
`if not exists` cannot do this, because between the check and the insert there is a window, and the
window is the incident.

The row is committed **before** the provider is called and is only closed after the answer is
recorded, so a crash anywhere in between leaves a `SENDING` row naming the exact key to ask about.
That row is the difference between an ambiguous outcome being recoverable and being merely
regrettable.

## What is deliberately absent from every column

There is no `pan`, no `card_number`, no `cvv`, no `expiry`, no `iban`, no `account_number`, no
`secret`, and no raw provider payload — not as a column and not inside a payload blob, because
`ProviderOutcome` has no field that could carry one. Instrument data never enters this database
(ADR-0023 PY6); what is stored is a provider *token reference*, which is meaningless anywhere but
at the adapter that issued it.

## The second constraint this milestone rests on

`cp_billing_payment_refund` carries a unique `(org_id, attempt_id, sequence)` — added at M9.16-R1
for finding P-3. Refunding is the one remaining place where correctness depended on a
read-modify-write: two concurrent refunds under *different* request keys each read the same
already-refunded total and each passed a ceiling the other was about to consume. The sequence makes
the second refund an impossible row until the first commits, so the loser re-reads and re-checks
against the winner. Same shape as the attempt sequence, same reason, same arbitrator.
"""

from __future__ import annotations

from sqlalchemy import JSON as SAJSON
from sqlalchemy import Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def _payload() -> object:
    return Field(default_factory=dict, sa_column=Column("payload", SAJSON))


class BillingPaymentRouteRow(SQLModel, table=True):
    """Operator-declared provider selection. Retired by flag, never removed: an attempt routed
    under a policy must stay explainable after the policy is withdrawn."""

    __tablename__ = "cp_billing_payment_route"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    provider_code: str = Field(index=True)
    priority: int = Field(default=100, index=True)
    currency: str = Field(default="", index=True)
    country: str = Field(default="", index=True)
    method: str = Field(default="", index=True)
    account_id: str = Field(default="", index=True)
    version: str = ""
    is_active: bool = Field(default=True, index=True)
    declared_at: float = Field(default=0.0, index=True)
    declared_by: str = ""
    payload: dict = _payload()  # type: ignore[assignment]


class BillingPaymentIntentRow(SQLModel, table=True):
    """A collection objective. The id is **derived** from what is being collected plus the caller's
    request key, so two workers answering one request write one row.

    Note the columns that do not exist: no `collected`, no `refunded`, and no `outstanding`.
    A payment total in a column is a second authority on a figure the Ledger owns, and it is the one
    that goes stale."""

    __tablename__ = "cp_billing_payment_intent"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    subject_id: str = Field(default="", index=True)
    subject_kind: str = Field(default="invoice", index=True)
    purpose: str = Field(default="invoice", index=True)
    state: str = Field(default="draft", index=True)
    amount_minor: int = Field(default=0, index=True)
    currency: str = Field(default="", index=True)
    method: str = Field(default="", index=True)
    provider_code: str = Field(default="", index=True)
    request_key: str = Field(default="", index=True)
    attempts_opened: int = Field(default=0)
    country: str = Field(default="", index=True)
    created_at: float = Field(default=0.0, index=True)
    created_by: str = ""
    updated_at: float = Field(default=0.0)
    closed_at: float | None = Field(default=None, index=True)
    payload: dict = _payload()  # type: ignore[assignment]


class BillingPaymentAttemptRow(SQLModel, table=True):
    """**One external movement of money. Append-only.**

    `(org_id, intent_id, sequence)` is unique and the primary key is derived from
    `(intent, sequence)`, so two workers that both decide "this is attempt 2" collide instead of
    both talking to a provider. `state` moves only along an FSM with no edge out of a settled
    state; every other column but the appended observations and the ledger reference is written
    once."""

    __tablename__ = "cp_billing_payment_attempt"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    intent_id: str = Field(index=True)
    account_id: str = Field(index=True)
    sequence: int = Field(default=1, index=True)
    state: str = Field(default="started", index=True)
    amount_minor: int = Field(default=0)
    currency: str = Field(default="", index=True)
    operation_key: str = Field(default="", index=True)
    provider_code: str = Field(default="", index=True)
    provider_reference: str = Field(default="", index=True)
    provider_status: str = Field(default="")
    failure_code: str = Field(default="", index=True)
    journal_entry_id: str = Field(default="", index=True)
    started_at: float = Field(default=0.0, index=True)
    settled_at: float | None = Field(default=None, index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint("org_id", "intent_id", "sequence", name="uq_cp_billing_payment_attempt"),
    )


class BillingPaymentOperationRow(SQLModel, table=True):
    """**The durable record that a provider call is happening.**

    `(org_id, operation_key)` is a composite primary key — a database constraint, not an
    application check — so claiming an operation is a race exactly one worker wins. Committed
    before the provider is called; a `sending` row after a crash names the key to retrieve."""

    __tablename__ = "cp_billing_payment_operation"

    org_id: str = Field(primary_key=True)
    operation_key: str = Field(primary_key=True)
    intent_id: str = Field(index=True)
    attempt_id: str = Field(index=True)
    action: str = Field(default="initiate", index=True)
    provider_code: str = Field(default="", index=True)
    state: str = Field(default="sending", index=True)
    amount_minor: int = Field(default=0)
    currency: str = Field(default="", index=True)
    sent_at: float = Field(default=0.0, index=True)
    completed_at: float | None = Field(default=None, index=True)
    provider_reference: str = Field(default="", index=True)
    provider_status: str = ""
    outcome_status: str = Field(default="", index=True)
    attempts_sent: int = Field(default=0)
    last_error: str = ""
    payload: dict = _payload()  # type: ignore[assignment]


class BillingProviderEventRow(SQLModel, table=True):
    """**Append-only provider callbacks.** The id is derived from the provider's own event id and
    `(org_id, provider_code, event_ref)` is unique, so duplicate delivery — which every real
    provider does by design — is one row rather than two financial facts.

    Events that were *not* applied are stored too, with the reason. A webhook that was ignored is
    exactly as interesting to an investigation as one that was applied."""

    __tablename__ = "cp_billing_provider_event"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    provider_code: str = Field(index=True)
    event_ref: str = Field(index=True)
    event_type: str = Field(default="", index=True)
    disposition: str = Field(default="applied", index=True)
    verified: bool = Field(default=False, index=True)
    attempt_id: str = Field(default="", index=True)
    intent_id: str = Field(default="", index=True)
    outcome_status: str = Field(default="", index=True)
    provider_reference: str = Field(default="", index=True)
    amount_minor: int = Field(default=0)
    currency: str = Field(default="")
    provider_ts: float = Field(default=0.0, index=True)
    received_at: float = Field(default=0.0, index=True)
    applied_at: float | None = Field(default=None)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id", "provider_code", "event_ref", name="uq_cp_billing_provider_event"
        ),
    )


class BillingCommercialRefundRow(SQLModel, table=True):
    """**Append-only refunds**, beside M9.2's `cp_billing_refund`, which is untouched.

    The id is derived from `(attempt, request key)`, so two concurrent requests under one key are
    one refund. Nothing here amends the attempt it reverses: a refund is a new fact, and there is
    no column on an attempt that could say otherwise."""

    __tablename__ = "cp_billing_payment_refund"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    intent_id: str = Field(index=True)
    attempt_id: str = Field(index=True)
    sequence: int = Field(default=1, index=True)
    state: str = Field(default="requested", index=True)
    amount_minor: int = Field(default=0)
    currency: str = Field(default="", index=True)
    reason: str = Field(default="", index=True)
    destination: str = Field(default="", index=True)
    operation_key: str = Field(default="", index=True)
    provider_code: str = Field(default="", index=True)
    provider_reference: str = Field(default="", index=True)
    request_key: str = Field(default="", index=True)
    requested_at: float = Field(default=0.0, index=True)
    requested_by: str = ""
    completed_at: float | None = Field(default=None, index=True)
    journal_entry_id: str = Field(default="", index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    #: **The refund concurrency constraint** (M9.16-R1, P-3). The ceiling — captured minus
    #: already-refunded — was enforced by a read-modify-write, so two transactions reading before
    #: either committed could each be individually valid and jointly excessive. `sequence` makes
    #: the second refund against one capture an *impossible row* until the first has committed,
    #: which forces the loser to re-read and re-check against a ceiling that now includes the
    #: winner. A database constraint, not a Python check.
    __table_args__ = (
        UniqueConstraint(
            "org_id", "attempt_id", "sequence", name="uq_cp_billing_payment_refund_sequence"
        ),
    )


class BillingPaymentReconciliationRow(SQLModel, table=True):
    """**Append-only findings.** The id is derived from what was observed, so re-sweeping an
    unchanged divergence records the same row rather than ten thousand copies of one problem.

    `status` is the only column that moves, and only to `acknowledged` or `resolved`, and only with
    a named actor — ADR-0023 PY5. The observed figures are never amended: the point of a finding is
    what was true when it was seen."""

    __tablename__ = "cp_billing_payment_reconciliation"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    outcome: str = Field(default="", index=True)
    status: str = Field(default="open", index=True)
    provider_code: str = Field(default="", index=True)
    intent_id: str = Field(default="", index=True)
    attempt_id: str = Field(default="", index=True)
    operation_key: str = Field(default="", index=True)
    provider_reference: str = Field(default="", index=True)
    billing_state: str = Field(default="")
    provider_state: str = Field(default="")
    billing_amount_minor: int = Field(default=0)
    provider_amount_minor: int = Field(default=0)
    currency: str = Field(default="")
    journal_entry_id: str = Field(default="", index=True)
    observed_at: float = Field(default=0.0, index=True)
    resolved_at: float | None = Field(default=None, index=True)
    resolved_by: str = Field(default="", index=True)
    payload: dict = _payload()  # type: ignore[assignment]


class BillingPaymentActivityRow(SQLModel, table=True):
    """Append-only payment audit trail. No update path and no delete path."""

    __tablename__ = "cp_billing_payment_activity"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    action: str = Field(index=True)
    actor_id: str = Field(default="", index=True)
    at: float = Field(default=0.0, index=True)
    account_id: str = Field(default="", index=True)
    intent_id: str = Field(default="", index=True)
    attempt_id: str = Field(default="", index=True)
    refund_id: str = Field(default="", index=True)
    provider_code: str = Field(default="", index=True)
    operation_key: str = Field(default="", index=True)
    amount_minor: int = Field(default=0)
    currency: str = Field(default="")
    payload: dict = _payload()  # type: ignore[assignment]


#: Every table this milestone creates, in creation order. The migration reads this list rather than
#: restating it, so a table added here cannot be forgotten there.
PAYMENT_TABLES = (
    BillingPaymentRouteRow,
    BillingPaymentIntentRow,
    BillingPaymentAttemptRow,
    BillingPaymentOperationRow,
    BillingProviderEventRow,
    BillingCommercialRefundRow,
    BillingPaymentReconciliationRow,
    BillingPaymentActivityRow,
)

__all__ = [
    "PAYMENT_TABLES",
    "BillingCommercialRefundRow",
    "BillingPaymentActivityRow",
    "BillingPaymentAttemptRow",
    "BillingPaymentIntentRow",
    "BillingPaymentOperationRow",
    "BillingPaymentReconciliationRow",
    "BillingPaymentRouteRow",
    "BillingProviderEventRow",
]
