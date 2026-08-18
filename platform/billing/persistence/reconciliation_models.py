"""Reconciliation & Financial Integrity tables (M9.19) — additive to the M9.4–M9.18 schema.

Five new `cp_billing_*` tables in the same projection-plus-payload row shape M9.4 established. No
existing table is altered, no column is dropped, and nothing in M9.2–M9.18 is touched — including
`cp_billing_ledger_entry`, `cp_billing_invoice_snapshot`, `cp_billing_payment_intent`,
`cp_billing_tax_assessment` and `cp_billing_recognition_entry`, every one of which keeps sole
ownership of the facts M9.19 only ever *reads*.

## The row this milestone is built on

`cp_billing_integrity_run` has its **primary key derived from the evidence digest** — a hash over
exactly the facts that were compared and nothing volatile. Three consequences fall out of that one
choice:

* two workers reconciling the same facts compute the same id and one loses the insert;
* re-running over unchanged evidence lands on the same row and writes nothing;
* **re-running after a repair has different evidence, so it is genuinely a new run** — which is what
  makes "reconciliation observes the result later" a real second observation rather than a rewrite
  of the first.

`cp_billing_integrity_finding` carries `version`, and every lifecycle transition is a conditional
UPDATE against it — the stale-worker defence, identical in shape to M9.17's collection case and
M9.18's revenue schedule. Combined with an FSM that has **no edge out of `resolved` or `closed`**,
a worker holding a stale `open` read cannot overwrite somebody else's resolution: the domain refuses
the transition, and if it somehow did not, the database refuses the write.

## What is deliberately absent from every column

There is **no balance, no total, and no monetary sum** — not as a column and not inside a payload
blob. `compared`, `matched` and `diverged` are counts. `severity` is a label. The two `amount_minor`
columns on a finding are **copies of the two figures that disagreed**, stored so the divergence
stays legible after the domains that produced it have moved on; nothing reads them to compute
anything, and no query sums them.

A "total divergent value" column would be the moment reconciliation became a second set of books:
somebody would report it, and a figure M9.19 computed would start appearing beside figures the
Ledger owns.

There is also no price, no rate, no tax component, no usage figure and no recognized amount — every
one of those belongs to a domain that already owns it.

## Append-only by construction

Runs have one narrow update path (recording the counts when the evaluation completes). Findings have
exactly one (the conditional lifecycle move). **Repair requests, resolutions and the audit trail
have no update path at all.** Historical divergence therefore stays auditable: a resolution is a new
row pointing at a finding, never an edit that makes the finding look like it never happened.
"""

from __future__ import annotations

from sqlalchemy import JSON as SAJSON
from sqlalchemy import Column, Index, UniqueConstraint
from sqlmodel import Field, SQLModel


def _payload() -> object:
    return Field(default_factory=dict, sa_column=Column("payload", SAJSON))


class BillingIntegrityRunRow(SQLModel, table=True):
    """One evaluation. **The primary key is the evidence digest**, so identity and reproducibility
    are the same fact.

    `evidence_digest` is stored as its own indexed column too, so "show me every run that looked at
    this evidence" is an index seek rather than a scan of a JSON blob.

    Note the columns that do not exist: no `total_divergent_minor`, no `expected_balance`, no
    `reconciled_balance`. `compared` / `matched` / `diverged` are counts."""

    __tablename__ = "cp_billing_integrity_run"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    relationship: str = Field(default="", index=True)
    subject_id: str = Field(default="", index=True)
    period_key: str = Field(default="", index=True)
    scope_key: str = Field(default="", index=True)
    evidence_digest: str = Field(default="", index=True)
    state: str = Field(default="evaluating", index=True)
    compared: int = Field(default=0)
    matched: int = Field(default=0)
    diverged: int = Field(default=0, index=True)
    max_severity: str = Field(default="info", index=True)
    started_at: float = Field(default=0.0, index=True)
    completed_at: float | None = Field(default=None, index=True)
    triggered_by: str = Field(default="", index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        # The two reads an operator and a scheduler issue, both org-scoped and both indexed.
        Index("ix_cp_billing_integrity_run_recent", "org_id", "relationship", "started_at"),
        Index("ix_cp_billing_integrity_run_subject", "org_id", "subject_id", "started_at"),
    )


