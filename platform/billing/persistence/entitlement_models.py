"""Entitlement Engine tables (M9.10) — additive to the M9.4/M9.6/M9.7/M9.8/M9.9 schema.

Five new `cp_billing_*` tables in the same projection-plus-payload row shape M9.4 established:
typed indexed columns for every key the entitlement contracts query on, plus the canonically
encoded aggregate in a portable JSON payload. No existing table is altered, no column is dropped,
and no Epic 5–8 table is touched — including M9.2's `cp_billing_entitlement_grant` and
`cp_billing_entitlement_snapshot`, which stay exactly as they are.

Two are **append-only by construction**: `cp_billing_entitlement_version` (a published snapshot is
what an enforcing plane acted on) and `cp_billing_entitlement_activity`.

Grants live inside the profile payload rather than in a table of their own. That is a deliberate
aggregate boundary, not a shortcut: precedence resolution is only correct when it sees *all* of an
account's grants at once, and a row-per-grant table invites a partial read that would silently
resolve to the wrong answer. The columns a screen needs to filter on — grant count, live count,
codes — are projected onto the profile row instead.
"""

from __future__ import annotations

from sqlalchemy import JSON as SAJSON
from sqlalchemy import Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def _payload() -> object:
    return Field(default_factory=dict, sa_column=Column("payload", SAJSON))


class BillingEntitlementDefinitionRow(SQLModel, table=True):
    """The gate registry. `(org_id, code)` is unique — one declaration per capability."""

    __tablename__ = "cp_billing_entitlement_definition"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    code: str = Field(index=True)
    display_name: str = ""
    kind: str = Field(default="flag", index=True)
    classification: str = Field(default="fail_open", index=True)
    meter_code: str = Field(default="", index=True)
    max_staleness_seconds: float = Field(default=3600.0, index=True)
    default_enabled: bool = Field(default=False, index=True)
    default_allowance: int | None = Field(default=None, index=True)
    version: int = Field(default=1, index=True)
    created_at: float = Field(default=0.0, index=True)
    updated_at: float = 0.0
    retired_at: float | None = Field(default=None, index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint("org_id", "code", name="uq_cp_billing_entitlement_definition"),
    )


class BillingEntitlementBundleRow(SQLModel, table=True):
    """Entitlement bundles. `(org_id, code)` is unique."""

    __tablename__ = "cp_billing_entitlement_bundle"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    code: str = Field(index=True)
    display_name: str = ""
    entry_count: int = Field(default=0, index=True)
    member_count: int = Field(default=0, index=True)
    version: int = Field(default=1, index=True)
    created_at: float = Field(default=0.0, index=True)
    updated_at: float = 0.0
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_cp_billing_entitlement_bundle"),)


class BillingEntitlementProfileRow(SQLModel, table=True):
    """One account's entitlement position, grants included in the payload (see module docstring).
    `(org_id, account_id)` is unique — an account has exactly one position."""

    __tablename__ = "cp_billing_entitlement_profile"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    subscription_id: str = Field(default="", index=True)
    version: int = Field(default=0, index=True)
    max_staleness_seconds: float = Field(default=3600.0, index=True)
    grant_count: int = Field(default=0, index=True)
    live_grant_count: int = Field(default=0, index=True)
    pending_change_count: int = Field(default=0, index=True)
    created_at: float = Field(default=0.0, index=True)
    updated_at: float = 0.0
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint("org_id", "account_id", name="uq_cp_billing_entitlement_profile"),
    )


class BillingEntitlementVersionRow(SQLModel, table=True):
    """Immutable published entitlement snapshots. Append-only: the unique constraint makes
    republishing a version a database-level error, not a convention."""

    __tablename__ = "cp_billing_entitlement_version"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    version: int = Field(index=True)
    published_at: float = Field(default=0.0, index=True)
    published_by: str = ""
    reason: str = ""
    rolled_back_from: int | None = Field(default=None, index=True)
    entitlement_count: int = 0
    grant_count: int = 0
    subscription_id: str = Field(default="", index=True)
    max_staleness_seconds: float = Field(default=3600.0, index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id", "account_id", "version", name="uq_cp_billing_entitlement_version"
        ),
    )


class BillingEntitlementActivityRow(SQLModel, table=True):
    """Append-only entitlement history."""

    __tablename__ = "cp_billing_entitlement_activity"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    action: str = Field(index=True)
    actor_id: str = ""
    at: float = Field(default=0.0, index=True)
    entitlement_code: str = Field(default="", index=True)
    grant_id: str = Field(default="", index=True)
    from_state: str = Field(default="", index=True)
    to_state: str = Field(default="", index=True)
    version: int = Field(default=0, index=True)
    payload: dict = _payload()  # type: ignore[assignment]


ENTITLEMENT_TABLES = (
    BillingEntitlementDefinitionRow,
    BillingEntitlementBundleRow,
    BillingEntitlementProfileRow,
    BillingEntitlementVersionRow,
    BillingEntitlementActivityRow,
)

ENTITLEMENT_TABLE_NAMES = tuple(t.__tablename__ for t in ENTITLEMENT_TABLES)
