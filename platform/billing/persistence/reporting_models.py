"""Compliance & Regulatory Reporting tables (M9.22) — additive to the M9.4–M9.21 schema.

Nine new `cp_billing_*` tables in the same projection-plus-payload row shape M9.4 established. No
existing table is altered, no column is dropped, and nothing in M9.2–M9.21 is touched — including
every table M9.22 reads and never writes.

## Why nine

Each holds something that must survive independently of the others:

* `cp_billing_report_definition` — what a report means, at one version, immutable;
* `cp_billing_reporting_period` — the window a filing covers, under a declared basis;
* `cp_billing_report_manifest` — the pinned inputs, **and the report's identity**;
* `cp_billing_report_source` — one pinned source per row, so the manifest paginates;
* `cp_billing_report` — the run;
* `cp_billing_report_exception` — named problems, never a generic failure;
* `cp_billing_report_validation` — the current verdict on a draft;
* `cp_billing_report_snapshot` — the finalized filing, content-addressed;
* `cp_billing_submission_package` — the bundle prepared for an external submission nobody here
  performs;
* `cp_billing_report_event` — the trail.

`cp_billing_report_source` is split from the manifest deliberately. §34 requires the source list to
paginate, and a manifest with three hundred pinned sources inside one JSON blob cannot — it has to
be loaded whole to show ten rows. The digest still covers all of them, so identity is unaffected.

## The two rows this milestone is built on

`cp_billing_report_definition` has its primary key derived from `(org, code, version)`. Publishing
the same version twice lands on the same row, which is what stops a reporting rule being edited
under a filing that already used it — and `definition_digest` lets a re-publication be *proven*
identical rather than assumed.

`cp_billing_report` has its primary key derived from `(org, definition, period, scope, manifest
digest, corrects)`. Three consequences, and they are the mandate's §7 guarantees as a schema:

* the same definition over the same period with the same pinned inputs is **one** report, so a
  retry files nothing twice;
* any input moving produces a **different** id — the corrected figures land beside what was filed,
  never on top of it;
* two organizations can never collide, because the org is inside the key.

## What is deliberately absent, and the one thing that is not

There is **no balance, no computed total and no currency-converted figure** anywhere in this
schema. `exception_count`, `blocking_exception_count`, `source_count` and `fact_count` are counts.
`completeness`, `evidence_completeness` and `integrity_posture` are labels.

The exception is narrow and declared: a report's **field values** live inside
`cp_billing_report.payload` and a finalized filing's inside `cp_billing_report_snapshot.payload`,
and a monetary field carries the integer minor units the owning domain wrote. That is the report —
a filing with no figures would report nothing. What is *not* here is a monetary **column**: the
moment one existed somebody would `SUM()` it across reports and publish a number beside one the
Ledger owns.

And there is **no card number, CVV, bank credential, provider secret, API key or authentication
token** — not as a column and not permitted inside any payload. Payment facts reach a report
through M9.16's safe identifiers. A test scans every model, DTO, event, snapshot and submission
package for the forbidden vocabulary.

## Immutability by construction

Manifests, sources, snapshots, submission packages and trail entries have **no update path at
all**. Exceptions have exactly one narrow one (the acknowledgement columns — an exception is never
deleted or cleared, because "somebody saw this and filed anyway" is what an examiner asks about).
Validations have one (the current verdict on a draft, which the snapshot pins at finalization).
Reports have one: the conditional lifecycle UPDATE.

`status` is a **projection over the event history**, not the authority. The history is the trail.
"""

from __future__ import annotations

from sqlalchemy import JSON as SAJSON
from sqlalchemy import Column, Index, UniqueConstraint
from sqlmodel import Field, SQLModel


def _payload() -> object:
    return Field(default_factory=dict, sa_column=Column("payload", SAJSON))


