"""Credit & Revenue Ledger tables (M9.13) — additive to the M9.4–M9.12 schema.

Seven new `cp_billing_*` tables in the same projection-plus-payload row shape M9.4 established. No
existing table is altered, no column is dropped, and no Epic 5–8 table is touched — including M9.2's
`cp_billing_ledger_entry`, `cp_billing_credit_grant`, and `cp_billing_credit_movement`, which stay
exactly as they are and which the minimal M9.5 `/ledger` and `/credits` surfaces still read.

Three are **append-only by construction**: journal entries, credit applications, and ledger
activity. There is no update path and no delete path to any of them.

## The exactly-once constraint

`cp_billing_journal_entry.id` is the primary key **and** the derived identity of what caused the
posting. There is no separate idempotency column, because a separate column can disagree with the
key; here they are the same object.

This is the single most load-bearing constraint in the Billing plane. Every other domain can recover
from a duplicate by re-running: a duplicated usage record is corrected, a duplicated rating run is
superseded. **A duplicated financial posting cannot be.** It has already changed a balance, may have
already reached a statement, and the remedy is a reversal, a credit note, and a conversation with a
customer who has been charged twice.

So the invariant is defended three deep, exactly as M9.11 and M9.12 defend theirs:

* the **derived primary key** — two processes posting the same cause compute the same id and collide
  rather than both succeeding;
* a **read-before-write** — the common case returns without attempting an insert;
* an insert inside a **`SAVEPOINT`** whose `IntegrityError` is caught and turned into a read of the
  winner, so the loser's surrounding transaction — its activity entry and its staged event — is not
  destroyed along with the failed insert.

## Why there is a sequence table

`cp_billing_ledger_sequence` is a durable per-account counter, not a `max(sequence) + 1` query. A
derived maximum hands a number back after a rollback, and two entries that have both occupied
position 47 make "what was the balance immediately after entry 47?" a question with two answers.
That question is what an auditor asks, so the ordering must be a fact rather than a reconstruction.

Note that a gap is *possible* — a number can be allocated for a posting that then fails. That is
deliberate and preferable to reuse: `BalanceService.sequence_gaps` surfaces the gap, and an
unexplained gap is something an audit can ask about. A silently reused number is not.
"""

from __future__ import annotations

from sqlalchemy import JSON as SAJSON
from sqlalchemy import Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def _payload() -> object:
    return Field(default_factory=dict, sa_column=Column("payload", SAJSON))


class BillingJournalEntryRow(SQLModel, table=True):
    """One immutable financial posting. **Append-only.**

    Named beside M9.2's `cp_billing_ledger_entry`, which is untouched. The two are deliberately
    separate: an M9.2 `LedgerEntry` is a bare revenue fact, and this one carries the accounting
    period it belongs to, its position in a total order, the digest of what caused it, and the
    lineage to walk back to that cause."""

    __tablename__ = "cp_billing_journal_entry"

    id: str = Field(primary_key=True)  # derived — the exactly-once constraint
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    period_key: str = Field(default="", index=True)
    sequence: int = Field(default=0, index=True)
    entry_type: str = Field(default="charge", index=True)
    direction: str = Field(default="debit", index=True)
    amount_minor: int = Field(default=0, index=True)
    currency: str = Field(default="INR", index=True)
    signed_minor: int = Field(default=0, index=True)
    source: str = Field(default="manual", index=True)
    source_id: str = Field(default="", index=True)
    source_digest: str = Field(default="", index=True)
    subscription_id: str = Field(default="", index=True)
    rating_run_id: str = Field(default="", index=True)
    charge_id: str = Field(default="", index=True)
    invoice_id: str = Field(default="", index=True)
    payment_id: str = Field(default="", index=True)
    credit_id: str = Field(default="", index=True)
    recognition: str = Field(default="not_applicable", index=True)
    reverses_entry_id: str = Field(default="", index=True)
    adjusts_entry_id: str = Field(default="", index=True)
    disposition: str = Field(default="in_period", index=True)
    description: str = ""
    posted_at: float = Field(default=0.0, index=True)
    posted_by: str = ""
    digest: str = Field(default="", index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_cp_billing_journal_entry_identity"),
        UniqueConstraint(
            "org_id", "account_id", "sequence", name="uq_cp_billing_journal_entry_sequence"
        ),
    )


