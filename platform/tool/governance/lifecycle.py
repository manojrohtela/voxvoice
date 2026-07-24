"""Approval lifecycle (M6.1B-2) — built on `core.fsm`, mirrors `tool/lifecycle.py`.

One state machine: `ApprovalState` (PENDING → APPROVED/REJECTED/EXPIRED/CANCELLED).
All four outcomes fire directly from PENDING — no intermediate hops, no retry.
Illegal moves (e.g. approving an already-EXPIRED record) raise typed `FSMError`s.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from core.fsm import StateMachine, StateMachineDefinition

from .domain import ApprovalRecord, ApprovalState


class ApprovalTrigger(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    EXPIRE = "expire"
    CANCEL = "cancel"


def _approval_def() -> StateMachineDefinition[ApprovalState, ApprovalTrigger]:
    d: StateMachineDefinition[ApprovalState, ApprovalTrigger] = StateMachineDefinition()
    d.add(ApprovalState.PENDING, ApprovalTrigger.APPROVE, ApprovalState.APPROVED)
    d.add(ApprovalState.PENDING, ApprovalTrigger.REJECT, ApprovalState.REJECTED)
    d.add(ApprovalState.PENDING, ApprovalTrigger.EXPIRE, ApprovalState.EXPIRED)
    d.add(ApprovalState.PENDING, ApprovalTrigger.CANCEL, ApprovalState.CANCELLED)
    return d


APPROVAL_LIFECYCLE = _approval_def()


def advance_approval(
    record: ApprovalRecord, trigger: ApprovalTrigger, *, clock: Callable[[], float] | None = None
) -> ApprovalRecord:
    """Validate `trigger` against the record's state; return it in the new state."""
    fsm: StateMachine[ApprovalState, ApprovalTrigger] = StateMachine(
        APPROVAL_LIFECYCLE, record.state, machine_id=record.approval_id, clock=clock,
    )
    return record.with_state(fsm.fire(trigger))
