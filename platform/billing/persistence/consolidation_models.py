"""Period Scope & Consolidation tables (M9.23) — additive to the M9.4–M9.22 schema.

Ten new `cp_billing_*` tables in the same projection-plus-payload row shape M9.4 established. No
existing table is altered, no column is dropped, and nothing in M9.2–M9.22 is touched — including
`cp_billing_commercial_account`, whose hierarchy this milestone reads on every scope resolution and
never writes.

## Why ten

* `cp_billing_scope_definition` — how accounts enter a scope, at one version, immutable;
* `cp_billing_scope_snapshot` — an immutable resolved membership;
* `cp_billing_scope_member` — **one account per row**, so a hundred-thousand-account scope
  paginates instead of living inside one JSON blob;
* `cp_billing_consolidation_period` — the window, under a declared basis;
* `cp_billing_consolidation` — the run;
* `cp_billing_consolidation_source` — one pinned source per row, so the manifest paginates;
* `cp_billing_consolidation_exception` — named problems, never a generic failure;
* `cp_billing_consolidation_validation` — the current verdict on a run;
* `cp_billing_consolidation_result` — the finalized artifact, content-addressed;
* `cp_billing_consolidation_event` — the trail.

`cp_billing_scope_member` and `cp_billing_consolidation_source` are split out deliberately. §25
requires this milestone to work at 100K accounts and 1M+ facts, and a membership inside one JSON
column has to be loaded whole to show ten rows. The digests still cover every row, so identity is
unaffected — and `member_ids` is an indexed keyset read, which is what makes batched collection
resumable rather than a re-scan.

## The three rows this milestone is built on

`cp_billing_scope_snapshot` has its primary key derived from `(org, definition, version,
membership digest)`. Resolving an unchanged hierarchy twice therefore lands on the same row, so a
retry does not mint a second view of January — and a hierarchy that *did* change produces a
different id, so January's snapshot is never edited into March's.

`cp_billing_consolidation` has its primary key derived from `(org, scope snapshot, period,
corrects)`. A duplicate consolidation is inexpressible rather than merely rejected, and a
correction lands beside the original because `corrects` is inside the key.

`cp_billing_consolidation_source` has its primary key derived from `(run, account, domain,
artifact)`. Two workers collecting the same batch write one row per source — the race is a no-op,
not a double count.

## What is deliberately absent, and the one thing that is not

There is **no balance, no recomputed total, no tax figure and no currency-converted amount** in this
schema, and **no FX rate column anywhere**. `member_count`, `collected_count`, `source_count`,
`record_count` and the exception counts are counts. `completeness`, `status` and the inclusion and
exclusion reasons are labels.

The exception is narrow and declared: `cp_billing_consolidation_source.amount_minor` carries the
integer minor units an owning domain already decided for one account, and
`cp_billing_consolidation.payload` carries the per-currency aggregates. That is the consolidation —
one with no figures would consolidate nothing. What is *not* here is a monetary column on the run
itself: the moment one existed somebody would `SUM()` it across runs and publish a number beside
one the Ledger owns.

`amount_minor` is **nullable, and null is the normal case for a gap**. An unavailable source stores
no figure at all, which is why no query can accidentally read a missing source as nought.

And there is **no card number, CVV, bank credential, provider secret, API key or authentication
token** — not as a column and not permitted inside any payload. A test scans every model, DTO,
event and snapshot for the forbidden vocabulary.

## Immutability by construction

Scope definitions, scope snapshots, members, source bindings, results and trail entries have **no
update path at all**. Exceptions have exactly one narrow one (the acknowledgement columns — an
exception is never deleted or cleared). Validations have one (the current verdict on a run, which
the result pins at finalization). Consolidations have one: the conditional lifecycle UPDATE.

`status` is a **projection over the event history**, not the authority. The history is the trail.
"""

from __future__ import annotations

