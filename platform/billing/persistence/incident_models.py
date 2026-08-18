"""Financial Integrity Correlation tables (M9.20) — additive to the M9.4–M9.19 schema.

Six new `cp_billing_*` tables in the same projection-plus-payload row shape M9.4 established. No
existing table is altered, no column is dropped, and nothing in M9.2–M9.19 is touched — including
every M9.19 table, which M9.20 reads and never writes.

## Why six, and not the ten a correlation engine could have

The mandate asks for the smallest structure that preserves immutable history, correlation,
lifecycle, evidence, concurrency and auditability. Each table earns its place by holding something
that must survive independently:

* `cp_billing_integrity_incident` — the aggregate, PK **derived from the correlation key**;
* `cp_billing_integrity_finding_link` — which findings belong, **and the anchor that proved it**;
* `cp_billing_integrity_event` — the audit trail, totally ordered per incident;
* `cp_billing_integrity_merge` — merge decisions, so neither original is rewritten;
* `cp_billing_integrity_correction` — correlation corrections, so the previous belief survives;
* `cp_billing_integrity_repair` — what was asked of which owning domain.

There is **no separate correlation table**: the correlation key lives on the incident, the anchor
that justified each attachment lives on the link, and a `cp_billing_integrity_correlation` row would
be a third copy of a fact those two already hold — which is how the three eventually disagree. The
evidence *graph* is likewise derived on read from links and merges rather than stored, for the same
reason M9.13 derives balances and M9.18 derives deferred revenue.

## The row this milestone is built on

`cp_billing_integrity_incident` has its **primary key derived from the correlation key**, which is
itself a hash over immutable financial identifiers and an org id. Three consequences:

* two workers correlating the same finding compute the same id and one loses the insert;
* re-correlating an unchanged finding lands on the same row and writes nothing;
* **two organizations can never collide**, because the org is inside the key rather than only in a
  `WHERE` clause.

`version` carries the compare-and-set that makes a stale worker fail loudly rather than overwrite a
conclusion somebody else reached.

## What is deliberately absent from every column

There is **no balance, no total, no amount and no currency** — not as a column and not inside a
payload blob. Not `expected_minor`, not `actual_minor`, not `divergent_total`. Every figure belongs
to the domain that decided it, and this schema references facts rather than copying them: a link
carries a `finding_id`, and the amounts live on M9.19's row where M9.19 put them.

Impact is a **classification** (`impact` is a label), never a computed figure. The moment a
monetary column existed here somebody would sum it and report it beside a number the Ledger owns.

There is also no price, no rate, no tax component, no usage figure and no recognized amount.

## Append-only by construction

Events, merges and corrections have **no update path at all**. Links have exactly one narrow one
(the detach columns — the row is never deleted, because "this was once believed to belong here" is
what an auditor asks about after a correction). Repairs have one (the observation flag). Incidents
have one: the conditional lifecycle UPDATE.

`status` is a **projection over the event history**, not the authority. The history is the trail.
"""

from __future__ import annotations

from sqlalchemy import JSON as SAJSON
from sqlalchemy import Column, Index, UniqueConstraint
from sqlmodel import Field, SQLModel


def _payload() -> object:
    return Field(default_factory=dict, sa_column=Column("payload", SAJSON))


class BillingIntegrityIncidentRow(SQLModel, table=True):
    """One financial integrity incident. **The primary key is the correlation key**, so identity
    and correlation are the same fact.

    `correlation_key` and `anchor_value` are stored as their own indexed columns too, so "show me
    every incident correlated on this ledger source" is an index seek rather than a scan of a JSON
    blob.

    Note the columns that do not exist: no `expected_minor`, no `actual_minor`, no
    `divergent_total`, no `currency`. `finding_count` and `unresolved_findings` are **counts**."""

    __tablename__ = "cp_billing_integrity_incident"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    correlation_key: str = Field(default="", index=True)
    anchor_kind: str = Field(default="none", index=True)
    anchor_value: str = Field(default="", index=True)
    status: str = Field(default="open", index=True)
    severity: str = Field(default="info", index=True)
    root_cause: str = Field(default="unknown", index=True)
    impact: str = Field(default="", index=True)
    finding_count: int = Field(default=0)  # a **count**, never money
    unresolved_findings: int = Field(default=0)  # a **count**, never money
    account_id: str = Field(default="", index=True)
    evidence_digest: str = Field(default="", index=True)
    canonical_id: str = Field(default="", index=True)
    first_detected_at: float = Field(default=0.0, index=True)
    last_observed_at: float = Field(default=0.0, index=True)
    closed_at: float | None = Field(default=None, index=True)
    version: int = Field(default=0, index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id", "correlation_key", name="uq_cp_billing_integrity_incident_key"
        ),
        # The three reads an operator queue, a paginated list and a scheduler all issue — every one
        # org-scoped, so a query cannot accidentally span tenants.
        Index("ix_cp_billing_integrity_incident_queue", "org_id", "status", "severity"),
        Index("ix_cp_billing_integrity_incident_page", "org_id", "first_detected_at", "id"),
        Index("ix_cp_billing_integrity_incident_acct", "org_id", "account_id", "status"),
    )


