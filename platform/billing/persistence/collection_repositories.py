"""Collections repositories (M9.17) — additive to the M9.4 persistence layer.

Six SQL implementations of the M9.17 Foundation contracts, reusing the M9.4 machinery unchanged:
the same `_Repo` session handle, the same codec, the same projection-plus-payload mapping, the same
"never commit, never roll back" rule, and the same org-scoped-by-construction reads (§A11.2).

## The three race-critical writes

`SqlCollectionCaseRepository.open` is an insert on a **derived primary key**, defended the three
ways every idempotent write since M9.11 has been: a derived id, a read-before-write, and a
`SAVEPOINT` whose `IntegrityError` becomes a read of the winner rather than a destroyed
transaction. Two workers opening a recovery for the same invoice produce one case.

`SqlCollectionCaseRepository.advance` is the **compare-and-set**, and it is the reason a stale
worker cannot undo a recovery. It issues `UPDATE ... WHERE id = ? AND version = ?` and raises
`CaseStale` when that matches nothing. This is deliberately *not* a read-then-write: between a read
and a write there is a window, and the window is the incident. The caller's correct response is to
re-read and re-decide, never to retry the same write — that would be the stale decision again.

`SqlCollectionActionRepository.claim` is the same shape one level down, on `state`. Two workers
that both see a due action both try `planned → claimed`; the database picks one, and the loser is
told so before it does anything external.

## What no repository here does

None decides a state, selects a stage, classifies a payment, chooses an action, or computes a
figure. And none of them writes to a payment, invoice, ledger, subscription, or account table — an
architecture test asserts that by scanning the write targets of the whole Application, because a
convention is what the next caller does not know about.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from billing.foundation import (
    ActionState,
    CaseStale,
    CollectionAction,
    CollectionActionRepository,
    CollectionActivity,
    CollectionActivityRepository,
    CollectionAttempt,
    CollectionAttemptRepository,
    CollectionCase,
    CollectionCaseRepository,
    CollectionDecision,
    CollectionDecisionRepository,
    CollectionPolicy,
    CollectionPolicyRepository,
)

from . import collection_models as rows
from .codec import encode
from .mapping import from_row, update_row
from .repositories import _Repo


def _policy_projection(p: CollectionPolicy) -> dict:
    return {
        "id": p.id,
        "org_id": p.org_id,
        "code": p.code,
        "version": p.version,
        "obligation_kind": p.obligation_kind.value,
        "stage_count": len(p.stages),
        "max_cycles": p.max_cycles,
        "is_active": p.is_active,
        "declared_at": p.declared_at,
        "declared_by": p.declared_by,
    }


def _case_projection(c: CollectionCase) -> dict:
    return {
        "id": c.id,
        "org_id": c.org_id,
        "account_id": c.account_id,
        "obligation_kind": c.obligation_kind.value,
        "obligation_id": c.obligation_id,
        "state": c.state.value,
        "signal": c.signal.value,
        "policy_id": c.policy_id,
        "policy_code": c.policy_code,
        "policy_version": c.policy_version,
        "cycle": c.cycle,
        "stage_index": c.stage_index,
        "actions_planned": c.actions_planned,
        "version": c.version,
        "payment_intent_id": c.payment_intent_id,
        "next_action_at": c.next_action_at,
        "opened_at": c.opened_at,
        "closed_at": c.closed_at,
    }


def _action_projection(a: CollectionAction) -> dict:
    return {
        "id": a.id,
        "org_id": a.org_id,
        "case_id": a.case_id,
        "cycle": a.cycle,
        "sequence": a.sequence,
        "kind": a.kind.value,
        "state": a.state.value,
        "stage_index": a.stage_index,
        "decision_id": a.decision_id,
        "policy_id": a.policy_id,
        "policy_version": a.policy_version,
        "due_at": a.due_at,
        "planned_at": a.planned_at,
        "completed_at": a.completed_at,
        "attempts_made": a.attempts_made,
    }


def _attempt_projection(a: CollectionAttempt) -> dict:
    return {
        "id": a.id,
        "org_id": a.org_id,
        "case_id": a.case_id,
        "action_id": a.action_id,
        "sequence": a.sequence,
        "kind": a.kind.value,
        "outcome": a.outcome.value,
        "channel": a.channel,
        "executed_at": a.executed_at,
    }


def _decision_projection(d: CollectionDecision) -> dict:
    return {
        "id": d.id,
        "org_id": d.org_id,
        "case_id": d.case_id,
        "cycle": d.cycle,
        "stage_index": d.stage_index,
        "signal": d.signal.value,
        "failure_class": d.failure_class.value,
        "selected": d.selected.value if d.selected is not None else "",
        "action_id": d.action_id,
        "policy_id": d.policy_id,
        "policy_version": d.policy_version,
        "decided_at": d.decided_at,
    }


def _activity_projection(a: CollectionActivity) -> dict:
    return {
        "id": a.id,
        "org_id": a.org_id,
        "case_id": a.case_id,
        "at": a.at,
        "kind": a.kind,
        "actor_id": a.actor_id,
        "action_id": a.action_id,
    }


class SqlCollectionPolicyRepository(_Repo, CollectionPolicyRepository):
    """Immutable strategy versions. There is no update method, and that is the contract."""

    def add(self, policy: CollectionPolicy) -> tuple[CollectionPolicy, bool]:
        existing = self._session.get(rows.BillingCollectionPolicyRow, policy.id)
        if existing is not None:
            return from_row(CollectionPolicy, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingCollectionPolicyRow(
                        payload=encode(policy), **_policy_projection(policy)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingCollectionPolicyRow, policy.id)
            if winner is None:  # pragma: no cover — the primary key guarantees a row
                raise
            return from_row(CollectionPolicy, winner), False
        return policy, True

    def get(self, org_id: str, policy_id: str) -> CollectionPolicy | None:
        row = self._one(
            select(rows.BillingCollectionPolicyRow).where(
                rows.BillingCollectionPolicyRow.org_id == org_id,
                rows.BillingCollectionPolicyRow.id == policy_id,
            )
        )
        return from_row(CollectionPolicy, row) if row else None

    def latest(self, org_id: str, code: str) -> CollectionPolicy | None:
        row = self._one(
            select(rows.BillingCollectionPolicyRow)
            .where(
                rows.BillingCollectionPolicyRow.org_id == org_id,
                rows.BillingCollectionPolicyRow.code == code,
                rows.BillingCollectionPolicyRow.is_active == True,  # noqa: E712
            )
            .order_by(rows.BillingCollectionPolicyRow.version.desc())
        )
        return from_row(CollectionPolicy, row) if row else None

    def versions(self, org_id: str, code: str) -> tuple[CollectionPolicy, ...]:
        found = self._all(
            select(rows.BillingCollectionPolicyRow)
            .where(
                rows.BillingCollectionPolicyRow.org_id == org_id,
                rows.BillingCollectionPolicyRow.code == code,
            )
            .order_by(rows.BillingCollectionPolicyRow.version)
        )
        return tuple(from_row(CollectionPolicy, r) for r in found)

    def all(self, org_id: str, include_inactive: bool = False) -> tuple[CollectionPolicy, ...]:
        statement = select(rows.BillingCollectionPolicyRow).where(
            rows.BillingCollectionPolicyRow.org_id == org_id
        )
        if not include_inactive:
            statement = statement.where(
                rows.BillingCollectionPolicyRow.is_active == True  # noqa: E712
            )
        found = self._all(
            statement.order_by(
                rows.BillingCollectionPolicyRow.code, rows.BillingCollectionPolicyRow.version
            )
        )
        return tuple(from_row(CollectionPolicy, r) for r in found)

    def deactivate(self, policy: CollectionPolicy) -> CollectionPolicy:
        """Withdraw a version from future binding. The row stays; only the flag moves, because a
        case already bound to it must keep replaying against it."""
        row = self._session.get(rows.BillingCollectionPolicyRow, policy.id)
        if row is None:
            return policy
        update_row(row, policy, _policy_projection(policy))
        self._session.flush()
        return policy


class SqlCollectionCaseRepository(_Repo, CollectionCaseRepository):
    """**The two arbitrators.** `open` decides who creates; `advance` decides who changes."""

    def open(self, case: CollectionCase) -> tuple[CollectionCase, bool]:
        existing = self._session.get(rows.BillingCollectionCaseRow, case.id)
        if existing is not None:
            return from_row(CollectionCase, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingCollectionCaseRow(payload=encode(case), **_case_projection(case))
                )
                self._session.flush()
        except IntegrityError:
            # Another worker opened the recovery for this obligation between the read and the
            # insert. There is one debt, so there is one case; this caller reads the winner's.
            winner = self._session.get(rows.BillingCollectionCaseRow, case.id)
            if winner is None:  # pragma: no cover — the primary key guarantees a row
                raise
            return from_row(CollectionCase, winner), False
        return case, True

    def advance(self, case: CollectionCase, *, expected_version: int) -> CollectionCase:
        """Compare-and-set on `version`. **This is the stale-worker defence.**

        Expressed as a filtered UPDATE rather than a read followed by a write, because the read
        would be exactly the stale read the check exists to catch. A zero row count is not a
        retryable error — the caller must re-read and re-decide, since whatever it was about to do
        was decided against a world that no longer exists."""
        result = self._session.execute(
            rows.BillingCollectionCaseRow.__table__.update()
            .where(
                rows.BillingCollectionCaseRow.__table__.c.id == case.id,
                rows.BillingCollectionCaseRow.__table__.c.org_id == case.org_id,
                rows.BillingCollectionCaseRow.__table__.c.version == expected_version,
            )
            .values(payload=encode(case), **_case_projection(case))
        )
        if result.rowcount != 1:
            stored = self._session.get(rows.BillingCollectionCaseRow, case.id)
            current = stored.version if stored is not None else "missing"
            raise CaseStale(
                f"collection case {case.id!r} moved while this worker was deciding "
                f"(expected version {expected_version}, found {current}); re-read and re-decide"
            )
        self._session.expire_all()
        return case

    def get(self, org_id: str, case_id: str) -> CollectionCase | None:
        row = self._one(
            select(rows.BillingCollectionCaseRow).where(
                rows.BillingCollectionCaseRow.org_id == org_id,
                rows.BillingCollectionCaseRow.id == case_id,
            )
        )
        return from_row(CollectionCase, row) if row else None

    def for_obligation(
        self, org_id: str, obligation_kind: str, obligation_id: str
    ) -> CollectionCase | None:
        row = self._one(
            select(rows.BillingCollectionCaseRow).where(
                rows.BillingCollectionCaseRow.org_id == org_id,
                rows.BillingCollectionCaseRow.obligation_kind == obligation_kind,
                rows.BillingCollectionCaseRow.obligation_id == obligation_id,
            )
        )
        return from_row(CollectionCase, row) if row else None

    def for_account(
        self, org_id: str, account_id: str, limit: int = 200
    ) -> tuple[CollectionCase, ...]:
        found = self._all(
            select(rows.BillingCollectionCaseRow)
            .where(
                rows.BillingCollectionCaseRow.org_id == org_id,
                rows.BillingCollectionCaseRow.account_id == account_id,
            )
            .order_by(rows.BillingCollectionCaseRow.opened_at.desc())
            .limit(limit)
        )
        return tuple(from_row(CollectionCase, r) for r in found)

    def by_state(
        self, org_id: str, states: tuple[str, ...], limit: int = 200
    ) -> tuple[CollectionCase, ...]:
        statement = select(rows.BillingCollectionCaseRow).where(
            rows.BillingCollectionCaseRow.org_id == org_id
        )
        if states:
            statement = statement.where(rows.BillingCollectionCaseRow.state.in_(states))
        found = self._all(
            statement.order_by(rows.BillingCollectionCaseRow.opened_at.desc()).limit(limit)
        )
        return tuple(from_row(CollectionCase, r) for r in found)

    def due(self, org_id: str, at: float, limit: int = 200) -> tuple[CollectionCase, ...]:
        """Cases whose next action has matured. Hits `ix_cp_billing_collection_case_due`.

        A query, not a scheduler. Whoever calls it on a timer lives outside this package."""
        found = self._all(
            select(rows.BillingCollectionCaseRow)
            .where(
                rows.BillingCollectionCaseRow.org_id == org_id,
                rows.BillingCollectionCaseRow.next_action_at != None,  # noqa: E711
                rows.BillingCollectionCaseRow.next_action_at <= at,
                rows.BillingCollectionCaseRow.closed_at == None,  # noqa: E711
            )
            .order_by(rows.BillingCollectionCaseRow.next_action_at)
            .limit(limit)
        )
        return tuple(from_row(CollectionCase, r) for r in found)

    def all(self, org_id: str, limit: int = 500) -> tuple[CollectionCase, ...]:
        found = self._all(
            select(rows.BillingCollectionCaseRow)
            .where(rows.BillingCollectionCaseRow.org_id == org_id)
            .order_by(rows.BillingCollectionCaseRow.opened_at.desc())
            .limit(limit)
        )
        return tuple(from_row(CollectionCase, r) for r in found)


class SqlCollectionActionRepository(_Repo, CollectionActionRepository):
    """Planned actions, claimed by exactly one worker."""

    def plan(self, action: CollectionAction) -> tuple[CollectionAction, bool]:
        existing = self._session.get(rows.BillingCollectionActionRow, action.id)
        if existing is not None:
            return from_row(CollectionAction, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingCollectionActionRow(
                        payload=encode(action), **_action_projection(action)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingCollectionActionRow, action.id)
            if winner is None:  # pragma: no cover — the primary key guarantees a row
                raise
            return from_row(CollectionAction, winner), False
        return action, True

    def claim(self, action: CollectionAction, *, expected_state: str) -> CollectionAction:
        """Conditional `planned → claimed`. The loser learns before it does anything external.

        The same shape as `SqlCollectionCaseRepository.advance` and for the same reason: an action
        is the unit of external effect, so exactly one worker must hold it."""
        result = self._session.execute(
            rows.BillingCollectionActionRow.__table__.update()
            .where(
                rows.BillingCollectionActionRow.__table__.c.id == action.id,
                rows.BillingCollectionActionRow.__table__.c.org_id == action.org_id,
                rows.BillingCollectionActionRow.__table__.c.state == expected_state,
            )
            .values(payload=encode(action), **_action_projection(action))
        )
        if result.rowcount != 1:
            stored = self._session.get(rows.BillingCollectionActionRow, action.id)
            current = stored.state if stored is not None else "missing"
            raise CaseStale(
                f"collection action {action.id!r} is no longer {expected_state!r} "
                f"(found {current!r}); another worker holds it"
            )
        self._session.expire_all()
        return action

    def settle(self, action: CollectionAction) -> CollectionAction:
        row = self._session.get(rows.BillingCollectionActionRow, action.id)
        if row is None:
            return self.plan(action)[0]
        update_row(row, action, _action_projection(action))
        self._session.flush()
        return action

    def get(self, org_id: str, action_id: str) -> CollectionAction | None:
        row = self._one(
            select(rows.BillingCollectionActionRow).where(
                rows.BillingCollectionActionRow.org_id == org_id,
                rows.BillingCollectionActionRow.id == action_id,
            )
        )
        return from_row(CollectionAction, row) if row else None

    def for_case(self, org_id: str, case_id: str) -> tuple[CollectionAction, ...]:
        found = self._all(
            select(rows.BillingCollectionActionRow)
            .where(
                rows.BillingCollectionActionRow.org_id == org_id,
                rows.BillingCollectionActionRow.case_id == case_id,
            )
            .order_by(
                rows.BillingCollectionActionRow.cycle, rows.BillingCollectionActionRow.sequence
            )
        )
        return tuple(from_row(CollectionAction, r) for r in found)

    def open_for_case(self, org_id: str, case_id: str) -> tuple[CollectionAction, ...]:
        found = self._all(
            select(rows.BillingCollectionActionRow)
            .where(
                rows.BillingCollectionActionRow.org_id == org_id,
                rows.BillingCollectionActionRow.case_id == case_id,
                rows.BillingCollectionActionRow.state.in_(
                    (ActionState.PLANNED.value, ActionState.CLAIMED.value)
                ),
            )
            .order_by(rows.BillingCollectionActionRow.due_at)
        )
        return tuple(from_row(CollectionAction, r) for r in found)

    def due(self, org_id: str, at: float, limit: int = 200) -> tuple[CollectionAction, ...]:
        found = self._all(
            select(rows.BillingCollectionActionRow)
            .where(
                rows.BillingCollectionActionRow.org_id == org_id,
                rows.BillingCollectionActionRow.state == ActionState.PLANNED.value,
                rows.BillingCollectionActionRow.due_at <= at,
            )
            .order_by(rows.BillingCollectionActionRow.due_at)
            .limit(limit)
        )
        return tuple(from_row(CollectionAction, r) for r in found)


class SqlCollectionAttemptRepository(_Repo, CollectionAttemptRepository):
    """Append-only evidence. No update method exists, by design."""

    def append(self, attempt: CollectionAttempt) -> tuple[CollectionAttempt, bool]:
        existing = self._session.get(rows.BillingCollectionAttemptRow, attempt.id)
        if existing is not None:
            return from_row(CollectionAttempt, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingCollectionAttemptRow(
                        payload=encode(attempt), **_attempt_projection(attempt)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingCollectionAttemptRow, attempt.id)
            if winner is None:  # pragma: no cover — the primary key guarantees a row
                raise
            return from_row(CollectionAttempt, winner), False
        return attempt, True

    def for_action(self, org_id: str, action_id: str) -> tuple[CollectionAttempt, ...]:
        found = self._all(
            select(rows.BillingCollectionAttemptRow)
            .where(
                rows.BillingCollectionAttemptRow.org_id == org_id,
                rows.BillingCollectionAttemptRow.action_id == action_id,
            )
            .order_by(rows.BillingCollectionAttemptRow.sequence)
        )
        return tuple(from_row(CollectionAttempt, r) for r in found)

    def for_case(self, org_id: str, case_id: str) -> tuple[CollectionAttempt, ...]:
        found = self._all(
            select(rows.BillingCollectionAttemptRow)
            .where(
                rows.BillingCollectionAttemptRow.org_id == org_id,
                rows.BillingCollectionAttemptRow.case_id == case_id,
            )
            .order_by(rows.BillingCollectionAttemptRow.executed_at)
        )
        return tuple(from_row(CollectionAttempt, r) for r in found)


class SqlCollectionDecisionRepository(_Repo, CollectionDecisionRepository):
    """The decision trail. Append-only and idempotent on the derived id, so a scheduler that
    re-evaluates an unchanged case records nothing new."""

    def append(self, decision: CollectionDecision) -> tuple[CollectionDecision, bool]:
        existing = self._session.get(rows.BillingCollectionDecisionRow, decision.id)
        if existing is not None:
            return from_row(CollectionDecision, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingCollectionDecisionRow(
                        payload=encode(decision), **_decision_projection(decision)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingCollectionDecisionRow, decision.id)
            if winner is None:  # pragma: no cover — the primary key guarantees a row
                raise
            return from_row(CollectionDecision, winner), False
        return decision, True

    def get(self, org_id: str, decision_id: str) -> CollectionDecision | None:
        row = self._one(
            select(rows.BillingCollectionDecisionRow).where(
                rows.BillingCollectionDecisionRow.org_id == org_id,
                rows.BillingCollectionDecisionRow.id == decision_id,
            )
        )
        return from_row(CollectionDecision, row) if row else None

    def for_case(self, org_id: str, case_id: str) -> tuple[CollectionDecision, ...]:
        found = self._all(
            select(rows.BillingCollectionDecisionRow)
            .where(
                rows.BillingCollectionDecisionRow.org_id == org_id,
                rows.BillingCollectionDecisionRow.case_id == case_id,
            )
            .order_by(rows.BillingCollectionDecisionRow.decided_at)
        )
        return tuple(from_row(CollectionDecision, r) for r in found)

    def latest(self, org_id: str, case_id: str) -> CollectionDecision | None:
        row = self._one(
            select(rows.BillingCollectionDecisionRow)
            .where(
                rows.BillingCollectionDecisionRow.org_id == org_id,
                rows.BillingCollectionDecisionRow.case_id == case_id,
            )
            .order_by(rows.BillingCollectionDecisionRow.decided_at.desc())
        )
        return from_row(CollectionDecision, row) if row else None


class SqlCollectionActivityRepository(_Repo, CollectionActivityRepository):
    """Append-only audit trail."""

    def append(self, activity: CollectionActivity) -> CollectionActivity:
        self._add(
            rows.BillingCollectionActivityRow(
                payload=encode(activity), **_activity_projection(activity)
            )
        )
        return activity

    def for_case(
        self, org_id: str, case_id: str, limit: int = 200
    ) -> tuple[CollectionActivity, ...]:
        found = self._all(
            select(rows.BillingCollectionActivityRow)
            .where(
                rows.BillingCollectionActivityRow.org_id == org_id,
                rows.BillingCollectionActivityRow.case_id == case_id,
            )
            .order_by(rows.BillingCollectionActivityRow.at)
            .limit(limit)
        )
        return tuple(from_row(CollectionActivity, r) for r in found)


__all__ = [
    "SqlCollectionActionRepository",
    "SqlCollectionActivityRepository",
    "SqlCollectionAttemptRepository",
    "SqlCollectionCaseRepository",
    "SqlCollectionDecisionRepository",
    "SqlCollectionPolicyRepository",
]