class BillingIntegrityFindingRow(SQLModel, table=True):
    """One divergence. **The duplicate-finding defence.**

    Two constraints saying the same thing: the id is derived from
    `(run, relationship, subject, divergence)`, and `(org_id, run_id, subject_key, divergence)` is
    unique. The first stops two workers recording the same mismatch twice; the second stops a caller
    supplying its own id.

    `state` moves under a conditional UPDATE on `version`, so a stale worker cannot overwrite a
    resolution. `severity` is written once at detection and no code path changes it — a resolution
    records that a critical divergence was fixed, it never makes it retroactively a warning.

    The two `*_amount_minor` columns are **copies of the figures that disagreed**, kept so the
    finding explains itself without re-reading domains that have moved on. Nothing sums them."""

    __tablename__ = "cp_billing_integrity_finding"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    run_id: str = Field(index=True)
    relationship: str = Field(default="", index=True)
    subject_key: str = Field(default="", index=True)
    divergence: str = Field(default="", index=True)
    severity: str = Field(default="warning", index=True)
    owner: str = Field(default="operator", index=True)
    state: str = Field(default="open", index=True)
    verdict: str = Field(default="matched", index=True)
    account_id: str = Field(default="", index=True)
    source_identity: str = Field(default="", index=True)
    counterpart_identity: str = Field(default="", index=True)
    source_amount_minor: int | None = Field(default=None)  # a copy, never summed
    counterpart_amount_minor: int | None = Field(default=None)  # a copy, never summed
    currency: str = Field(default="", index=True)
    detected_at: float = Field(default=0.0, index=True)
    acknowledged_at: float | None = Field(default=None)
    resolved_at: float | None = Field(default=None, index=True)
    resolved_by: str = Field(default="")
    repair_request_id: str = Field(default="", index=True)
    resolution_id: str = Field(default="", index=True)
    version: int = Field(default=0, index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "run_id",
            "subject_key",
            "divergence",
            name="uq_cp_billing_integrity_finding",
        ),
        # The operator queue: unresolved, worst first, org-scoped.
        Index("ix_cp_billing_integrity_finding_queue", "org_id", "state", "severity"),
        # Suffixed `_queue` rather than plain `_owner`: SQLModel already generates
        # `ix_cp_billing_integrity_finding_owner` for the single-column `owner` index, and two
        # indexes cannot share a name.
        Index("ix_cp_billing_integrity_finding_owner_queue", "org_id", "owner", "state"),
        Index("ix_cp_billing_integrity_finding_account", "org_id", "account_id", "state"),
    )


class BillingIntegrityRepairRequestRow(SQLModel, table=True):
    """A request dispatched to an owning domain. **Append-only — no update path exists.**

    `dispatched_command` names what was asked for and `accepted` records whether the owner took it.
    Neither is evidence that the divergence is gone; only a later run is, and that is deliberate."""

    __tablename__ = "cp_billing_integrity_repair_request"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    finding_id: str = Field(index=True)
    owner: str = Field(default="", index=True)
    action: str = Field(default="", index=True)
    request_key: str = Field(default="", index=True)
    dispatched_command: str = Field(default="", index=True)
    accepted: bool = Field(default=False, index=True)
    requested_at: float = Field(default=0.0, index=True)
    requested_by: str = Field(default="")
    payload: dict = _payload()  # type: ignore[assignment]


class BillingIntegrityResolutionRow(SQLModel, table=True):
    """Evidence that a divergence was addressed. **Append-only — no update path exists.**

    `evidence_digest` is a digest over the *resulting* facts, so "this was fixed" is a claim backed
    by what the books say now rather than an assertion somebody typed. It is evidence that two facts
    were compared; it is never read as a financial value."""

    __tablename__ = "cp_billing_integrity_resolution"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    finding_id: str = Field(index=True)
    run_id: str = Field(default="", index=True)
    owner: str = Field(default="", index=True)
    action: str = Field(default="", index=True)
    request_key: str = Field(default="", index=True)
    repair_request_id: str = Field(default="", index=True)
    source_command_id: str = Field(default="", index=True)
    evidence_digest: str = Field(default="", index=True)
    resolved_at: float = Field(default=0.0, index=True)
    resolved_by: str = Field(default="", index=True)
    payload: dict = _payload()  # type: ignore[assignment]


class BillingIntegrityActivityRow(SQLModel, table=True):
    """The audit trail an accountant reads. Append-only."""

    __tablename__ = "cp_billing_integrity_activity"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    run_id: str = Field(index=True)
    at: float = Field(default=0.0, index=True)
    kind: str = Field(default="", index=True)
    actor_id: str = Field(default="", index=True)
    finding_id: str = Field(default="", index=True)
    payload: dict = _payload()  # type: ignore[assignment]


#: Declared so the migration and the "every table is built" test iterate the same list.
RECONCILIATION_TABLES = (
    BillingIntegrityRunRow,
    BillingIntegrityFindingRow,
    BillingIntegrityRepairRequestRow,
    BillingIntegrityResolutionRow,
    BillingIntegrityActivityRow,
)

__all__ = [
    "RECONCILIATION_TABLES",
    "BillingIntegrityActivityRow",
    "BillingIntegrityFindingRow",
    "BillingIntegrityRepairRequestRow",
    "BillingIntegrityResolutionRow",
    "BillingIntegrityRunRow",
]