class BillingAccountingPeriodRow(SQLModel, table=True):
    """One account's accounting period. `(org_id, account_id, period_key)` is unique — two periods
    for one window would split an account's financial history across both, and a balance folded from
    a split history is wrong in a way nobody notices."""

    __tablename__ = "cp_billing_accounting_period"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    period_key: str = Field(index=True)
    period_start: float = Field(default=0.0, index=True)
    period_end: float = Field(default=0.0, index=True)
    currency: str = Field(default="INR", index=True)
    state: str = Field(default="open", index=True)
    entry_count: int = 0
    debit_minor: int = 0
    credit_minor: int = 0
    closing_balance_minor: int = Field(default=0, index=True)
    close_digest: str = Field(default="", index=True)
    opened_at: float = Field(default=0.0, index=True)
    closing_at: float | None = None
    closed_at: float | None = Field(default=None, index=True)
    closed_by: str = ""
    updated_at: float = 0.0
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id", "account_id", "period_key", name="uq_cp_billing_accounting_period"
        ),
    )


class BillingBalanceCheckpointRow(SQLModel, table=True):
    """A **derived** balance snapshot. The only replaceable row in this milestone, because a
    checkpoint is a cache with a proof rather than a financial fact — and a rebuild must replace
    the figure it supersedes rather than accumulate beside it."""

    __tablename__ = "cp_billing_balance_checkpoint"

    id: str = Field(primary_key=True)  # derived from (org, account, period)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    period_key: str = Field(index=True)
    currency: str = Field(default="INR", index=True)
    outstanding_minor: int = Field(default=0, index=True)
    debit_minor: int = 0
    credit_minor: int = 0
    entry_count: int = 0
    through_sequence: int = Field(default=0, index=True)
    digest: str = Field(default="", index=True)
    computed_at: float = Field(default=0.0, index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id", "account_id", "period_key", name="uq_cp_billing_balance_checkpoint"
        ),
    )


class BillingCommercialCreditRow(SQLModel, table=True):
    """A credit grant. **Carries no balance column** — what remains is derived from the
    applications, so the grant and its consumption can never disagree."""

    __tablename__ = "cp_billing_commercial_credit"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    code: str = Field(index=True)
    kind: str = Field(default="promotional", index=True)
    amount_minor: int = Field(default=0, index=True)
    currency: str = Field(default="INR", index=True)
    state: str = Field(default="active", index=True)
    granted_at: float = Field(default=0.0, index=True)
    granted_by: str = ""
    expires_at: float | None = Field(default=None, index=True)
    source_ref: str = Field(default="", index=True)
    reason: str = ""
    closed_at: float | None = None
    closed_by: str = ""
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_cp_billing_commercial_credit"),)


class BillingCreditApplicationRow(SQLModel, table=True):
    """One append-only credit movement. Releasing a credit is a new row of the opposite sign, never
    the removal of one — which is what keeps "how much is left?" answerable by folding."""

    __tablename__ = "cp_billing_credit_application"

    id: str = Field(primary_key=True)  # derived
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    credit_id: str = Field(index=True)
    amount_minor: int = Field(default=0, index=True)
    currency: str = Field(default="INR", index=True)
    applied_at: float = Field(default=0.0, index=True)
    applied_by: str = ""
    journal_entry_id: str = Field(default="", index=True)
    invoice_id: str = Field(default="", index=True)
    reverses_application_id: str = Field(default="", index=True)
    reason: str = ""
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_cp_billing_credit_application_identity"),
    )


class BillingLedgerActivityRow(SQLModel, table=True):
    """Append-only ledger history — including the postings that recorded nothing. "We saw this
    cause again and wrote no second entry" is the evidence exactly-once worked, and it is exactly
    the entry an auditor wants when a total looks too round."""

    __tablename__ = "cp_billing_ledger_activity"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    action: str = Field(index=True)
    actor_id: str = ""
    at: float = Field(default=0.0, index=True)
    account_id: str = Field(default="", index=True)
    period_key: str = Field(default="", index=True)
    journal_entry_id: str = Field(default="", index=True)
    credit_id: str = Field(default="", index=True)
    amount_minor: int = 0
    currency: str = Field(default="", index=True)
    reason: str = ""
    payload: dict = _payload()  # type: ignore[assignment]


class BillingLedgerSequenceRow(SQLModel, table=True):
    """A durable per-account entry counter.

    A stored counter rather than `max(sequence) + 1`, for the same reason M9.8 stores account
    numbers: a derived maximum hands a number back after a rollback, and a reused position makes the
    balance history ambiguous at exactly the point an auditor is reading it."""

    __tablename__ = "cp_billing_ledger_sequence"

    org_id: str = Field(primary_key=True)
    account_id: str = Field(primary_key=True)
    last_sequence: int = 0


LEDGER_TABLES = (
    BillingJournalEntryRow,
    BillingAccountingPeriodRow,
    BillingBalanceCheckpointRow,
    BillingCommercialCreditRow,
    BillingCreditApplicationRow,
    BillingLedgerActivityRow,
    BillingLedgerSequenceRow,
)

LEDGER_TABLE_NAMES = tuple(t.__tablename__ for t in LEDGER_TABLES)
