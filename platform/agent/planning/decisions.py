"""Decision recording (M6.5B-2) — the audit-trail unit every planning step
produces. `payload_hash` is a content-hash reference, never the raw payload
itself — the same `HookVerdict.MODIFY` discipline (§4.9 of the M6.5A
design), applied here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..domain import AgentDecision
from ..ids import content_hash
from .ids import decision_id_for


def record_decision(
    *, goal_id: str, kind: str, payload: Any, sequence: int,
    reasoning_decision_ref: str | None = None, clock: Callable[[], float] | None = None,
) -> AgentDecision:
    now = clock() if clock is not None else 0.0
    return AgentDecision(
        decision_id=decision_id_for(goal_id, kind, sequence), goal_id=goal_id, kind=kind,
        reasoning_decision_ref=reasoning_decision_ref,
        payload_hash=content_hash(payload), created_at=now,
    )
