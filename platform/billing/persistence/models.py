"""Persistence models (M9.4) — SQLModel tables for the Billing Platform.

These are the **persistence representation** only. Neither the Foundation nor the
Application ever sees one: `mapping.py` translates row ↔ frozen domain aggregate at the
boundary, and no SQLModel type escapes this package (ADR-0031 §13; the M8.4 precedent).

**Namespace isolation.** Every table is prefixed `cp_billing_*`. `cp_` keeps Billing inside
the one Control-Plane database namespace the Epic 8 tables already occupy; `_billing_`
guarantees Billing never reuses, extends, or collides with a legacy table. No Epic 5–8
table is read, written, or altered by this package.

**Row shape — projection + payload.** Each row carries (a) *projected columns* for the keys
the repository contracts actually query on — identity, `org_id`, `account_id`, lifecycle
state, period bounds, idempotency keys — indexed and typed, and (b) a portable JSON
`payload` holding the canonically encoded aggregate. The aggregate is reconstructed from
the payload, so a Foundation field addition needs no migration, while every query the 29
contracts expose still runs on an indexed column rather than a JSON scan. Projected columns
are *derived from* the payload and never a second source of truth for it (§A2.5).

Referential integrity is enforced by the Foundation's validators and services, so these
tables use indexed plain columns rather than DB-level foreign keys — the same decoupled,
order-free table creation Epic 8 chose.
"""

from __future__ import annotations

from sqlalchemy import JSON as SAJSON
from sqlalchemy import Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def _payload() -> object:
    return Field(default_factory=dict, sa_column=Column("payload", SAJSON))


# ── accounts ────────────────────────────────────────────────────────────────────


class BillingAccountRow(SQLModel, table=True):
    __tablename__ = "cp_billing_account"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    parent_account_id: str = Field(default="", index=True)
    status: str = Field(default="active", index=True)
    currency: str = ""
    created_at: float = 0.0
    payload: dict = _payload()  # type: ignore[assignment]


# ── catalog + pricing ───────────────────────────────────────────────────────────


class BillingPlanRow(SQLModel, table=True):
    __tablename__ = "cp_billing_plan"

    id: str = Field(primary_key=True)
    org_id: str = Field(default="", index=True)
    code: str = Field(index=True)
    version: int = 1
    published: bool = Field(default=False, index=True)
    tier: str = ""
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (UniqueConstraint("org_id", "code", "version", name="uq_cp_billing_plan"),)


class BillingFeatureRow(SQLModel, table=True):
    __tablename__ = "cp_billing_feature"

    id: str = Field(primary_key=True)
    code: str = Field(index=True, unique=True)
    kind: str = ""
    payload: dict = _payload()  # type: ignore[assignment]


class BillingMeterRow(SQLModel, table=True):
    __tablename__ = "cp_billing_meter"

    id: str = Field(primary_key=True)
    org_id: str = Field(default="", index=True)
    code: str = Field(index=True)
    version: int = 1
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_cp_billing_meter"),)


class BillingCatalogSnapshotRow(SQLModel, table=True):
    """Immutable published catalog versions — appended, never updated."""

    __tablename__ = "cp_billing_catalog_snapshot"

    id: int | None = Field(default=None, primary_key=True)
    org_id: str = Field(default="", index=True)
    version: int = Field(index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (UniqueConstraint("org_id", "version", name="uq_cp_billing_catalog"),)


class BillingPriceBookRow(SQLModel, table=True):
    __tablename__ = "cp_billing_price_book"

    id: str = Field(primary_key=True)
    org_id: str = Field(default="", index=True)
    code: str = Field(index=True)
    version: int = 1
    published: bool = False
    currency: str = ""
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (UniqueConstraint("id", "version", name="uq_cp_billing_price_book"),)


# ── subscriptions + entitlements ────────────────────────────────────────────────


class BillingSubscriptionRow(SQLModel, table=True):
    __tablename__ = "cp_billing_subscription"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    state: str = Field(default="draft", index=True)
    plan_code: str = ""
    plan_version: int = 1
    contract_id: str = Field(default="", index=True)
    payload: dict = _payload()  # type: ignore[assignment]


class BillingEntitlementGrantRow(SQLModel, table=True):
    __tablename__ = "cp_billing_entitlement_grant"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    feature_code: str = Field(default="", index=True)
    source: str = ""
    granted_at: float = 0.0
    revoked_at: float | None = Field(default=None, index=True)
    payload: dict = _payload()  # type: ignore[assignment]


class BillingEntitlementSnapshotRow(SQLModel, table=True):
    """Published entitlement snapshots — immutable and versioned (ADR-0023 E4)."""

    __tablename__ = "cp_billing_entitlement_snapshot"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    version: int = Field(index=True)
    issued_at: float = 0.0
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint("org_id", "account_id", "version", name="uq_cp_billing_snapshot"),
    )


# ── metering (append-only) ──────────────────────────────────────────────────────


