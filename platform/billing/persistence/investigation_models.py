"""Financial Investigation & Audit Evidence tables (M9.21) — additive to the M9.4–M9.20 schema.

Seven new `cp_billing_*` tables in the same projection-plus-payload row shape M9.4 established. No
existing table is altered, no column is dropped, and nothing in M9.2–M9.20 is touched — including
every M9.19 and M9.20 table, which M9.21 reads and never writes.

## Why seven, and not the twelve a case tracker could have

The mandate asks for the smallest structure that preserves investigation identity, an immutable
timeline, evidence references and snapshots, questions, observations, conclusions, the audit
package, concurrency and auditability. Each table earns its place by holding something that must
survive independently:

* `cp_billing_investigation` — the aggregate, PK derived from `(org, incident)`;
* `cp_billing_investigation_event` — the timeline, totally ordered per investigation;
* `cp_billing_investigation_evidence` — the chain of custody, content-addressed;
* `cp_billing_investigation_question` — what was asked, and what became of it;
* `cp_billing_investigation_observation` — facts and hypotheses, kept apart;
* `cp_billing_investigation_conclusion` — statements, superseded but never edited;
* `cp_billing_audit_package` — immutable exports, addressed by manifest digest.

There is **no separate hypothesis table** and no separate chain-of-custody table: a hypothesis is
an observation with a kind, and the custody chain is the evidence rows linked by
`previous_evidence_id`. Either as its own table would be a second copy of a fact these already
hold, which is how the two eventually disagree.

## The two rows this milestone is built on

`cp_billing_investigation` has its primary key derived from `(org_id, incident_id)`. Two
investigators opening a case on one incident therefore compute the same id and one loses the
insert — rather than both proceeding to different conclusions about the same divergence. And two
organizations can never collide, because the org is *inside* the key rather than only in a `WHERE`
clause.

`cp_billing_investigation_evidence` has its primary key derived from the **content**. Capturing an
unchanged immutable source twice lands on one row; capturing it after the representation changed
produces a different id, so the new capture is a new link in the chain rather than an overwrite of
what was true before. There is no `UPDATE` path to this table at all.

## What is deliberately absent from every column

There is **no balance, no total, no amount and no currency** — not as a column, and not as a
projection. Not `expected_minor`, not `impact_minor`, not `outstanding`. `evidence_count`,
`open_question_count` and `timeline_length` are **counts**. Impact and severity are labels the
owning domains decided.

The one place a figure appears anywhere in this schema is inside an evidence `payload` — where it
is a verbatim copy of what the owning domain wrote, captured as evidence of what that domain said,
never as a value this milestone computes or would sum. The moment a monetary *column* existed here
somebody would aggregate it and report it beside a number the Ledger owns.

And there is **no card number, no CVV, no bank credential, no provider secret, no API key and no
authentication token** — not as a column and not permitted inside a payload. M9.16's
token-reference-only boundary holds through this layer: evidence bodies are assembled from the safe
identifiers the owning domain publishes. A test scans every model, DTO, event and package for the
forbidden vocabulary.

## Append-only by construction

Evidence, timeline entries and audit packages have **no update path at all**. Conclusions have
none either — a revision is a new row naming the one it supersedes. Questions have exactly one
narrow path (the outcome columns) and observations one (the hypothesis verdict); neither row is
ever deleted, because "this was asked and never settled" and "this was believed and then rejected"
are what an examiner asks about. Investigations have one: the conditional lifecycle UPDATE.

`status` is a **projection over the event history**, not the authority. The history is the trail.
"""

from __future__ import annotations

from sqlalchemy import JSON as SAJSON
from sqlalchemy import Column, Index, UniqueConstraint
from sqlmodel import Field, SQLModel


def _payload() -> object:
    return Field(default_factory=dict, sa_column=Column("payload", SAJSON))


