"""Dunning & Collections tables (M9.17) — additive to the M9.4–M9.16 schema.

Six new `cp_billing_*` tables in the same projection-plus-payload row shape M9.4 established. No
existing table is altered, no column is dropped, and nothing in M9.2–M9.16 is touched — including
`cp_billing_dunning`, which keeps its rows, its unique constraint, and its meaning. The richer
aggregates land beside it, exactly as M9.13–M9.16's did beside theirs.

## The two rows this milestone rests on

`cp_billing_collection_case` has its **primary key derived from the obligation**, so every way a
second recovery could be opened for one debt collapses onto one row: a duplicated payment-failure
event, a scheduler that ran twice, an invoice re-read, a restart mid-command, two workers sweeping
the same overdue document. One of them wins the insert and the rest read what it wrote.

The same row carries `version`, and every transition is a conditional UPDATE against it. That is
the stale-worker defence: worker A reads a case as scheduled, worker B records a payment success
and closes it as recovered, worker A comes back to schedule another retry — and its UPDATE matches
zero rows because the version moved. Without the column that write would succeed and a customer who
had paid would be chased.

`cp_billing_collection_action` is the second arbitrator. Its id is derived from
`(case, cycle, sequence)`, so two workers that both conclude "the next action is number 3" collide
on a primary key instead of both sending a notice, and its `state` moves `planned → claimed` under
a conditional UPDATE, so only one worker ever executes it.

## What is deliberately absent from every column

There is **no amount, no balance, no `outstanding`, no `amount_due`, and no copied invoice total**
— not as a column and not inside a payload blob, because no aggregate here has a field that could
carry one. A stored figure would be a second authority on a number the Ledger owns, and it would be
the stale one on the day it mattered. What is owed is read at display time from the Payment
position, which reads it from the document, which got it from the Ledger.

There is also no instrument data, no provider name, no recipient address, and no message body. A
collection notice is decided here and rendered elsewhere; storing a recipient would make this the
owner of a customer contact record it does not own.

## Append-only by construction

Attempts, decisions and the audit trail have no update path at all. Actions have exactly two
narrow ones (claim and settle) and decisions have none — re-evaluating an unchanged case derives
the same decision id and writes nothing.
"""

from __future__ import annotations

from sqlalchemy import JSON as SAJSON
from sqlalchemy import Column, Index, UniqueConstraint
from sqlmodel import Field, SQLModel


def _payload() -> object:
    return Field(default_factory=dict, sa_column=Column("payload", SAJSON))


class BillingCollectionPolicyRow(SQLModel, table=True):
    """One **immutable** strategy version.

    `(org_id, code, version)` is unique and the id is derived from it, so publishing the same
    version twice is idempotent and publishing a different body under the same version is a
    constraint violation rather than a silent rewrite of the rules a live case is being judged by.

    There is no update path. `is_active` withdraws a version from *future* binding; cases already
    bound to it keep pointing at it, because their decisions must still replay."""

    __tablename__ = "cp_billing_collection_policy"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    code: str = Field(index=True)
    version: int = Field(default=1, index=True)
    obligation_kind: str = Field(default="invoice", index=True)
    stage_count: int = Field(default=0)
    max_cycles: int = Field(default=1)
    is_active: bool = Field(default=True, index=True)
    declared_at: float = Field(default=0.0, index=True)
    declared_by: str = ""
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint("org_id", "code", "version", name="uq_cp_billing_collection_policy"),
    )


class BillingCollectionCaseRow(SQLModel, table=True):
    """The recovery of one obligation. **One row per debt, enforced by a derived primary key.**

    `version` is the optimistic-concurrency token and the reason this table is safe under two
    workers. Every state change is `UPDATE ... WHERE id = ? AND version = ?`, so a decision taken
    from a stale read fails loudly instead of overwriting a recovery.

    Note the columns that do not exist: no `amount`, no `outstanding`, no `balance`. Collections
    does not hold a figure the Ledger owns."""

    __tablename__ = "cp_billing_collection_case"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    account_id: str = Field(index=True)
    obligation_kind: str = Field(default="invoice", index=True)
    obligation_id: str = Field(default="", index=True)
    state: str = Field(default="draft", index=True)
    signal: str = Field(default="none", index=True)
    policy_id: str = Field(default="", index=True)
    policy_code: str = Field(default="", index=True)
    policy_version: int = Field(default=0)
    cycle: int = Field(default=1, index=True)
    stage_index: int = Field(default=0)
    actions_planned: int = Field(default=0)
    version: int = Field(default=0, index=True)
    payment_intent_id: str = Field(default="", index=True)
    next_action_at: float | None = Field(default=None, index=True)
    opened_at: float = Field(default=0.0, index=True)
    closed_at: float | None = Field(default=None, index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id", "obligation_kind", "obligation_id", name="uq_cp_billing_collection_case"
        ),
        # The scheduler's only read: "what is due in this org right now". Composite because the
        # org filter without the due-time filter is the whole table on a large tenant.
        Index("ix_cp_billing_collection_case_due", "org_id", "state", "next_action_at"),
        Index("ix_cp_billing_collection_case_account", "org_id", "account_id", "state"),
    )