class BillingUsageRecordRow(SQLModel, table=True):
    """Append-only. `(source_event_id, meter_code)` is unique — the database itself is the
    last line of defence for exactly-once billing under at-least-once delivery (M4)."""

    __tablename__ = "cp_billing_usage_record"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    meter_code: str = Field(index=True)
    source_event_id: str = Field(index=True)
    period_start: float = Field(default=0.0, index=True)
    period_end: float = 0.0
    status: str = "recorded"
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint("source_event_id", "meter_code", name="uq_cp_billing_usage_source"),
    )


class BillingUsageAggregateRow(SQLModel, table=True):
    """Derived and recomputable, therefore upsertable — keyed by (org, account, meter,
    period)."""

    __tablename__ = "cp_billing_usage_aggregate"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    meter_code: str = Field(index=True)
    period_start: float = Field(default=0.0, index=True)
    period_end: float = 0.0
    sealed: bool = False
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "account_id",
            "meter_code",
            "period_start",
            "period_end",
            name="uq_cp_billing_usage_aggregate",
        ),
    )


class BillingUsageQuarantineRow(SQLModel, table=True):
    __tablename__ = "cp_billing_usage_quarantine"

    id: str = Field(primary_key=True)
    org_id: str = Field(default="", index=True)
    meter_code: str = Field(default="", index=True)
    received_at: float = Field(default=0.0, index=True)
    payload: dict = _payload()  # type: ignore[assignment]


# ── ledger + credits (append-only) ──────────────────────────────────────────────


class BillingLedgerEntryRow(SQLModel, table=True):
    """Append-only revenue truth (ADR-0023 RS1). No update path exists above this row."""

    __tablename__ = "cp_billing_ledger_entry"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    invoice_id: str = Field(default="", index=True)
    payment_id: str = Field(default="", index=True)
    entry_type: str = ""
    direction: str = ""
    amount_minor: int = 0
    currency: str = ""
    recorded_at: float = Field(default=0.0, index=True)
    payload: dict = _payload()  # type: ignore[assignment]


class BillingCreditGrantRow(SQLModel, table=True):
    __tablename__ = "cp_billing_credit_grant"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    kind: str = ""
    amount_minor: int = 0
    currency: str = ""
    granted_at: float = 0.0
    expires_at: float | None = None
    payload: dict = _payload()  # type: ignore[assignment]


class BillingCreditMovementRow(SQLModel, table=True):
    __tablename__ = "cp_billing_credit_movement"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    grant_id: str = Field(default="", index=True)
    invoice_id: str = Field(default="", index=True)
    amount_minor: int = 0
    currency: str = ""
    occurred_at: float = 0.0
    payload: dict = _payload()  # type: ignore[assignment]


# ── invoicing ───────────────────────────────────────────────────────────────────


class BillingInvoiceRow(SQLModel, table=True):
    __tablename__ = "cp_billing_invoice"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    state: str = Field(default="draft", index=True)
    subscription_id: str = Field(default="", index=True)
    invoice_number: str = Field(default="", index=True)
    period_start: float = 0.0
    period_end: float = 0.0
    issued_at: float | None = None
    payload: dict = _payload()  # type: ignore[assignment]


class BillingInvoiceSequenceRow(SQLModel, table=True):
    """One counter per (legal entity, jurisdiction) — the gapless, never-reused invoice
    number sequence (ADR-0023 IN4)."""

    __tablename__ = "cp_billing_invoice_sequence"

    id: str = Field(primary_key=True)  # f"{entity_code}:{jurisdiction}"
    entity_code: str = ""
    jurisdiction: str = ""
    next_sequence: int = 1


class BillingCreditNoteRow(SQLModel, table=True):
    __tablename__ = "cp_billing_credit_note"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    invoice_id: str = Field(index=True)
    amount_minor: int = 0
    currency: str = ""
    issued_at: float = 0.0
    payload: dict = _payload()  # type: ignore[assignment]


class BillingRunRow(SQLModel, table=True):
    __tablename__ = "cp_billing_run"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    period_start: float = Field(default=0.0, index=True)
    period_end: float = 0.0
    stage: str = ""
    invoice_id: str = ""
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id", "account_id", "period_start", "period_end", name="uq_cp_billing_run"
        ),
    )


# ── payments ────────────────────────────────────────────────────────────────────


class BillingPaymentRow(SQLModel, table=True):
    __tablename__ = "cp_billing_payment"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    invoice_id: str = Field(default="", index=True)
    state: str = Field(default="required", index=True)
    provider_code: str = Field(default="", index=True)
    amount_minor: int = 0
    currency: str = ""
    requested_at: float = 0.0
    payload: dict = _payload()  # type: ignore[assignment]


class BillingPaymentInstrumentRow(SQLModel, table=True):
    __tablename__ = "cp_billing_payment_instrument"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    provider_code: str = ""
    kind: str = ""
    is_default: bool = False
    payload: dict = _payload()  # type: ignore[assignment]