class BillingReportDefinitionRow(SQLModel, table=True):
    """What a report means, at one version. **Immutable once published.**

    `definition_digest` is the load-bearing column: re-publishing a version can be proven identical
    rather than assumed, so a filing bound to it keeps meaning what it meant."""

    __tablename__ = "cp_billing_report_definition"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    report_code: str = Field(index=True)
    # No `index=True`: SQLModel would auto-name it `ix_cp_billing_report_definition_version`,
    # which is exactly what `cp_billing_report.definition_version` auto-generates — two tables,
    # one index name. The composite below and the unique constraint cover this column anyway.
    version: int = Field(default=1)
    title: str = Field(default="")
    jurisdiction: str = Field(default="", index=True)
    frequency: str = Field(default="monthly", index=True)
    period_basis: str = Field(default="calendar_month", index=True)
    currency_basis: str = Field(default="not_applicable", index=True)
    declared_currency: str | None = Field(default=None)
    integrity_blocks_finalization: bool = Field(default=False)
    definition_digest: str = Field(default="", index=True)
    published_at: float = Field(default=0.0, index=True)
    published_by: str = Field(default="")
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id", "report_code", "version", name="uq_cp_billing_report_definition_version"
        ),
        Index("ix_cp_billing_report_definition_code", "org_id", "report_code", "version"),
    )


class BillingReportingPeriodRow(SQLModel, table=True):
    """One window a filing covers. **`basis` is inside the identity** — a calendar month is not an
    accounting period is not a tax period, and Billing already owns four of these."""

    __tablename__ = "cp_billing_reporting_period"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    basis: str = Field(default="calendar_month", index=True)
    period_key: str = Field(default="", index=True)
    window_start: float = Field(default=0.0, index=True)
    window_end: float = Field(default=0.0, index=True)
    state: str = Field(default="open", index=True)
    aligned_source_id: str = Field(default="", index=True)
    opened_at: float = Field(default=0.0)
    closed_at: float | None = Field(default=None, index=True)
    closed_by: str = Field(default="")
    version: int = Field(default=0, index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id", "basis", "period_key", name="uq_cp_billing_reporting_period_key"
        ),
        Index("ix_cp_billing_reporting_period_list", "org_id", "basis", "window_start"),
    )


class BillingReportManifestRow(SQLModel, table=True):
    """**The pinned inputs, and the report's identity.** No update path.

    `source_digest` is a canonical digest over every pinned reference. The report id derives from
    it, which is what makes "never overwrite a previous report because a source changed"
    structural rather than a rule somebody has to remember."""

    __tablename__ = "cp_billing_report_manifest"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    definition_id: str = Field(index=True)
    definition_version: int = Field(default=1, index=True)
    period_id: str = Field(index=True)
    account_id: str = Field(default="", index=True)  # empty ⇒ organization-wide
    source_digest: str = Field(default="", index=True)
    generator_version: str = Field(default="")
    generated_at: float = Field(default=0.0, index=True)
    source_count: int = Field(default=0)  # a **count**, never money
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        Index("ix_cp_billing_report_manifest_scope", "org_id", "definition_id", "period_id"),
    )


class BillingReportSourceRow(SQLModel, table=True):
    """One pinned source. **Its own table so the manifest paginates** (§34).

    `status` distinguishes `resolved`, `empty` and `unavailable`. The third is the one that
    matters: it never becomes zero, never disappears, and is what an examiner reads when asking
    what the filing could not see."""

    __tablename__ = "cp_billing_report_source"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    manifest_id: str = Field(index=True)
    domain: str = Field(default="", index=True)
    source_type: str = Field(default="", index=True)
    source_id: str = Field(default="", index=True)
    locator: str = Field(default="", index=True)
    status: str = Field(default="resolved", index=True)
    revision: str = Field(default="")
    digest: str = Field(default="", index=True)
    resolved_at: float = Field(default=0.0)
    fact_count: int = Field(default=0)  # a **count**, never money
    unavailable_reason: str = Field(default="")
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint("manifest_id", "locator", name="uq_cp_billing_report_source_locator"),
        Index("ix_cp_billing_report_source_page", "org_id", "manifest_id", "locator"),
        Index("ix_cp_billing_report_source_by_status", "org_id", "manifest_id", "status"),
    )


