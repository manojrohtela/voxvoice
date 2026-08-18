"""Billing Account tables (M9.8) — additive to the M9.4/M9.6/M9.7 schema.

Three new `cp_billing_*` tables in the same projection-plus-payload row shape M9.4
established: typed indexed columns for every key the account contracts query on, plus the
canonically encoded aggregate in a portable JSON payload. No existing table is altered, no
column is dropped, and no Epic 5–8 table is touched.

One is **append-only by construction** — `cp_billing_account_activity`. An account history
that could be edited would not be a history.

`cp_billing_account_number` is a per-organization sequence rather than a computed maximum: an
account number appears on invoices and in a customer's own accounts-payable system, so it must
be allocated once and never reused, including after a draft is deleted. A row that only ever
moves forward is the cheapest way to guarantee that.

`parent_account_id` and `payer_account_id` are indexed **separately** because they are separate
graphs — a hierarchy query and a payer query never touch each other's index.
"""

from __future__ import annotations

from sqlalchemy import JSON as SAJSON
from sqlalchemy import Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def _payload() -> object:
    return Field(default_factory=dict, sa_column=Column("payload", SAJSON))


class BillingCommercialAccountRow(SQLModel, table=True):
    """The commercial account book. `(org_id, account_number)` is unique: two accounts sharing
    a number is a reconciliation defect, not a naming preference."""

    __tablename__ = "cp_billing_commercial_account"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_number: str = Field(index=True)
    display_name: str = Field(default="", index=True)
    currency: str = Field(index=True)
    kind: str = Field(default="direct", index=True)
    state: str = Field(default="draft", index=True)
    commercial_status: str = Field(default="good_standing", index=True)
    # Two graphs, two indexes — the hierarchy and the payer chain are resolved separately.
    parent_account_id: str = Field(default="", index=True)
    payer_account_id: str = Field(default="", index=True)
    legal_name: str = Field(default="", index=True)
    registration_number: str = Field(default="", index=True)
    tax_identifier: str = Field(default="", index=True)
    country: str = Field(default="", index=True)
    risk_rating: str = Field(default="unrated", index=True)
    credit_hold: bool = Field(default=False, index=True)
    tax_profile_id: str = Field(default="", index=True)
    created_at: float = Field(default=0.0, index=True)
    updated_at: float = 0.0
    closed_at: float | None = Field(default=None, index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint("org_id", "account_number", name="uq_cp_billing_commercial_account"),
    )


class BillingAccountActivityRow(SQLModel, table=True):
    """Append-only account history."""

    __tablename__ = "cp_billing_account_activity"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    action: str = Field(index=True)
    actor_id: str = ""
    at: float = Field(default=0.0, index=True)
    from_state: str = Field(default="", index=True)
    to_state: str = Field(default="", index=True)
    payload: dict = _payload()  # type: ignore[assignment]


class BillingAccountNumberRow(SQLModel, table=True):
    """One monotonic account-number sequence per organization. Never rewound, never reused."""

    __tablename__ = "cp_billing_account_number"

    org_id: str = Field(primary_key=True)
    last_sequence: int = 0


ACCOUNT_TABLES = (
    BillingCommercialAccountRow,
    BillingAccountActivityRow,
    BillingAccountNumberRow,
)

ACCOUNT_TABLE_NAMES = tuple(t.__tablename__ for t in ACCOUNT_TABLES)