class BillingInvestigationRow(SQLModel, table=True):
    """One investigation of one integrity incident. **The primary key is `(org, incident)`.**

    Note the columns that do not exist: no `impact_minor`, no `exposure`, no `currency`.
    `evidence_count`, `open_question_count` and `unavailable_evidence_count` are counts, and
    `incident_severity` is a label M9.20 decided and this row copies once for triage display."""

    __tablename__ = "cp_billing_investigation"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    incident_id: str = Field(index=True)
    status: str = Field(default="open", index=True)
    priority: str = Field(default="normal", index=True)
    incident_severity: str = Field(default="info", index=True)
    summary: str = Field(default="")
    opened_at: float = Field(default=0.0, index=True)
    opened_by: str = Field(default="", index=True)
    last_activity_at: float = Field(default=0.0, index=True)
    evidence_digest: str = Field(default="", index=True)
    evidence_count: int = Field(default=0)  # a **count**, never money
    open_question_count: int = Field(default=0)  # a **count**, never money
    unavailable_evidence_count: int = Field(default=0)  # a **count**, never money
    conclusion_id: str = Field(default="", index=True)
    closed_at: float | None = Field(default=None, index=True)
    closed_by: str = Field(default="")
    closed_reason: str = Field(default="")
    version: int = Field(default=0, index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        # One investigation per incident, said a second way. The derived id already guarantees it;
        # the constraint means a hand-written insert cannot bypass the guarantee.
        UniqueConstraint(
            "org_id", "incident_id", name="uq_cp_billing_investigation_incident"
        ),
        # The three reads an operator queue, a paginated list and a triage board all issue — every
        # one org-scoped, so a query cannot accidentally span tenants.
        Index("ix_cp_billing_investigation_queue", "org_id", "status", "priority"),
        Index("ix_cp_billing_investigation_page", "org_id", "opened_at", "id"),
        Index("ix_cp_billing_investigation_actor", "org_id", "opened_by", "status"),
    )


class BillingInvestigationEventRow(SQLModel, table=True):
    """One immutable line of the investigation timeline. **No update path, no delete path.**

    The mandate's instruction not to depend on application logs, made structural: this table is
    where "who examined it, when, and what did they record" lives, and it is append-only."""

    __tablename__ = "cp_billing_investigation_event"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    investigation_id: str = Field(index=True)
    sequence: int = Field(default=0, index=True)
    kind: str = Field(default="", index=True)
    at: float = Field(default=0.0, index=True)
    actor_id: str = Field(default="", index=True)
    subject: str = Field(default="", index=True)
    summary: str = Field(default="")
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "investigation_id",
            "sequence",
            name="uq_cp_billing_investigation_event_seq",
        ),
        Index(
            "ix_cp_billing_investigation_event_read", "org_id", "investigation_id", "sequence"
        ),
    )


class BillingInvestigationEvidenceRow(SQLModel, table=True):
    """One link in a chain of custody. **Content-addressed, append-only, never replaced.**

    `mode` is the milestone's central distinction and it is fixed for the life of the row: there is
    no `UPDATE` path to this table, so a live reference cannot become a snapshot or the reverse.

    `content_digest` is recomputed from `payload` by anyone who wants to check it — which is what
    makes a stored representation *evidence* rather than a copy.

    `previous_evidence_id` is the chain. A source becoming unavailable, redacted or superseded
    appends a new row pointing at the one it follows; the earlier row keeps its identity, its
    digest, its author and its timestamp."""

    __tablename__ = "cp_billing_investigation_evidence"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    investigation_id: str = Field(index=True)
    domain: str = Field(default="", index=True)
    source_type: str = Field(default="", index=True)
    source_id: str = Field(default="", index=True)
    revision: str = Field(default="")
    locator: str = Field(default="", index=True)
    source_key: str = Field(default="", index=True)  # locator without the revision: the chain key
    mode: str = Field(default="evidence_snapshot", index=True)
    availability: str = Field(default="available", index=True)
    content_digest: str = Field(default="", index=True)
    captured_at: float = Field(default=0.0, index=True)
    captured_by: str = Field(default="", index=True)
    previous_evidence_id: str = Field(default="", index=True)
    unavailable_reason: str = Field(default="")
    redaction_reason: str = Field(default="")
    sequence: int = Field(default=0, index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        Index(
            "ix_cp_billing_investigation_evidence_chain",
            "org_id",
            "investigation_id",
            "source_key",
            "sequence",
        ),
        Index(
            "ix_cp_billing_investigation_evidence_page",
            "org_id",
            "investigation_id",
            "sequence",
        ),
    )


class BillingInvestigationQuestionRow(SQLModel, table=True):
    """One question, and what became of it. `UNANSWERABLE` is an outcome, not a failure."""

    __tablename__ = "cp_billing_investigation_question"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    investigation_id: str = Field(index=True)
    kind: str = Field(default="", index=True)
    text: str = Field(default="")
    status: str = Field(default="open", index=True)
    required: bool = Field(default=False, index=True)
    answer: str = Field(default="")
    asked_at: float = Field(default=0.0, index=True)
    asked_by: str = Field(default="")
    answered_at: float | None = Field(default=None)
    answered_by: str = Field(default="")
    version: int = Field(default=0)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        Index(
            "ix_cp_billing_investigation_question_open",
            "org_id",
            "investigation_id",
            "status",
            "required",
        ),
    )


