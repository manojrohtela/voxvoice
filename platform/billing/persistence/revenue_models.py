"""Revenue Recognition tables (M9.18) — additive to the M9.4–M9.17 schema.

Five new `cp_billing_*` tables in the same projection-plus-payload row shape M9.4 established. No
existing table is altered, no column is dropped, and nothing in M9.2–M9.17 is touched — including
`cp_billing_ledger_entry`, which keeps sole ownership of financial recording, and
`cp_billing_invoice_snapshot`, which keeps sole ownership of document content.

## The row this milestone is built on

`cp_billing_revenue_schedule` has its **primary key derived from the basis digest** — a hash over
every input that can change the answer: document, revision, snapshot content, amount, service
window, method, policy version, remainder rule and granularity. Three consequences fall out of that
one choice:

* two workers scheduling the same recognition compute the same id and one loses the insert;
* re-running an unchanged derivation lands on the same row and writes nothing;
* **changing any input produces a different schedule rather than a mutated one**, which is what
  stops a policy edit in April from silently restating a recognition taken in March.

The same row carries `version`, and every transition is a conditional UPDATE against it — the
stale-worker defence, identical in shape to M9.17's collection case.

`cp_billing_recognition_entry` is the **double-recognition defence**: its id is derived from
`(schedule, sequence)` and `(org_id, schedule_id, sequence)` is unique, so two workers that both
decide "this is period 3" write one row, and the `PENDING → RECOGNIZED` move is itself a conditional
UPDATE, so only one of them ever reaches the Ledger.

## What is deliberately absent from every column

There is **no `deferred_balance`, no `recognized_total`, and no `deferred_minor`** — not as a
column and not inside a payload blob. Deferred revenue is `basis amount − recognized − adjusted`,
derived from the entries that actually happened, every time it is asked for. A stored balance would
be a second authority on a figure the Ledger owns and it would be the stale one, which is the same
rule M9.13 applies to `AccountBalance`, M9.16 to a payment position and M9.17 to what a customer
owes. `entries_recognized` is a **count**, not money.

There is also no invoice total, no tax component, no price, no payment reference and no collection
reference. The amount was copied from an issued snapshot; everything else belongs to a domain that
already owns it.

## Append-only by construction

Adjustments and the audit trail have no update path at all. Entries have exactly two narrow ones —
the conditional recognition move and the posting outcome — and a recognized entry is never amended:
a correction is a new `cp_billing_recognition_adjustment` row carrying provenance to the original.
"""

from __future__ import annotations

from sqlalchemy import JSON as SAJSON
from sqlalchemy import Column, Index, UniqueConstraint
from sqlmodel import Field, SQLModel


def _payload() -> object:
    return Field(default_factory=dict, sa_column=Column("payload", SAJSON))


class BillingRecognitionPolicyRow(SQLModel, table=True):
    """One **immutable** recognition strategy version.

    `(org_id, code, version)` is unique and the id is derived from it, so publishing the same
    version twice is idempotent and publishing a different body under the same version is a
    constraint violation rather than a silent rewrite of the rules a live schedule was judged by.

    There is no update path. `is_active` withdraws a version from *future* binding; schedules
    already bound to it keep pointing at it, because their recognition must still replay."""

    __tablename__ = "cp_billing_recognition_policy"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    code: str = Field(index=True)
    version: int = Field(default=1, index=True)
    method: str = Field(default="straight_line", index=True)
    granularity: str = Field(default="month")
    remainder: str = Field(default="largest_remainder")
    is_active: bool = Field(default=True, index=True)
    declared_at: float = Field(default=0.0, index=True)
    declared_by: str = ""
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint("org_id", "code", "version", name="uq_cp_billing_recognition_policy"),
    )