class BillingIntegrityFindingLinkRow(SQLModel, table=True):
    """One M9.19 finding attached to one incident. **The duplicate-attachment defence.**

    Two constraints saying the same thing: the id is derived from `(incident, finding)`, and
    `(org_id, incident_id, finding_id)` is unique. The first stops the same finding delivered twice
    from attaching twice; the second stops a caller supplying its own id.

    `anchor_kind` / `anchor_value` record **why this finding belongs here**, stored on the link
    rather than only on the incident — a correlation correction changes what the incident is
    anchored on, and the original justification for each attachment must survive that.

    `detached_at` is set rather than the row deleted. There is no delete path."""

    __tablename__ = "cp_billing_integrity_finding_link"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    incident_id: str = Field(index=True)
    finding_id: str = Field(index=True)
    run_id: str = Field(default="", index=True)
    relationship: str = Field(default="", index=True)
    divergence: str = Field(default="", index=True)
    severity: str = Field(default="", index=True)
    anchor_kind: str = Field(default="", index=True)
    anchor_value: str = Field(default="", index=True)
    edge: str = Field(default="", index=True)
    subject_key: str = Field(default="", index=True)
    attached_at: float = Field(default=0.0, index=True)
    attached_by: str = Field(default="")
    detached_at: float | None = Field(default=None, index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id", "incident_id", "finding_id", name="uq_cp_billing_integrity_link"
        ),
        # "which incidents has this finding ever belonged to" — including detached ones, which is
        # the question a correlation correction leaves behind.
        Index("ix_cp_billing_integrity_link_finding", "org_id", "finding_id", "attached_at"),
    )


class BillingIntegrityEventRow(SQLModel, table=True):
    """The audit trail. **Append-only — no update path exists.**

    `(org_id, incident_id, sequence)` is unique, which gives the trail a total order per incident
    that survives a restart. The id is derived from `(incident, kind, subject, sequence)`, so a
    replayed command appends nothing twice.

    This table — not an application log — is the source of explanation. A log rotates, is sampled,
    and cannot be proved unedited."""

    __tablename__ = "cp_billing_integrity_event"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    incident_id: str = Field(index=True)
    sequence: int = Field(default=0, index=True)
    kind: str = Field(default="", index=True)
    at: float = Field(default=0.0, index=True)
    actor_id: str = Field(default="", index=True)
    finding_id: str = Field(default="", index=True)
    subject: str = Field(default="")
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id", "incident_id", "sequence", name="uq_cp_billing_integrity_event_seq"
        ),
    )


class BillingIntegrityMergeRow(SQLModel, table=True):
    """A merge decision as a new fact. **Append-only — no update path exists.**

    Neither original incident is rewritten: the merged one moves to `merged` and gains a
    `canonical_id`, keeping its severity, its findings and its history exactly as they were."""

    __tablename__ = "cp_billing_integrity_merge"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    canonical_id: str = Field(index=True)
    merged_id: str = Field(index=True)
    anchor_kind: str = Field(default="")
    decided_at: float = Field(default=0.0, index=True)
    decided_by: str = Field(default="", index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint("org_id", "merged_id", name="uq_cp_billing_integrity_merge"),
    )


class BillingIntegrityCorrectionRow(SQLModel, table=True):
    """A correlation correction as a new fact. **Append-only — no update path exists.**

    Carries both the previous and the corrected key, because an auditor's question is never "what
    is the correlation?" but "what did you believe, when, and on what grounds did you change your
    mind?"."""

    __tablename__ = "cp_billing_integrity_correction"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    finding_id: str = Field(index=True)
    previous_incident_id: str = Field(default="", index=True)
    previous_key: str = Field(default="", index=True)
    corrected_incident_id: str = Field(default="", index=True)
    corrected_key: str = Field(default="", index=True)
    corrected_at: float = Field(default=0.0, index=True)
    corrected_by: str = Field(default="", index=True)
    payload: dict = _payload()  # type: ignore[assignment]


class BillingIntegrityRepairRow(SQLModel, table=True):
    """A repair requested of an owning domain. Append-only apart from the observation flag.

    `observed` records that the owner reported back — **never** that the incident is resolved. Only
    verification says that, and it says it by re-reading the books."""

    __tablename__ = "cp_billing_integrity_repair"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    incident_id: str = Field(index=True)
    owner: str = Field(default="", index=True)
    action: str = Field(default="", index=True)
    request_key: str = Field(default="", index=True)
    finding_id: str = Field(default="", index=True)
    reference: str = Field(default="", index=True)
    observed: bool = Field(default=False, index=True)
    observed_at: float | None = Field(default=None)
    requested_at: float = Field(default=0.0, index=True)
    requested_by: str = Field(default="")
    payload: dict = _payload()  # type: ignore[assignment]


#: Declared so the migration and the "every table is built" test iterate the same list.
INCIDENT_TABLES = (
    BillingIntegrityIncidentRow,
    BillingIntegrityFindingLinkRow,
    BillingIntegrityEventRow,
    BillingIntegrityMergeRow,
    BillingIntegrityCorrectionRow,
    BillingIntegrityRepairRow,
)

__all__ = [
    "INCIDENT_TABLES",
    "BillingIntegrityCorrectionRow",
    "BillingIntegrityEventRow",
    "BillingIntegrityFindingLinkRow",
    "BillingIntegrityIncidentRow",
    "BillingIntegrityMergeRow",
    "BillingIntegrityRepairRow",
]
