"""Deployment lifecycle FSM (Epic 8, M8.16) — ADR-0022.

The deterministic, table-driven state machine (ADR-0022 §Deployment Lifecycle). Illegal
transitions raise; identical plan + adapter outcomes drive identical transitions (§A2.4). This
module is pure policy — no I/O, no infrastructure, no clock — so the transition rules are
testable in isolation and can never depend on the environment.

    REQUESTED → VALIDATING → PENDING_APPROVAL → QUEUED → EXECUTING ⇄ VERIFYING → COMPLETED
    failure paths: FAILED, ROLLING_BACK → ROLLED_BACK, CANCELLED
"""

from __future__ import annotations

from .domain import DeploymentExecutionState as S
from .errors import IllegalTransitionError

# The one legal-transition table (ADR-0022). Every edge is deliberate.
LEGAL_TRANSITIONS: dict[S, frozenset[S]] = {
    S.REQUESTED: frozenset({S.VALIDATING, S.CANCELLED}),
    S.VALIDATING: frozenset({S.PENDING_APPROVAL, S.QUEUED, S.FAILED, S.CANCELLED}),
    S.PENDING_APPROVAL: frozenset({S.QUEUED, S.FAILED, S.CANCELLED}),
    S.QUEUED: frozenset({S.EXECUTING, S.CANCELLED}),
    S.EXECUTING: frozenset({S.VERIFYING, S.FAILED, S.ROLLING_BACK, S.CANCELLED}),
    S.VERIFYING: frozenset({S.COMPLETED, S.EXECUTING, S.ROLLING_BACK, S.FAILED}),
    S.ROLLING_BACK: frozenset({S.ROLLED_BACK, S.FAILED}),
    # terminal states — no outgoing edges except a fresh retry (QUEUED) from FAILED
    S.FAILED: frozenset({S.QUEUED}),
    S.COMPLETED: frozenset({S.ROLLING_BACK}),  # a completed deploy can still be rolled back
    S.ROLLED_BACK: frozenset(),
    S.CANCELLED: frozenset(),
}

TERMINAL_STATES: frozenset[S] = frozenset(
    {S.COMPLETED, S.FAILED, S.ROLLED_BACK, S.CANCELLED}
)


def can_transition(frm: S, to: S) -> bool:
    return to in LEGAL_TRANSITIONS.get(frm, frozenset())


def assert_transition(frm: S, to: S) -> None:
    """Raise `IllegalTransitionError` if `frm → to` is not in the FSM table."""
    if not can_transition(frm, to):
        raise IllegalTransitionError(
            f"illegal deployment transition {frm.value!r} → {to.value!r}"
        )