from sqlalchemy import JSON as SAJSON
from sqlalchemy import Column, Index, UniqueConstraint
from sqlmodel import Field, SQLModel


def _payload() -> object:
    return Field(default_factory=dict, sa_column=Column("payload", SAJSON))


class BillingScopeDefinitionRow(SQLModel, table=True):
    """How accounts enter a scope, at one version. **Immutable once published.**

    `definition_digest` is the load-bearing column: re-publishing a version can be proven identical
    rather than assumed, so a consolidation bound to it keeps covering the accounts it covered."""

    __tablename__ = "cp_billing_scope_definition"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    scope_code: str = Field(index=True)
    # No `index=True`: SQLModel would auto-name it `ix_cp_billing_scope_definition_version`, and
    # the composite below plus the unique constraint already cover the column. M9.22's D-2 was
    # exactly this mistake made twice.
    version: int = Field(default=1)
    mode: str = Field(default="single_account", index=True)
    title: str = Field(default="")
    anchor_account_id: str = Field(default="", index=True)
    currency_mode: str = Field(default="currency_separated", index=True)
    declared_currency: str | None = Field(default=None)
    period_basis: str = Field(default="accounting_period", index=True)
    integrity_blocks_finalization: bool = Field(default=False)
    definition_digest: str = Field(default="", index=True)
    published_at: float = Field(default=0.0, index=True)
    published_by: str = Field(default="")
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id", "scope_code", "version", name="uq_cp_billing_scope_definition_version"
        ),
        Index("ix_cp_billing_scope_definition_code", "org_id", "scope_code", "version"),
    )


class BillingScopeSnapshotRow(SQLModel, table=True):
    """**An immutable resolved membership.** No update path, anywhere.

    The primary key derives from the membership digest, so resolving an unchanged hierarchy twice
    writes one row — and a hierarchy that moved produces a different snapshot rather than editing
    the one a finalized consolidation references."""

    __tablename__ = "cp_billing_scope_snapshot"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    scope_definition_id: str = Field(index=True)
    scope_version: int = Field(default=1)
    mode: str = Field(default="single_account", index=True)
    hierarchy: str = Field(default="none", index=True)
    member_count: int = Field(default=0)  # a **count**, never money
    excluded_count: int = Field(default=0)
    currency_mode: str = Field(default="currency_separated")
    declared_currency: str | None = Field(default=None)
    membership_digest: str = Field(default="", index=True)
    snapshot_digest: str = Field(default="", index=True)
    effective_at: float = Field(default=0.0, index=True)
    resolved_by: str = Field(default="")
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        Index(
            "ix_cp_billing_scope_snapshot_definition",
            "org_id",
            "scope_definition_id",
            "effective_at",
        ),
    )


class BillingScopeMemberRow(SQLModel, table=True):
    """One account in a resolved scope. **Its own table so membership paginates** (§25).

    `account_revision` is what makes the snapshot a snapshot: the account's parent and payer edges
    as they stood at resolution, so January's row still says "A was under P" after A moves to Q."""

    __tablename__ = "cp_billing_scope_member"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    snapshot_id: str = Field(index=True)
    account_id: str = Field(index=True)
    inclusion: str = Field(default="anchor", index=True)
    account_revision: str = Field(default="")
    currency: str | None = Field(default=None)
    depth: int = Field(default=0)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint("snapshot_id", "account_id", name="uq_cp_billing_scope_member_account"),
        # The keyset index batched collection walks: ordered by account id inside a snapshot, so
        # `member_ids(after=...)` is an indexed range scan rather than an offset over a table.
        Index("ix_cp_billing_scope_member_cursor", "org_id", "snapshot_id", "account_id"),
    )


