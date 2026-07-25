"""Deterministic, content-hash execution identities (M6.5B-3).

Reuses `agent.ids.content_hash` (same package, not a sibling runtime).
"""

from __future__ import annotations

from ..ids import content_hash


def execution_id_for(goal_id: str, started_at: float) -> str:
    return "ae_" + content_hash(goal_id, started_at, "execution")


def trace_id_for(execution_id: str) -> str:
    return "at_" + content_hash(execution_id, "trace")


def replay_id_for(goal_id: str, result_hash: str) -> str:
    return "ay_" + content_hash(goal_id, result_hash, "replay")