class BillingInvestigationObservationRow(SQLModel, table=True):
    """One observed fact or hypothesis. **`kind` is inside the derived id**, so the same wording
    recorded as a fact and as a hypothesis occupies two rows and the trail shows both."""

    __tablename__ = "cp_billing_investigation_observation"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    investigation_id: str = Field(index=True)
    kind: str = Field(default="", index=True)
    statement: str = Field(default="")
    statement_digest: str = Field(default="", index=True)
    hypothesis_status: str = Field(default="not_applicable", index=True)
    recorded_at: float = Field(default=0.0, index=True)
    recorded_by: str = Field(default="", index=True)
    settled_at: float | None = Field(default=None)
    settled_by: str = Field(default="")
    settled_reason: str = Field(default="")
    sequence: int = Field(default=0, index=True)
    version: int = Field(default=0)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        Index(
            "ix_cp_billing_investigation_observation_read",
            "org_id",
            "investigation_id",
            "sequence",
        ),
        Index(
            "ix_cp_billing_investigation_observation_open",
            "org_id",
            "investigation_id",
            "kind",
            "hypothesis_status",
        ),
    )


class BillingInvestigationConclusionRow(SQLModel, table=True):
    """One conclusion. **Append-only** — a revision is a new row naming what it supersedes.

    `incident_root_cause` is what M9.20 said when this was written; `evidenced_root_cause` is what
    the evidence indicates when the two disagree. Both are recorded and **neither is written back
    to the incident** — reclassification belongs to the owning M9.20 workflow."""

    __tablename__ = "cp_billing_investigation_conclusion"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    investigation_id: str = Field(index=True)
    incident_id: str = Field(index=True)
    classification: str = Field(default="", index=True)
    statement: str = Field(default="")
    statement_digest: str = Field(default="", index=True)
    incident_root_cause: str = Field(default="unknown", index=True)
    evidenced_root_cause: str | None = Field(default=None, index=True)
    supersedes: str = Field(default="", index=True)
    recorded_at: float = Field(default=0.0, index=True)
    recorded_by: str = Field(default="", index=True)
    version: int = Field(default=0, index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        Index(
            "ix_cp_billing_investigation_conclusion_read",
            "org_id",
            "investigation_id",
            "version",
        ),
    )


class BillingAuditPackageRow(SQLModel, table=True):
    """One immutable audit export. **Primary key derived from the manifest digest.**

    Two consequences, and they are the mandate's export guarantees stated as a schema:

    * exporting an unchanged investigation twice lands on this row and writes nothing;
    * any change to the investigation produces a different manifest, so a different digest, so a
      **new row beside this one** — never a rewrite of it.

    There is no update path to this table. `completeness` is decided on evidence availability, and
    `payload` carries the gaps by name so an incomplete package says what is missing rather than
    presenting as thorough."""

    __tablename__ = "cp_billing_audit_package"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    investigation_id: str = Field(index=True)
    incident_id: str = Field(index=True)
    manifest_digest: str = Field(default="", index=True)
    completeness: str = Field(default="incomplete", index=True)
    evidence_count: int = Field(default=0)  # counts, never money
    observation_count: int = Field(default=0)
    question_count: int = Field(default=0)
    conclusion_count: int = Field(default=0)
    timeline_length: int = Field(default=0)
    gap_count: int = Field(default=0, index=True)
    created_at: float = Field(default=0.0, index=True)
    created_by: str = Field(default="", index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "investigation_id",
            "manifest_digest",
            name="uq_cp_billing_audit_package_manifest",
        ),
        Index(
            "ix_cp_billing_audit_package_latest", "org_id", "investigation_id", "created_at"
        ),
    )


INVESTIGATION_TABLES: tuple[type[SQLModel], ...] = (
    BillingInvestigationRow,
    BillingInvestigationEventRow,
    BillingInvestigationEvidenceRow,
    BillingInvestigationQuestionRow,
    BillingInvestigationObservationRow,
    BillingInvestigationConclusionRow,
    BillingAuditPackageRow,
)