class BillingReportRow(SQLModel, table=True):
    """One report run. **The primary key encodes its pinned inputs.**

    Note the columns that do not exist: no `total_minor`, no `reported_amount`, no `currency`.
    Every figure lives in `payload` as the report's *values*, which is what the filing says —
    and a monetary column would invite a `SUM()` across filings that no domain owns."""

    __tablename__ = "cp_billing_report"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    report_code: str = Field(index=True)
    definition_id: str = Field(index=True)
    definition_version: int = Field(default=1, index=True)
    period_id: str = Field(index=True)
    manifest_id: str = Field(index=True)
    manifest_digest: str = Field(default="", index=True)
    account_id: str = Field(default="", index=True)
    status: str = Field(default="draft", index=True)
    completeness: str = Field(default="incomplete", index=True)
    evidence_completeness: str = Field(default="evidence_incomplete", index=True)
    integrity_posture: str = Field(default="indeterminate", index=True)
    report_digest: str = Field(default="", index=True)
    snapshot_id: str = Field(default="", index=True)
    exception_count: int = Field(default=0)  # counts, never money
    blocking_exception_count: int = Field(default=0, index=True)
    source_count: int = Field(default=0)
    corrects: str = Field(default="", index=True)
    corrected_by: str = Field(default="", index=True)
    created_at: float = Field(default=0.0, index=True)
    created_by: str = Field(default="")
    prepared_at: float | None = Field(default=None)
    validated_at: float | None = Field(default=None)
    finalized_at: float | None = Field(default=None, index=True)
    finalized_by: str = Field(default="")
    version: int = Field(default=0, index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        # The three reads a compliance queue, a paginated list and a period view all issue — every
        # one org-scoped, so a query cannot accidentally span tenants.
        Index("ix_cp_billing_report_queue", "org_id", "status", "completeness"),
        Index("ix_cp_billing_report_page", "org_id", "created_at", "id"),
        Index("ix_cp_billing_report_period", "org_id", "report_code", "period_id"),
    )


class BillingReportExceptionRow(SQLModel, table=True):
    """One **named** problem. No generic "report failed" exists in this schema.

    Never deleted and never cleared: an acknowledgement records that somebody saw it, which is a
    different fact from the problem being gone — and `blocks_finalization` is derived from
    `severity`, so acknowledging does not unblock a filing."""

    __tablename__ = "cp_billing_report_exception"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    report_id: str = Field(index=True)
    kind: str = Field(default="", index=True)
    severity: str = Field(default="blocking", index=True)
    subject: str = Field(default="", index=True)
    diagnostic: str = Field(default="")
    state: str = Field(default="open", index=True)
    field_key: str = Field(default="", index=True)
    source_locator: str = Field(default="", index=True)
    created_at: float = Field(default=0.0, index=True)
    acknowledged_at: float | None = Field(default=None)
    acknowledged_by: str = Field(default="")
    acknowledgement_reason: str = Field(default="")
    version: int = Field(default=0)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        Index("ix_cp_billing_report_exception_page", "org_id", "report_id", "created_at"),
        Index("ix_cp_billing_report_exception_block", "org_id", "report_id", "severity", "state"),
    )


class BillingReportValidationRow(SQLModel, table=True):
    """The **current** verdict on a draft. Replaced by a later validation of the same report.

    Deliberately not append-only: a validation result is a statement about a draft, not a filing.
    What is permanent is the snapshot, which pins the verdict that let a report finalize, and the
    trail, which records every validation that ran."""

    __tablename__ = "cp_billing_report_validation"

    report_id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    passed: bool = Field(default=False, index=True)
    definition_version: int = Field(default=1)
    check_count: int = Field(default=0)  # a **count**, never money
    failure_count: int = Field(default=0, index=True)
    validated_at: float = Field(default=0.0, index=True)
    validated_by: str = Field(default="")
    payload: dict = _payload()  # type: ignore[assignment]