class BillingConsolidationPeriodRow(SQLModel, table=True):
    """The window a consolidation covers. **`basis` is inside the identity** — an accounting period
    is not a tax period is not a recognition period, and Billing already owns four of these."""

    __tablename__ = "cp_billing_consolidation_period"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    # No `index=True`: SQLModel auto-names it `ix_cp_billing_consolidation_period_basis`, which
    # is *exactly* what `cp_billing_consolidation.period_basis` auto-generates — two tables, one
    # index name, and `create_all` fails at wiring time. The unique constraint below covers this
    # column anyway. M9.22's D-2 was this same collision made twice; this is the third.
    basis: str = Field(default="accounting_period")
    period_key: str = Field(default="", index=True)
    window_start: float = Field(default=0.0, index=True)
    window_end: float = Field(default=0.0)
    aligned_source_id: str = Field(default="", index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id", "basis", "period_key", name="uq_cp_billing_consolidation_period_key"
        ),
    )


class BillingConsolidationRow(SQLModel, table=True):
    """One deterministic consolidation. **Exactly one update path**: the conditional lifecycle
    UPDATE keyed on `version`.

    The primary key derives from `(org, scope snapshot, period, corrects)`, so two workers opening
    the same consolidation write one row and a correction lands beside the original."""

    __tablename__ = "cp_billing_consolidation"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    scope_snapshot_id: str = Field(index=True)
    scope_definition_id: str = Field(index=True)
    scope_version: int = Field(default=1)
    period_id: str = Field(index=True)
    period_basis: str = Field(default="accounting_period", index=True)
    period_key: str = Field(default="", index=True)
    status: str = Field(default="draft", index=True)
    completeness: str = Field(default="unavailable", index=True)
    currency_mode: str = Field(default="currency_separated")
    declared_currency: str | None = Field(default=None)
    member_count: int = Field(default=0)
    collected_count: int = Field(default=0)
    collection_cursor: str = Field(default="")
    source_count: int = Field(default=0)
    unavailable_source_count: int = Field(default=0)
    exception_count: int = Field(default=0)
    blocking_exception_count: int = Field(default=0)
    consolidation_digest: str = Field(default="", index=True)
    corrects: str = Field(default="", index=True)
    superseded_by: str = Field(default="", index=True)
    correction_reason: str = Field(default="")
    opened_at: float = Field(default=0.0, index=True)
    opened_by: str = Field(default="")
    finalized_at: float | None = Field(default=None, index=True)
    finalized_by: str = Field(default="")
    version: int = Field(default=0)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        Index("ix_cp_billing_consolidation_search", "org_id", "status", "opened_at"),
        Index("ix_cp_billing_consolidation_period", "org_id", "period_key", "scope_definition_id"),
    )


class BillingConsolidationSourceRow(SQLModel, table=True):
    """One pinned source. **Its own table so the manifest paginates** (§25). Append-only.

    `amount_minor` is nullable and **null is the normal case for a gap**: an unavailable source
    stores no figure at all, so no aggregate can read it as nought."""

    __tablename__ = "cp_billing_consolidation_source"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    consolidation_id: str = Field(index=True)
    account_id: str = Field(index=True)
    domain: str = Field(index=True)
    artifact: str = Field(default="")
    read_name: str = Field(default="")
    # Composite-only: a bare `status` index would auto-name to the same string SQLModel generates
    # for every other `status` column in this schema. M9.22's D-2, avoided by construction.
    status: str = Field(default="resolved")
    completeness: str = Field(default="source_complete")
    period_key: str = Field(default="", index=True)
    period_applied: bool = Field(default=False)
    source_revision: str = Field(default="")
    source_digest: str = Field(default="")
    record_count: int = Field(default=0)
    amount_minor: int | None = Field(default=None)
    currency: str | None = Field(default=None)
    detail: str = Field(default="")
    bound_at: float = Field(default=0.0)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "consolidation_id",
            "account_id",
            "domain",
            "artifact",
            name="uq_cp_billing_consolidation_source_binding",
        ),
        Index(
            "ix_cp_billing_consolidation_source_by_status", "org_id", "consolidation_id", "status"
        ),
    )


