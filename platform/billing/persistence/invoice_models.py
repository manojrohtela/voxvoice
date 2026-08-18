"""Invoice Engine tables (M9.14) — additive to the M9.4–M9.13 schema.

Seven new `cp_billing_*` tables in the same projection-plus-payload row shape M9.4 established. No
existing table is altered, no column is dropped, and no Epic 5–8 table is touched — including M9.2's
`cp_billing_invoice` and `cp_billing_credit_note`, which stay exactly as they are and which the
minimal M9.5 `/invoices` surface still reads and writes.

Four are **append-only by construction**: snapshots, revisions, allocated numbers, and renderings.
There is no update path and no delete path to any of them.

## Why document content lives in its own table

`cp_billing_commercial_invoice` carries a *header*: state, number, and the digest of the content it
sealed. The content itself is a row in `cp_billing_invoice_snapshot`, keyed by
`(org_id, document_id, revision)` and never updated.

That split is the immutability mechanism, not a normalisation preference. An issued document pins
one revision's digest, and every read resolves *that digest* through
`SqlInvoiceSnapshotRepository.by_digest`. If a later revision somehow existed, it could not become
the issued document by being newer, because the lookup is by content address. Immutability that
depends on nobody calling an update method is immutability that lasts until someone does.

## Why numbers have their own table

`cp_billing_invoice_number` is the **allocation ledger**: one row per allocated sequence, with
`(org_id, series_key, sequence)` as the primary key. A duplicate number is therefore not a bug that
validation might catch — it is a row that cannot be inserted.

`cp_billing_invoice_series` is the durable per-series counter it is drawn from. Both are written
inside the issuing transaction, which is what makes the series **gapless across a crash**: a process
that dies between allocating and issuing rolls back both, so the number is never consumed. A
pre-allocated pool cannot offer this, and where a jurisdiction requires gapless numbering there is
no legal way to fill a hole after the fact.
"""

from __future__ import annotations

from sqlalchemy import JSON as SAJSON
from sqlalchemy import Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def _payload() -> object:
    return Field(default_factory=dict, sa_column=Column("payload", SAJSON))


class BillingCommercialInvoiceRow(SQLModel, table=True):
    """A customer document's header. **Carries no lines and no content.**

    Named beside M9.2's `cp_billing_invoice`, which is untouched. The two are deliberately separate:
    an M9.2 `Invoice` carries its lines inline, and this one carries the digest of a sealed snapshot
    that holds them — which is what lets the content be append-only while the lifecycle advances."""

    __tablename__ = "cp_billing_commercial_invoice"

    id: str = Field(primary_key=True)  # derived from what the document documents
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    kind: str = Field(default="invoice", index=True)
    state: str = Field(default="draft", index=True)
    currency: str = Field(default="INR", index=True)
    number_formatted: str = Field(default="", index=True)
    number_sequence: int = Field(default=0, index=True)
    entity_code: str = Field(default="", index=True)
    jurisdiction: str = Field(default="", index=True)
    revision: int = Field(default=0, index=True)
    issued_revision: int = Field(default=0, index=True)
    issued_snapshot_digest: str = Field(default="", index=True)
    accounting_period_key: str = Field(default="", index=True)
    period_start: float | None = Field(default=None, index=True)
    period_end: float | None = Field(default=None, index=True)
    total_minor: int = Field(default=0, index=True)
    amount_due_minor: int = Field(default=0, index=True)
    delivery_state: str = Field(default="unknown", index=True)
    related_document_ids: str = Field(default="", index=True)  # comma-joined, for `related_to`
    prepared_at: float = Field(default=0.0, index=True)
    issued_at: float | None = Field(default=None, index=True)
    due_at: float | None = Field(default=None, index=True)
    settled_at: float | None = None
    updated_at: float = 0.0
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_cp_billing_commercial_invoice_identity"),
    )


class BillingInvoiceSnapshotRow(SQLModel, table=True):
    """**Append-only document content.** One row per revision; never updated, never deleted.

    `digest` is indexed because it is the *lookup key an issued document uses*, not merely a
    checksum: resolving content by address is what makes byte-identical historical reproduction a
    property of the schema rather than of care."""

    __tablename__ = "cp_billing_invoice_snapshot"

    id: str = Field(primary_key=True)  # derived from (document, revision)
    org_id: str = Field(index=True)
    document_id: str = Field(index=True)
    account_id: str = Field(index=True)
    revision: int = Field(default=0, index=True)
    kind: str = Field(default="invoice", index=True)
    digest: str = Field(default="", index=True)
    currency: str = Field(default="INR", index=True)
    number_formatted: str = Field(default="", index=True)
    total_minor: int = Field(default=0, index=True)
    amount_due_minor: int = Field(default=0, index=True)
    tax_total_minor: int = Field(default=0)
    line_count: int = Field(default=0)
    accounting_period_key: str = Field(default="", index=True)
    tax_determination_id: str = Field(default="", index=True)
    template_code: str = Field(default="standard", index=True)
    template_version: int = Field(default=1, index=True)
    issue_date: float | None = Field(default=None, index=True)
    due_date: float | None = Field(default=None, index=True)
    prepared_at: float = Field(default=0.0, index=True)
    prepared_by: str = ""
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id", "document_id", "revision", name="uq_cp_billing_invoice_snapshot_revision"
        ),
    )


