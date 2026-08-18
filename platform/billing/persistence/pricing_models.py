"""Pricing Engine tables (M9.7) — additive to the M9.4/M9.6 schema.

Seven new `cp_billing_*` tables in the same projection-plus-payload row shape M9.4
established: typed indexed columns for every key the pricing contracts query on, plus the
canonically encoded aggregate in a portable JSON payload. No existing table is altered, no
column is dropped, and no Epic 5–8 table is touched.

Three are **append-only by construction** — `cp_billing_price_book_version` (a published price
is immutable, and every historical version stays readable forever), `cp_billing_pricing_version`
(the org-wide snapshot), and `cp_billing_pricing_activity` (history is appended, never
rewritten). None has an update path anywhere above it.

No money is stored in a projection column. Amounts live only inside the encoded payload, as
integer minor units paired with their currency, because a bare numeric column is exactly how a
currency gets separated from its amount (ADR-0023 RS5).
"""

from __future__ import annotations

from sqlalchemy import JSON as SAJSON
from sqlalchemy import Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def _payload() -> object:
    return Field(default_factory=dict, sa_column=Column("payload", SAJSON))


class BillingPricingBookRow(SQLModel, table=True):
    """The authoring price books. Identity is `(org_id, code, revision)`: revisions
    accumulate, so the book a subscription was priced from survives a reprice."""

    __tablename__ = "cp_billing_pricing_book"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    code: str = Field(index=True)
    revision: int = Field(default=1, index=True)
    currency: str = Field(index=True)
    state: str = Field(default="draft", index=True)
    region: str = Field(default="global", index=True)
    audience: str = Field(default="public", index=True)
    catalog_version: int | None = Field(default=None, index=True)
    effective_from: float = Field(default=0.0, index=True)
    effective_until: float | None = Field(default=None, index=True)
    published_version: int | None = Field(default=None, index=True)
    updated_at: float = 0.0
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint("org_id", "code", "revision", name="uq_cp_billing_pricing_book"),
    )


class BillingPriceBookVersionRow(SQLModel, table=True):
    """Immutable published price-book versions. The unique constraint makes republishing a
    version a database-level error, not a convention."""

    __tablename__ = "cp_billing_price_book_version"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    code: str = Field(index=True)
    version: int = Field(index=True)
    currency: str = Field(index=True)
    region: str = Field(default="global", index=True)
    audience: str = Field(default="public", index=True)
    catalog_version: int | None = Field(default=None, index=True)
    effective_from: float = Field(default=0.0, index=True)
    effective_until: float | None = Field(default=None, index=True)
    published_at: float = Field(default=0.0, index=True)
    published_by: str = ""
    rolled_back_from: int | None = Field(default=None, index=True)
    price_count: int = 0
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint("org_id", "code", "version", name="uq_cp_billing_price_book_version"),
    )


class BillingDiscountPolicyRow(SQLModel, table=True):
    __tablename__ = "cp_billing_discount_policy"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    code: str = Field(index=True)
    kind: str = Field(default="percentage", index=True)
    priority: int = Field(default=100, index=True)
    stackable: bool = Field(default=True, index=True)
    effective_from: float = Field(default=0.0, index=True)
    effective_until: float | None = Field(default=None, index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_cp_billing_discount_policy"),)


class BillingPromotionRow(SQLModel, table=True):
    __tablename__ = "cp_billing_promotion"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    code: str = Field(index=True)
    campaign: str = Field(default="", index=True)
    exclusive: bool = Field(default=False, index=True)
    effective_from: float = Field(default=0.0, index=True)
    effective_until: float | None = Field(default=None, index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_cp_billing_promotion"),)


class BillingCouponRow(SQLModel, table=True):
    __tablename__ = "cp_billing_coupon"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    code: str = Field(index=True)
    promotion_code: str = Field(default="", index=True)
    single_use: bool = Field(default=False, index=True)
    effective_from: float = Field(default=0.0, index=True)
    effective_until: float | None = Field(default=None, index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_cp_billing_coupon"),)


class BillingPricingVersionRow(SQLModel, table=True):
    """Immutable org-wide pricing snapshots. Append-only."""

    __tablename__ = "cp_billing_pricing_version"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    version: int = Field(index=True)
    catalog_version: int | None = Field(default=None, index=True)
    published_at: float = Field(default=0.0, index=True)
    published_by: str = ""
    rolled_back_from: int | None = Field(default=None, index=True)
    entity_count: int = 0
    price_count: int = 0
    notes: str = ""
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (UniqueConstraint("org_id", "version", name="uq_cp_billing_pricing_version"),)


class BillingPricingActivityRow(SQLModel, table=True):
    """Append-only pricing history."""

    __tablename__ = "cp_billing_pricing_activity"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    action: str = Field(index=True)
    subject_kind: str = Field(default="", index=True)
    subject_code: str = Field(default="", index=True)
    actor_id: str = ""
    at: float = Field(default=0.0, index=True)
    pricing_version: int | None = Field(default=None, index=True)
    payload: dict = _payload()  # type: ignore[assignment]


PRICING_TABLES = (
    BillingPricingBookRow,
    BillingPriceBookVersionRow,
    BillingDiscountPolicyRow,
    BillingPromotionRow,
    BillingCouponRow,
    BillingPricingVersionRow,
    BillingPricingActivityRow,
)

PRICING_TABLE_NAMES = tuple(t.__tablename__ for t in PRICING_TABLES)
