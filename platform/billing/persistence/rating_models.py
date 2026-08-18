"""Rating Engine tables (M9.12) — additive to the M9.4–M9.11 schema.

Five new `cp_billing_*` tables in the same projection-plus-payload row shape M9.4 established. No
existing table is altered, no column is dropped, and no Epic 5–8 table is touched — including
M9.2's `cp_billing_charge_line`, which stays exactly as it is and which the invoicing spine still
reads.

Three are **append-only by construction**: rating runs' charges, rating snapshots, and rating
activity.

## The idempotency constraint

`uq_cp_billing_rating_run_basis` on `(org_id, subscription_id, period_start, period_end,
basis_digest)` is the load-bearing object in this milestone.

It says exactly what the invariant says: **at most one rating run per set of inputs, per period,
per agreement.** Because the primary key is *derived* from the same tuple, the constraint and the
key agree by construction rather than by coincidence — two defences for the invariant that cannot
be recovered from, exactly as M9.11 did for commercial usage. A double-rated period is not a
re-run that can be deleted; it is two commercial figures for one month, and whichever one reached
an invoice first is the one the customer already paid.

The constraint is what makes idempotent rerating a property of the *database* rather than of a
code path that could be raced:

* a nightly job and a manual rerate colliding both compute the same id and the same digest; one
  wins, the other's `IntegrityError` is caught by the repository and turned into a read of the
  winner. Both callers get the same figure;
* a retry after a crash mid-transaction cannot produce a second run;
* a rerun months later over unchanged inputs recomputes the same key and is a no-op.

**And the inverse is what makes rerating safe:** if any input moved, the digest differs, so the
key differs, so the new run is *appended beside* the old one. There is no code path that can
overwrite the figure a customer was billed — not because nobody wrote one, but because the id of a
revised run is never the id of the run it revises.
"""

from __future__ import annotations

from sqlalchemy import JSON as SAJSON
from sqlalchemy import Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def _payload() -> object:
    return Field(default_factory=dict, sa_column=Column("payload", SAJSON))


class BillingRatingRunRow(SQLModel, table=True):
    """One rating attempt and its verdict. The id is derived from the basis digest; see the
    module docstring for why that, plus the unique constraint, is two defences rather than one."""

    __tablename__ = "cp_billing_rating_run"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    subscription_id: str = Field(index=True)
    period_start: float = Field(default=0.0, index=True)
    period_end: float = Field(default=0.0, index=True)
    currency: str = Field(default="INR", index=True)
    state: str = Field(default="draft", index=True)
    sequence: int = Field(default=1, index=True)
    basis_digest: str = Field(default="", index=True)
    result_digest: str = Field(default="", index=True)
    total_minor: int = Field(default=0, index=True)
    charge_count: int = 0
    reason: str = Field(default="", index=True)
    supersedes: str = Field(default="", index=True)
    adjusts: str = Field(default="", index=True)
    superseded_by: str = Field(default="", index=True)
    rated_at: float = Field(default=0.0, index=True)
    rated_by: str = ""
    finalized_at: float | None = Field(default=None, index=True)
    finalized_by: str = ""
    updated_at: float = 0.0
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "subscription_id",
            "period_start",
            "period_end",
            "basis_digest",
            name="uq_cp_billing_rating_run_basis",
        ),
    )


class BillingRatedChargeRow(SQLModel, table=True):
    """One calculated charge. **Append-only.**

    Named beside M9.2's `cp_billing_charge_line`, which is the *invoice line* table and is
    untouched. The two are deliberately separate: an invoice line is what appears on a document,
    and a rated charge is the calculation that produced it, breakdown and provenance included."""

    __tablename__ = "cp_billing_rated_charge"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    rating_run_id: str = Field(index=True)
    subscription_id: str = Field(default="", index=True)
    kind: str = Field(default="usage", index=True)
    description: str = ""
    currency: str = Field(default="INR", index=True)
    quantity: int = Field(default=0, index=True)
    unit: str = ""
    list_minor: int = 0
    gross_minor: int = 0
    discount_minor: int = 0
    net_minor: int = Field(default=0, index=True)
    price_code: str = Field(default="", index=True)
    price_version: int = Field(default=0, index=True)
    price_book_code: str = Field(default="", index=True)
    meter_code: str = Field(default="", index=True)
    item_id: str = Field(default="", index=True)
    usage_digest: str = Field(default="", index=True)
    component_count: int = 0
    discount_count: int = 0
    digest: str = Field(default="", index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (UniqueConstraint("org_id", "id", name="uq_cp_billing_rated_charge_identity"),)


class BillingRatingSnapshotRow(SQLModel, table=True):
    """Append-only rating snapshots: what a run decided, frozen at finalization."""

    __tablename__ = "cp_billing_rating_snapshot"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    subscription_id: str = Field(index=True)
    rating_run_id: str = Field(index=True)
    period_start: float = Field(default=0.0, index=True)
    period_end: float = Field(default=0.0, index=True)
    sequence: int = Field(default=1, index=True)
    result_digest: str = Field(default="", index=True)
    basis_digest: str = Field(default="", index=True)
    total_minor: int = 0
    charge_count: int = 0
    taken_at: float = Field(default=0.0, index=True)
    taken_by: str = ""
    reason: str = ""
    payload: dict = _payload()  # type: ignore[assignment]


class BillingMinimumCommitmentRow(SQLModel, table=True):
    """Spend floors. `(org_id, code)` is unique — two floors for one code would be applied
    twice, and a commitment charged twice is a commitment nobody agreed to."""

    __tablename__ = "cp_billing_minimum_commitment"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    code: str = Field(index=True)
    amount_minor: int = 0
    currency: str = Field(default="INR", index=True)
    subscription_id: str = Field(default="", index=True)
    interval: str = Field(default="monthly", index=True)
    contract_id: str = Field(default="", index=True)
    active: bool = Field(default=True, index=True)
    effective_from: float = Field(default=0.0, index=True)
    effective_until: float | None = Field(default=None, index=True)
    created_at: float = Field(default=0.0, index=True)
    retired_at: float | None = Field(default=None, index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_cp_billing_minimum_commitment"),)


class BillingRatingActivityRow(SQLModel, table=True):
    """Append-only rating history — including the runs that changed nothing. "We rated this
    again and the figure held" is the evidence determinism worked, and it is exactly the entry a
    finance team wants when a customer asks why a figure moved twice and then stopped."""

    __tablename__ = "cp_billing_rating_activity"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    action: str = Field(index=True)
    actor_id: str = ""
    at: float = Field(default=0.0, index=True)
    account_id: str = Field(default="", index=True)
    subscription_id: str = Field(default="", index=True)
    rating_run_id: str = Field(default="", index=True)
    period_start: float | None = Field(default=None, index=True)
    period_end: float | None = None
    total_minor: int = 0
    currency: str = Field(default="", index=True)
    reason: str = ""
    payload: dict = _payload()  # type: ignore[assignment]


RATING_TABLES = (
    BillingRatingRunRow,
    BillingRatedChargeRow,
    BillingRatingSnapshotRow,
    BillingMinimumCommitmentRow,
    BillingRatingActivityRow,
)

RATING_TABLE_NAMES = tuple(t.__tablename__ for t in RATING_TABLES)