class BillingDunningRow(SQLModel, table=True):
    __tablename__ = "cp_billing_dunning"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    payment_id: str = Field(index=True)
    state: str = ""
    next_attempt_at: float | None = None
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (UniqueConstraint("org_id", "payment_id", name="uq_cp_billing_dunning"),)


class BillingReconciliationRow(SQLModel, table=True):
    """Append-only reconciliation evidence; the finding carries no domain id, so the row
    uses a surrogate key."""

    __tablename__ = "cp_billing_reconciliation"

    id: int | None = Field(default=None, primary_key=True)
    payment_id: str = Field(index=True)
    provider_code: str = Field(default="", index=True)
    verdict: str = Field(default="", index=True)
    observed_at: float = 0.0
    payload: dict = _payload()  # type: ignore[assignment]


# ── refunds ─────────────────────────────────────────────────────────────────────


class BillingRefundRow(SQLModel, table=True):
    __tablename__ = "cp_billing_refund"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    payment_id: str = Field(default="", index=True)
    invoice_id: str = Field(default="", index=True)
    request_key: str = Field(default="", index=True)
    state: str = Field(default="requested", index=True)
    amount_minor: int = 0
    currency: str = ""
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint("org_id", "request_key", name="uq_cp_billing_refund_request"),
    )


class BillingDisputeRow(SQLModel, table=True):
    __tablename__ = "cp_billing_dispute"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    payment_id: str = Field(index=True)
    stage: str = ""
    amount_minor: int = 0
    currency: str = ""
    payload: dict = _payload()  # type: ignore[assignment]


# ── tax ─────────────────────────────────────────────────────────────────────────


class BillingTaxProfileRow(SQLModel, table=True):
    __tablename__ = "cp_billing_tax_profile"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    regime: str = ""
    country: str = ""
    updated_at: float = 0.0
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (UniqueConstraint("org_id", "account_id", name="uq_cp_billing_tax_profile"),)


class BillingTaxDeterminationRow(SQLModel, table=True):
    """Immutable tax evidence — appended once per invoice, never recomputed (TX2/TX4)."""

    __tablename__ = "cp_billing_tax_determination"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    invoice_id: str = Field(index=True)
    regime: str = ""
    treatment: str = ""
    determined_at: float = 0.0
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint("org_id", "invoice_id", name="uq_cp_billing_tax_determination"),
    )


# ── contracts, licenses, events ─────────────────────────────────────────────────


class BillingContractRow(SQLModel, table=True):
    __tablename__ = "cp_billing_contract"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    code: str = Field(default="", index=True)
    state: str = Field(default="draft", index=True)
    payload: dict = _payload()  # type: ignore[assignment]


class BillingLicenseRow(SQLModel, table=True):
    __tablename__ = "cp_billing_license"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    contract_id: str = Field(default="", index=True)
    kind: str = ""
    state: str = Field(default="issued", index=True)
    payload: dict = _payload()  # type: ignore[assignment]


class BillingEventRow(SQLModel, table=True):
    """Append-only `billing.*` fact stream — Billing keeps no private audit log; this is the
    durable hand-off point to the Epic 8 Audit Platform (ADR-0023 §5.2)."""

    __tablename__ = "cp_billing_event"

    id: str = Field(primary_key=True)
    type: str = Field(index=True)
    ts: float = Field(default=0.0, index=True)
    org_id: str = Field(default="", index=True)
    subject_id: str = Field(default="", index=True)
    actor_id: str = ""
    correlation_id: str | None = None
    payload: dict = _payload()  # type: ignore[assignment]


# The registry the `BillingDatabase` creates and the migration chain operates on. Ordered
# by commercial lifecycle for readability; creation order is irrelevant (no DB-level FKs).
BILLING_TABLES = (
    BillingAccountRow,
    BillingPlanRow,
    BillingFeatureRow,
    BillingMeterRow,
    BillingCatalogSnapshotRow,
    BillingPriceBookRow,
    BillingSubscriptionRow,
    BillingEntitlementGrantRow,
    BillingEntitlementSnapshotRow,
    BillingUsageRecordRow,
    BillingUsageAggregateRow,
    BillingUsageQuarantineRow,
    BillingLedgerEntryRow,
    BillingCreditGrantRow,
    BillingCreditMovementRow,
    BillingInvoiceRow,
    BillingInvoiceSequenceRow,
    BillingCreditNoteRow,
    BillingRunRow,
    BillingPaymentRow,
    BillingPaymentInstrumentRow,
    BillingDunningRow,
    BillingReconciliationRow,
    BillingRefundRow,
    BillingDisputeRow,
    BillingTaxProfileRow,
    BillingTaxDeterminationRow,
    BillingContractRow,
    BillingLicenseRow,
    BillingEventRow,
)

BILLING_TABLE_NAMES = tuple(t.__tablename__ for t in BILLING_TABLES)