class BillingRevenueScheduleRow(SQLModel, table=True):
    """A plan for turning one billed amount into recognized revenue.

    The primary key **is** the basis digest, so identity and reproducibility are the same fact.
    `basis_digest` is stored as its own indexed column too, so "show me every schedule derived from
    this evidence" is an index seek rather than a scan of a JSON blob.

    Note the columns that do not exist: no `recognized_total`, no `deferred_balance`.
    `entries_recognized` is a count."""

    __tablename__ = "cp_billing_revenue_schedule"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    document_id: str = Field(default="", index=True)
    revision: int = Field(default=1)
    snapshot_digest: str = Field(default="", index=True)
    basis_digest: str = Field(default="", index=True)
    state: str = Field(default="draft", index=True)
    method: str = Field(default="straight_line", index=True)
    currency: str = Field(default="", index=True)
    amount_minor: int = Field(default=0)  # the basis amount — copied evidence, not a balance
    service_start: float = Field(default=0.0, index=True)
    service_end: float = Field(default=0.0, index=True)
    policy_id: str = Field(default="", index=True)
    policy_code: str = Field(default="", index=True)
    policy_version: int = Field(default=0)
    entries_planned: int = Field(default=0)
    entries_recognized: int = Field(default=0)  # a **count**, never money
    version: int = Field(default=0, index=True)
    superseded_by: str = Field(default="", index=True)
    subscription_id: str = Field(default="", index=True)
    created_at: float = Field(default=0.0, index=True)
    closed_at: float | None = Field(default=None, index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        # The supersession trail: every schedule derived from one document, across revisions.
        Index("ix_cp_billing_revenue_schedule_doc", "org_id", "document_id", "revision"),
        Index("ix_cp_billing_revenue_schedule_account", "org_id", "account_id", "state"),
    )


class BillingRecognitionEntryRow(SQLModel, table=True):
    """One dated slice of revenue. **The double-recognition defence.**

    Two constraints saying the same thing: the id is derived from `(schedule, sequence)`, and
    `(org_id, schedule_id, sequence)` is unique. The first stops two workers computing the same
    slice twice; the second stops a caller supplying its own id.

    `state` moves `pending → recognized` under a conditional UPDATE, so exactly one worker ever
    reaches the Ledger with a given slice."""

    __tablename__ = "cp_billing_recognition_entry"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    schedule_id: str = Field(index=True)
    account_id: str = Field(index=True)
    sequence: int = Field(default=1)
    period_start: float = Field(default=0.0)
    period_end: float = Field(default=0.0)
    amount_minor: int = Field(default=0)
    currency: str = Field(default="", index=True)
    recognize_on: float = Field(default=0.0, index=True)
    state: str = Field(default="pending", index=True)
    recognition: str = Field(default="deferred", index=True)
    ledger_entry_id: str = Field(default="", index=True)
    ledger_period_key: str = Field(default="")
    ledger_disposition: str = Field(default="")
    recognized_at: float | None = Field(default=None, index=True)
    posted_at: float | None = Field(default=None)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id", "schedule_id", "sequence", name="uq_cp_billing_recognition_entry"
        ),
        # The two reads a scheduler and a repair queue issue, both org-scoped and both indexed.
        Index("ix_cp_billing_recognition_entry_due", "org_id", "state", "recognize_on"),
    )


class BillingRecognitionAdjustmentRow(SQLModel, table=True):
    """A correction as a new fact. **Append-only — no update path exists.**

    Carries its own provenance: which entry, which document revision, which policy version, why,
    effective when, and which ledger entry recorded it. That is the row an auditor reads when the
    question is "this number changed — on whose authority?"."""

    __tablename__ = "cp_billing_recognition_adjustment"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    schedule_id: str = Field(index=True)
    entry_id: str = Field(index=True)
    kind: str = Field(default="reversal", index=True)
    amount_minor: int = Field(default=0)  # signed: a reversal is the negative of the original
    currency: str = Field(default="", index=True)
    document_id: str = Field(default="", index=True)
    revision: int = Field(default=0)
    policy_code: str = Field(default="")
    policy_version: int = Field(default=0)
    effective_at: float = Field(default=0.0, index=True)
    ledger_entry_id: str = Field(default="", index=True)
    created_at: float = Field(default=0.0, index=True)
    created_by: str = ""
    request_key: str = Field(default="", index=True)
    payload: dict = _payload()  # type: ignore[assignment]


class BillingRecognitionActivityRow(SQLModel, table=True):
    """The audit trail an accountant reads. Append-only."""

    __tablename__ = "cp_billing_recognition_activity"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    schedule_id: str = Field(index=True)
    at: float = Field(default=0.0, index=True)
    kind: str = Field(default="", index=True)
    actor_id: str = Field(default="", index=True)
    entry_id: str = Field(default="", index=True)
    payload: dict = _payload()  # type: ignore[assignment]


#: Declared so the migration and the "every table is built" test iterate the same list.
REVENUE_TABLES = (
    BillingRecognitionPolicyRow,
    BillingRevenueScheduleRow,
    BillingRecognitionEntryRow,
    BillingRecognitionAdjustmentRow,
    BillingRecognitionActivityRow,
)

__all__ = [
    "REVENUE_TABLES",
    "BillingRecognitionActivityRow",
    "BillingRecognitionAdjustmentRow",
    "BillingRecognitionEntryRow",
    "BillingRecognitionPolicyRow",
    "BillingRevenueScheduleRow",
]
