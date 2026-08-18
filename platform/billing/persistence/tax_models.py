"""Tax Determination tables (M9.15) — additive to the M9.4–M9.14 schema.

Seven new `cp_billing_*` tables in the same projection-plus-payload row shape M9.4 established. No
existing table is altered, no column is dropped, and no Epic 5–8 table is touched — including
M9.2's `cp_billing_tax_profile` and `cp_billing_tax_determination`, which stay exactly as they are.
The second of those is the **projection target**: `FinalizeTaxAssessment` writes it from
`TaxAssessment.to_determination()`, so the record M9.5's `/taxes` surface and M9.14's document
assembly already read is a view of one truth rather than a second copy of it.

Three are **append-only by construction**: declared rates, assessments, and the audit trail. There
is no update path and no delete path to any of them.

## Why there is no rate table in the sense that phrase usually means

`cp_billing_tax_rate` holds **operator-declared evidence**, not a shipped table of law. Every row
carries the reference that set it, the version it was published under, and the window it applies
in; nothing in the codebase seeds it, and a fresh installation determines no tax at all until an
operator declares one. That is the shape ADR-0023 TX1 requires — determination is a rented edge,
evidence is an owned fact — and it is why the rows are append-only: superseding a rate writes a new
row beside the one it replaces, because an issued invoice must stay explainable against the rate
that was in force when it was issued (TX2).

## Why assessments are append-only with a narrow state column

`cp_billing_tax_assessment.id` is derived from the determination's **basis** — the immutable inputs
— so re-determining the same facts is one row rather than two competing answers. `state` is the one
column that moves, and only along the FSM, which has no edge out of `final`. Every figure, every
component, every piece of provenance, and the digest over the lot are written once and never
amended: a correction is a **new** assessment carrying `supersedes_assessment_id`, so a reader can
answer not just "what is the tax?" but "what did you charge, and what did you change it to?"
"""

from __future__ import annotations

from sqlalchemy import JSON as SAJSON
from sqlalchemy import Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def _payload() -> object:
    return Field(default_factory=dict, sa_column=Column("payload", SAJSON))


class BillingTaxJurisdictionRow(SQLModel, table=True):
    """A declared place with taxing authority. `(org_id, code)` is unique — a duplicate
    jurisdiction is a duplicate nexus, and a duplicate nexus is a supplier registered twice in the
    same place. Retired by flag, never removed."""

    __tablename__ = "cp_billing_tax_jurisdiction"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    code: str = Field(index=True)
    display_name: str = ""
    country: str = Field(default="", index=True)
    region: str = Field(default="", index=True)
    level: str = Field(default="country", index=True)
    regime: str = Field(default="none", index=True)
    parent_code: str = Field(default="", index=True)
    is_active: bool = Field(default=True, index=True)
    declared_at: float = Field(default=0.0, index=True)
    declared_by: str = ""
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_cp_billing_tax_jurisdiction"),)


class BillingTaxClassificationRow(SQLModel, table=True):
    """What is supplied, in the authority's vocabulary. No delete: a classification an issued
    invoice referenced must remain readable."""

    __tablename__ = "cp_billing_tax_classification"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    code: str = Field(index=True)
    display_name: str = ""
    default_treatment: str = Field(default="standard", index=True)
    is_digital_service: bool = Field(default=False, index=True)
    declared_at: float = Field(default=0.0, index=True)
    declared_by: str = ""
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_cp_billing_tax_classification"),)


class BillingTaxNexusRow(SQLModel, table=True):
    """The supplier's obligation in one jurisdiction, and the registration that discharges it.
    Ended by setting `effective_until` — never by removing the row, because "were we registered
    there in March?" is a question an audit asks."""

    __tablename__ = "cp_billing_tax_nexus"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    entity_code: str = Field(index=True)
    jurisdiction_code: str = Field(index=True)
    kind: str = Field(default="registration", index=True)
    registration_number: str = Field(default="", index=True)
    registration_regime: str = Field(default="", index=True)
    effective_from: float = Field(default=0.0, index=True)
    effective_until: float | None = Field(default=None, index=True)
    declared_at: float = Field(default=0.0, index=True)
    declared_by: str = ""
    payload: dict = _payload()  # type: ignore[assignment]


