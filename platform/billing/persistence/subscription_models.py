"""Subscription Engine tables (M9.9) — additive to the M9.4/M9.6/M9.7/M9.8 schema.

Four new `cp_billing_*` tables in the same projection-plus-payload row shape M9.4 established:
typed indexed columns for every key the subscription contracts query on, plus the canonically
encoded aggregate in a portable JSON payload. No existing table is altered, no column is
dropped, and no Epic 5–8 table is touched — including M9.2's `cp_billing_subscription`, which
stays exactly as it is.

Two are **append-only by construction**: `cp_billing_subscription_version` (a published version
is what an invoice was raised against) and `cp_billing_subscription_activity`.

`catalog_version` and `pricing_version` are indexed as plain integers. They are *pins into
frozen snapshots*, not foreign keys, and deliberately so: a foreign key would make the two
version lines a coupling the database enforces, when the whole point of M9.6 and M9.7 is that
they version independently and neither can reach backwards into an existing agreement.
"""

from __future__ import annotations

from sqlalchemy import JSON as SAJSON
from sqlalchemy import Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def _payload() -> object:
    return Field(default_factory=dict, sa_column=Column("payload", SAJSON))


class BillingCommercialSubscriptionRow(SQLModel, table=True):
    """The subscription book. `(org_id, subscription_number)` is unique."""

    __tablename__ = "cp_billing_commercial_subscription"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    subscription_number: str = Field(index=True)
    account_id: str = Field(index=True)
    # Pins into frozen snapshots — indexed for reads, never a foreign key. See module docstring.
    catalog_version: int = Field(default=0, index=True)
    pricing_version: int = Field(default=0, index=True)
    currency: str = Field(index=True)
    plan_code: str = Field(default="", index=True)
    state: str = Field(default="draft", index=True)
    version: int = Field(default=1, index=True)
    interval: str = Field(default="monthly", index=True)
    cycle_number: int = Field(default=1, index=True)
    period_start: float | None = Field(default=None, index=True)
    period_end: float | None = Field(default=None, index=True)
    trial_ends_at: float | None = Field(default=None, index=True)
    renewal_mode: str = Field(default="auto", index=True)
    item_count: int = 0
    seat_count: int = Field(default=0, index=True)
    pending_change_count: int = Field(default=0, index=True)
    contract_id: str = Field(default="", index=True)
    created_at: float = Field(default=0.0, index=True)
    updated_at: float = 0.0
    cancelled_at: float | None = Field(default=None, index=True)
    ended_at: float | None = Field(default=None, index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id", "subscription_number", name="uq_cp_billing_commercial_subscription"
        ),
    )


class BillingSubscriptionVersionRow(SQLModel, table=True):
    """Immutable published subscription versions. Append-only: the unique constraint makes
    republishing a version a database-level error, not a convention."""

    __tablename__ = "cp_billing_subscription_version"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    subscription_id: str = Field(index=True)
    version: int = Field(index=True)
    published_at: float = Field(default=0.0, index=True)
    published_by: str = ""
    reason: str = ""
    rolled_back_from: int | None = Field(default=None, index=True)
    catalog_version: int | None = Field(default=None, index=True)
    pricing_version: int | None = Field(default=None, index=True)
    state: str = Field(default="", index=True)
    item_count: int = 0
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id", "subscription_id", "version", name="uq_cp_billing_subscription_version"
        ),
    )


class BillingSubscriptionActivityRow(SQLModel, table=True):
    """Append-only subscription history."""

    __tablename__ = "cp_billing_subscription_activity"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    subscription_id: str = Field(index=True)
    action: str = Field(index=True)
    actor_id: str = ""
    at: float = Field(default=0.0, index=True)
    from_state: str = Field(default="", index=True)
    to_state: str = Field(default="", index=True)
    version: int = Field(default=1, index=True)
    payload: dict = _payload()  # type: ignore[assignment]


class BillingSubscriptionNumberRow(SQLModel, table=True):
    """One monotonic subscription-number sequence per organization. Never rewound."""

    __tablename__ = "cp_billing_subscription_number"

    org_id: str = Field(primary_key=True)
    last_sequence: int = 0


SUBSCRIPTION_TABLES = (
    BillingCommercialSubscriptionRow,
    BillingSubscriptionVersionRow,
    BillingSubscriptionActivityRow,
    BillingSubscriptionNumberRow,
)

SUBSCRIPTION_TABLE_NAMES = tuple(t.__tablename__ for t in SUBSCRIPTION_TABLES)