class BillingConsolidationExceptionRow(SQLModel, table=True):
    """A named problem. **Never deleted, never cleared** — one narrow acknowledgement path."""

    __tablename__ = "cp_billing_consolidation_exception"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    consolidation_id: str = Field(index=True)
    kind: str = Field(index=True)
    severity: str = Field(default="blocking", index=True)
    state: str = Field(default="open")
    subject: str = Field(default="")
    diagnostic: str = Field(default="")
    account_id: str = Field(default="", index=True)
    domain: str | None = Field(default=None)
    period_key: str = Field(default="")
    created_at: float = Field(default=0.0, index=True)
    acknowledged_at: float | None = Field(default=None)
    acknowledged_by: str = Field(default="")
    acknowledgement_note: str = Field(default="")
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        Index(
            "ix_cp_billing_consolidation_exception_open",
            "org_id",
            "consolidation_id",
            "severity",
        ),
    )


class BillingConsolidationValidationRow(SQLModel, table=True):
    """The current verdict on a run. Pinned into the result at finalization."""

    __tablename__ = "cp_billing_consolidation_validation"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    consolidation_id: str = Field(index=True)
    passed: bool = Field(default=False, index=True)
    check_count: int = Field(default=0)
    failure_count: int = Field(default=0)
    validated_at: float = Field(default=0.0)
    validated_by: str = Field(default="")
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id", "consolidation_id", name="uq_cp_billing_consolidation_validation_run"
        ),
    )


class BillingConsolidationResultRow(SQLModel, table=True):
    """The finalized artifact, content-addressed on pinned state only. **No update, no delete.**"""

    __tablename__ = "cp_billing_consolidation_result"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    consolidation_id: str = Field(index=True)
    consolidation_digest: str = Field(default="", index=True)
    scope_snapshot_id: str = Field(index=True)
    scope_snapshot_digest: str = Field(default="")
    period_key: str = Field(default="", index=True)
    source_digest: str = Field(default="")
    completeness: str = Field(default="unavailable")
    representation: str = Field(default="")
    finalized_at: float = Field(default=0.0, index=True)
    finalized_by: str = Field(default="")
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id", "consolidation_id", name="uq_cp_billing_consolidation_result_run"
        ),
    )


class BillingConsolidationEventRow(SQLModel, table=True):
    """The trail. Append-only; there is no update and no delete.

    **The authority on what happened.** `cp_billing_consolidation.status` is a projection over
    these rows, not the other way round."""

    __tablename__ = "cp_billing_consolidation_event"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    consolidation_id: str = Field(index=True)
    kind: str = Field(index=True)
    sequence: int = Field(default=0)
    subject: str = Field(default="")
    detail: str = Field(default="")
    actor: str = Field(default="")
    occurred_at: float = Field(default=0.0, index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "consolidation_id", "sequence", name="uq_cp_billing_consolidation_event_sequence"
        ),
    )


#: The ten tables migration 0033 creates, in dependency order.
CONSOLIDATION_TABLES = (
    BillingScopeDefinitionRow,
    BillingScopeSnapshotRow,
    BillingScopeMemberRow,
    BillingConsolidationPeriodRow,
    BillingConsolidationRow,
    BillingConsolidationSourceRow,
    BillingConsolidationExceptionRow,
    BillingConsolidationValidationRow,
    BillingConsolidationResultRow,
    BillingConsolidationEventRow,
)

__all__ = [
    "CONSOLIDATION_TABLES",
    "BillingConsolidationEventRow",
    "BillingConsolidationExceptionRow",
    "BillingConsolidationPeriodRow",
    "BillingConsolidationResultRow",
    "BillingConsolidationRow",
    "BillingConsolidationSourceRow",
    "BillingConsolidationValidationRow",
    "BillingScopeDefinitionRow",
    "BillingScopeMemberRow",
    "BillingScopeSnapshotRow",
]