class BillingTaxExemptionRow(SQLModel, table=True):
    """A customer's relief from tax in one jurisdiction. Revoked by timestamp, never deleted: an
    invoice issued while an exemption was valid stays explainable after the certificate expires."""

    __tablename__ = "cp_billing_tax_exemption"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    jurisdiction_code: str = Field(index=True)
    kind: str = Field(default="certificate", index=True)
    certificate_ref: str = Field(default="", index=True)
    effective_from: float = Field(default=0.0, index=True)
    effective_until: float | None = Field(default=None, index=True)
    revoked_at: float | None = Field(default=None, index=True)
    recorded_at: float = Field(default=0.0, index=True)
    recorded_by: str = ""
    payload: dict = _payload()  # type: ignore[assignment]


class BillingTaxRateRow(SQLModel, table=True):
    """**Operator-declared, effective-dated rate evidence. Append-only.**

    Not a shipped table of law: nothing seeds this, and a fresh installation determines no tax until
    an operator declares a rate with the notification that set it. `reference` and `source_version`
    are what make a row evidence rather than a number."""

    __tablename__ = "cp_billing_tax_rate"

    id: str = Field(primary_key=True)  # derived from jurisdiction + type + class + effective start
    org_id: str = Field(index=True)
    jurisdiction_code: str = Field(index=True)
    tax_type: str = Field(default="", index=True)
    name: str = Field(default="", index=True)
    rate_basis_points: int = Field(default=0, index=True)
    classification_code: str = Field(default="", index=True)
    treatment: str = Field(default="standard", index=True)
    effective_from: float = Field(default=0.0, index=True)
    effective_until: float | None = Field(default=None, index=True)
    reference: str = Field(default="", index=True)
    source_version: str = Field(default="", index=True)
    retired_at: float | None = Field(default=None, index=True)
    declared_at: float = Field(default=0.0, index=True)
    declared_by: str = ""
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (UniqueConstraint("org_id", "id", name="uq_cp_billing_tax_rate_identity"),)


class BillingTaxAssessmentRow(SQLModel, table=True):
    """**Append-only determinations.** The id is derived from the basis, so re-determining the same
    facts is one row rather than two competing answers.

    `state` is the only column that moves, and only along an FSM with no edge out of `final`. Every
    figure, every component, every piece of provenance, and the digest over the lot live in the
    payload and are written once."""

    __tablename__ = "cp_billing_tax_assessment"

    id: str = Field(primary_key=True)  # derived from the basis digest
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    subject_id: str = Field(index=True)
    subject_kind: str = Field(default="invoice", index=True)
    state: str = Field(default="provisional", index=True)
    treatment: str = Field(default="standard", index=True)
    regime: str = Field(default="none", index=True)
    currency: str = Field(default="INR", index=True)
    version: int = Field(default=1, index=True)
    jurisdiction_code: str = Field(default="", index=True)
    gst_split: str = Field(default="not_applicable", index=True)
    taxable_base_minor: int = Field(default=0, index=True)
    tax_total_minor: int = Field(default=0, index=True)
    withholding_minor: int = 0
    line_count: int = 0
    component_count: int = 0
    nexus_id: str = Field(default="", index=True)
    registration_used: str = Field(default="", index=True)
    exemption_id: str = Field(default="", index=True)
    is_overridden: bool = Field(default=False, index=True)
    is_sourced: bool = Field(default=True, index=True)
    supersedes_assessment_id: str = Field(default="", index=True)
    digest: str = Field(default="", index=True)
    assessed_at: float = Field(default=0.0, index=True)
    assessed_by: str = ""
    finalized_at: float | None = Field(default=None, index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_cp_billing_tax_assessment_identity"),
    )


class BillingTaxActivityRow(SQLModel, table=True):
    """Append-only tax audit trail — including the determinations that concluded nothing new.
    "We determined this again and reached the same conclusion" is the evidence determination is
    deterministic, and it is exactly what an auditor asks for when a rate has changed and a figure
    has not."""

    __tablename__ = "cp_billing_tax_activity"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    action: str = Field(index=True)
    actor_id: str = ""
    at: float = Field(default=0.0, index=True)
    account_id: str = Field(default="", index=True)
    subject_id: str = Field(default="", index=True)
    assessment_id: str = Field(default="", index=True)
    jurisdiction_code: str = Field(default="", index=True)
    amount_minor: int = 0
    currency: str = Field(default="", index=True)
    reason: str = ""
    payload: dict = _payload()  # type: ignore[assignment]


TAX_TABLES = (
    BillingTaxJurisdictionRow,
    BillingTaxClassificationRow,
    BillingTaxNexusRow,
    BillingTaxExemptionRow,
    BillingTaxRateRow,
    BillingTaxAssessmentRow,
    BillingTaxActivityRow,
)

TAX_TABLE_NAMES = tuple(t.__tablename__ for t in TAX_TABLES)
