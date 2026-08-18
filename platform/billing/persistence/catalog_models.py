"""Commercial Catalog tables (M9.6) — additive to the M9.4 schema.

Six new `cp_billing_*` tables, the same projection-plus-payload row shape M9.4 established:
typed indexed columns for every key the catalog contracts query on, plus the canonically
encoded aggregate in a portable JSON payload. No existing table is altered, no column is
dropped, and no Epic 5–8 table is touched.

Two of these are **append-only by construction**: `cp_billing_catalog_version` (a published
snapshot is immutable, and every historical version stays readable forever) and
`cp_billing_catalog_activity` (history is appended, never rewritten). Neither has an update
path anywhere above it.
"""

from __future__ import annotations

from sqlalchemy import JSON as SAJSON
from sqlalchemy import Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def _payload() -> object:
    return Field(default_factory=dict, sa_column=Column("payload", SAJSON))


class BillingProductRow(SQLModel, table=True):
    __tablename__ = "cp_billing_product"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    code: str = Field(index=True)
    kind: str = Field(default="platform", index=True)
    state: str = Field(default="draft", index=True)
    visibility: str = Field(default="public", index=True)
    audience: str = Field(default="self_serve", index=True)
    updated_at: float = 0.0
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_cp_billing_product"),)


class BillingEditionRow(SQLModel, table=True):
    __tablename__ = "cp_billing_edition"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    code: str = Field(index=True)
    product_code: str = Field(index=True)
    state: str = Field(default="draft", index=True)
    visibility: str = Field(default="public", index=True)
    rank: int = Field(default=0, index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    # Edition codes are unique *within a product*, not globally: two products may both have
    # a "pro" edition, and forcing global uniqueness would be a naming tax with no payoff.
    __table_args__ = (
        UniqueConstraint("org_id", "product_code", "code", name="uq_cp_billing_edition"),
    )


class BillingBundleRow(SQLModel, table=True):
    __tablename__ = "cp_billing_bundle"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    code: str = Field(index=True)
    state: str = Field(default="draft", index=True)
    visibility: str = Field(default="public", index=True)
    audience: str = Field(default="self_serve", index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_cp_billing_bundle"),)


class BillingFeatureGroupRow(SQLModel, table=True):
    __tablename__ = "cp_billing_feature_group"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    code: str = Field(index=True)
    rank: int = Field(default=0, index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_cp_billing_feature_group"),)


class BillingCatalogVersionRow(SQLModel, table=True):
    """Immutable published catalog snapshots. Append-only: the unique constraint makes
    republishing a version a database-level error, not a convention."""

    __tablename__ = "cp_billing_catalog_version"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    version: int = Field(index=True)
    published_at: float = Field(default=0.0, index=True)
    published_by: str = ""
    rolled_back_from: int | None = Field(default=None, index=True)
    entity_count: int = 0
    notes: str = ""
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (UniqueConstraint("org_id", "version", name="uq_cp_billing_catalog_version"),)


class BillingCatalogActivityRow(SQLModel, table=True):
    """Append-only catalog history."""

    __tablename__ = "cp_billing_catalog_activity"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    action: str = Field(index=True)
    subject_kind: str = Field(default="", index=True)
    subject_code: str = Field(default="", index=True)
    actor_id: str = ""
    at: float = Field(default=0.0, index=True)
    catalog_version: int | None = Field(default=None, index=True)
    payload: dict = _payload()  # type: ignore[assignment]


CATALOG_TABLES = (
    BillingProductRow,
    BillingEditionRow,
    BillingBundleRow,
    BillingFeatureGroupRow,
    BillingCatalogVersionRow,
    BillingCatalogActivityRow,
)

CATALOG_TABLE_NAMES = tuple(t.__tablename__ for t in CATALOG_TABLES)