class BillingCollectionActionRow(SQLModel, table=True):
    """One decided action, claimable by exactly one worker.

    The id is derived from `(case, cycle, sequence)` and `(org_id, case_id, cycle, sequence)` is
    unique — two constraints saying the same thing, because the derived id protects against two
    workers computing the same action and the unique constraint protects against a caller that
    supplies its own id.

    Durable **before** any external effect. A crash between planning and executing leaves a
    `planned` or `claimed` row naming exactly what was in flight."""

    __tablename__ = "cp_billing_collection_action"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    case_id: str = Field(index=True)
    cycle: int = Field(default=1)
    sequence: int = Field(default=1)
    kind: str = Field(default="notify", index=True)
    state: str = Field(default="planned", index=True)
    stage_index: int = Field(default=0)
    decision_id: str = Field(default="", index=True)
    policy_id: str = Field(default="")
    policy_version: int = Field(default=0)
    due_at: float = Field(default=0.0, index=True)
    planned_at: float = Field(default=0.0, index=True)
    completed_at: float | None = Field(default=None)
    attempts_made: int = Field(default=0)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id", "case_id", "cycle", "sequence", name="uq_cp_billing_collection_action"
        ),
        Index("ix_cp_billing_collection_action_due", "org_id", "state", "due_at"),
    )


class BillingCollectionAttemptRow(SQLModel, table=True):
    """Evidence that an action was executed. **Append-only — no update path exists.**

    `(org_id, action_id, sequence)` is unique, so two workers recording "attempt 2 of this action"
    produce one row. Separate from the action because an action tried three times and deferred
    twice is one decision with three pieces of evidence, and an integer counter loses which of the
    three actually reached the customer."""

    __tablename__ = "cp_billing_collection_attempt"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    case_id: str = Field(index=True)
    action_id: str = Field(index=True)
    sequence: int = Field(default=1)
    kind: str = Field(default="notify", index=True)
    outcome: str = Field(default="deferred", index=True)
    channel: str = Field(default="")
    executed_at: float = Field(default=0.0, index=True)
    payload: dict = _payload()  # type: ignore[assignment]

    __table_args__ = (
        UniqueConstraint(
            "org_id", "action_id", "sequence", name="uq_cp_billing_collection_attempt"
        ),
    )


class BillingCollectionDecisionRow(SQLModel, table=True):
    """Why an action was selected — **append-only, and idempotent on its derived id.**

    The id is derived from the decision's *inputs*, so a scheduler polling every minute records one
    row per real decision rather than one per sweep. Every input that fed the choice is stored
    beside it, so the decision replays exactly after the policy has been rewritten twice."""

    __tablename__ = "cp_billing_collection_decision"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    case_id: str = Field(index=True)
    cycle: int = Field(default=1)
    stage_index: int = Field(default=0)
    signal: str = Field(default="none", index=True)
    failure_class: str = Field(default="unknown", index=True)
    selected: str = Field(default="", index=True)
    action_id: str = Field(default="", index=True)
    policy_id: str = Field(default="", index=True)
    policy_version: int = Field(default=0)
    decided_at: float = Field(default=0.0, index=True)
    payload: dict = _payload()  # type: ignore[assignment]


class BillingCollectionActivityRow(SQLModel, table=True):
    """The audit trail an operator reads. Append-only."""

    __tablename__ = "cp_billing_collection_activity"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    case_id: str = Field(index=True)
    at: float = Field(default=0.0, index=True)
    kind: str = Field(default="", index=True)
    actor_id: str = Field(default="", index=True)
    action_id: str = Field(default="", index=True)
    payload: dict = _payload()  # type: ignore[assignment]


#: Declared so the migration and the "every table is built" test iterate the same list.
COLLECTION_TABLES = (
    BillingCollectionPolicyRow,
    BillingCollectionCaseRow,
    BillingCollectionActionRow,
    BillingCollectionAttemptRow,
    BillingCollectionDecisionRow,
    BillingCollectionActivityRow,
)

__all__ = [
    "COLLECTION_TABLES",
    "BillingCollectionActionRow",
    "BillingCollectionActivityRow",
    "BillingCollectionAttemptRow",
    "BillingCollectionCaseRow",
    "BillingCollectionDecisionRow",
    "BillingCollectionPolicyRow",
]