class BillingInvoiceRevisionRow(SQLModel, table=True):
    """Append-only revision history — who prepared what, when, and why.

    Kept beside the snapshots rather than derived from them because the *reason* a revision exists
    is not in its content, and "why was this redrafted four times before issue?" is a question a
    dispute asks."""

    __tablename__ = "cp_billing_invoice_revision"

    id: str = Field(primary_key=True)  # derived from (document, revision)
    org_id: str = Field(index=True)
    document_id: str = Field(index=True)
    revision: int = Field(default=0, index=True)
    snapshot_digest: str = Field(default="", index=True)
    at: float = Field(default=0.0, index=True)
    actor_id: str = ""
    reason: str = ""
    issued: bool = Field(default=False, index=True)
    payload: dict = _payload()  # type: ignore[assignment]


class BillingInvoiceNumberRow(SQLModel, table=True):
    """The **allocation ledger**: one row per allocated sequence.

    `(org_id, series_key, sequence)` is the primary key, so a duplicate invoice number is not a
    finding a validator might report — it is a row the database refuses. That is the difference
    between a compliance property that is checked and one that is guaranteed."""

    __tablename__ = "cp_billing_invoice_number"

    org_id: str = Field(primary_key=True)
    series_key: str = Field(primary_key=True)
    sequence: int = Field(primary_key=True)
    document_id: str = Field(default="", index=True)
    formatted: str = Field(default="", index=True)
    allocated_at: float = Field(default=0.0, index=True)


class BillingInvoiceSeriesRow(SQLModel, table=True):
    """A durable per-series counter.

    A stored counter rather than `max(sequence) + 1`, for the same reason M9.13 stores its entry
    sequence: a derived maximum hands a number back after a rollback, and a reused invoice number is
    a compliance failure that cannot be corrected — both documents have already been sent."""

    __tablename__ = "cp_billing_invoice_series"

    org_id: str = Field(primary_key=True)
    series_key: str = Field(primary_key=True)
    last_sequence: int = 0
    updated_at: float = 0.0


class BillingRenderedDocumentRow(SQLModel, table=True):
    """**Append-only renderings**, keyed by what they rendered.

    Stored rather than only recomputed so "the PDF the customer holds" is an artefact the platform
    can produce again identically. The key includes the snapshot digest *and* the template version,
    because a template revision produces a different artefact from the same content — and both must
    remain readable."""

    __tablename__ = "cp_billing_rendered_document"

    id: str = Field(primary_key=True)  # derived from (document, digest, format, template version)
    org_id: str = Field(index=True)
    document_id: str = Field(index=True)
    snapshot_digest: str = Field(default="", index=True)
    format: str = Field(default="html", index=True)
    template_code: str = Field(default="standard", index=True)
    template_version: int = Field(default=1, index=True)
    byte_size: int = Field(default=0)
    rendered_at: float = Field(default=0.0, index=True)
    content: str = ""
    payload: dict = _payload()  # type: ignore[assignment]


class BillingInvoiceActivityRow(SQLModel, table=True):
    """Append-only invoice history — every preparation, revision, issuance, correction, rendering,
    and delivery report."""

    __tablename__ = "cp_billing_invoice_activity"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    action: str = Field(index=True)
    actor_id: str = ""
    at: float = Field(default=0.0, index=True)
    account_id: str = Field(default="", index=True)
    document_id: str = Field(default="", index=True)
    document_number: str = Field(default="", index=True)
    revision: int = 0
    amount_minor: int = 0
    currency: str = Field(default="", index=True)
    reason: str = ""
    payload: dict = _payload()  # type: ignore[assignment]


INVOICE_TABLES = (
    BillingCommercialInvoiceRow,
    BillingInvoiceSnapshotRow,
    BillingInvoiceRevisionRow,
    BillingInvoiceNumberRow,
    BillingInvoiceSeriesRow,
    BillingRenderedDocumentRow,
    BillingInvoiceActivityRow,
)

INVOICE_TABLE_NAMES = tuple(t.__tablename__ for t in INVOICE_TABLES)