class BillingReportSnapshotRow(SQLModel, table=True):
    """**The finalized filing, content-addressed and immutable.** No update path.

    `report_digest` is recomputable from `payload` by anyone holding the filing, which is what
    makes a submission defensible rather than merely archived. Everything in the representation is
    pinned state: the definition version, the period, the manifest digest, and the values as
    computed — so a source moving next week changes nothing about what this says."""

    __tablename__ = "cp_billing_report_snapshot"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    report_id: str = Field(index=True)
    definition_id: str = Field(index=True)
    definition_version: int = Field(default=1, index=True)
    manifest_id: str = Field(index=True)
    manifest_digest: str = Field(default="", index=True)
    report_digest: str = Field(default="", index=True)
    completeness: str = Field(default="complete", index=True)
    evidence_completeness: str = Field(default="evidence_complete", index=True)
    validation_passed: bool = Field(default=True, index=True)
    finalized_at: float = Field(default=0.0, index=True)
    finalized_by: str = Field(default="")
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint("org_id", "report_id", name="uq_cp_billing_report_snapshot_report"),
    )


class BillingSubmissionPackageRow(SQLModel, table=True):
    """A bundle prepared **for** an external submission this milestone never performs.

    `submitted_at` and `submission_reference` are declared and always empty: §24 defers
    transmission to a future adapter, and there is no network client, regulator endpoint or
    credential anywhere in M9.22. A test asserts nothing here can write them."""

    __tablename__ = "cp_billing_submission_package"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    report_id: str = Field(index=True)
    snapshot_id: str = Field(index=True)
    definition_id: str = Field(index=True)
    definition_version: int = Field(default=1)
    report_digest: str = Field(default="", index=True)
    manifest_digest: str = Field(default="", index=True)
    package_digest: str = Field(default="", index=True)
    completeness: str = Field(default="complete", index=True)
    evidence_completeness: str = Field(default="evidence_complete")
    validation_passed: bool = Field(default=True)
    prepared_at: float = Field(default=0.0, index=True)
    prepared_by: str = Field(default="")
    submitted_at: float | None = Field(default=None)  # always None in M9.22
    submission_reference: str = Field(default="")  # always empty in M9.22
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id", "report_id", "report_digest", name="uq_cp_billing_submission_digest"
        ),
    )


class BillingReportEventRow(SQLModel, table=True):
    """One immutable line of a report's trail, totally ordered per report. No update, no delete.

    §25 requires a filing to answer *who finalized it, when, on what evidence, and what changed*
    **without depending on application logs**. These rows are that answer."""

    __tablename__ = "cp_billing_report_event"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    report_id: str = Field(index=True)
    sequence: int = Field(default=0, index=True)
    kind: str = Field(default="", index=True)
    at: float = Field(default=0.0, index=True)
    actor_id: str = Field(default="", index=True)
    subject: str = Field(default="", index=True)
    summary: str = Field(default="")
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id", "report_id", "sequence", name="uq_cp_billing_report_event_seq"
        ),
        Index("ix_cp_billing_report_event_read", "org_id", "report_id", "sequence"),
    )


REPORTING_TABLES: tuple[type[SQLModel], ...] = (
    BillingReportDefinitionRow,
    BillingReportingPeriodRow,
    BillingReportManifestRow,
    BillingReportSourceRow,
    BillingReportRow,
    BillingReportExceptionRow,
    BillingReportValidationRow,
    BillingReportSnapshotRow,
    BillingSubmissionPackageRow,
    BillingReportEventRow,
)
