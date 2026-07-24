"""Deterministic, content-hash planning identities (M6.5B-2).

Reuses `agent.ids.content_hash` (same package, not a sibling runtime).
"""

from __future__ import annotations

from ..ids import content_hash


def plan_id_for(goal_id: str, revision: int, created_at: float) -> str:
    return "ap_" + content_hash(goal_id, revision, created_at, "plan")


def step_id_for(goal_id: str, index: int) -> str:
    return "as_" + content_hash(goal_id, index, "step")


def decision_id_for(goal_id: str, kind: str, sequence: int) -> str:
    return "ad_" + content_hash(goal_id, kind, sequence, "decision")
