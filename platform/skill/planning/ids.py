"""Deterministic, content-hash planning identities (M6.2B-2).

Reuses `skill.ids.content_hash` (same package, not a sibling runtime).
"""

from __future__ import annotations

from ..ids import content_hash


def plan_id_for(qualified_name: str, version: str, dependency_hash: str) -> str:
    return "sp_" + content_hash(qualified_name, version, dependency_hash, "plan")


def context_id_for(plan_id: str, requested_by: str, created_at: float) -> str:
    return "sx_" + content_hash(plan_id, requested_by, created_at, "context")


def replay_id_for(plan_id: str, context_id: str) -> str:
    return "sr_" + content_hash(plan_id, context_id, "planning_replay")


def trace_id_for(plan_id: str) -> str:
    return "st_" + content_hash(plan_id, "trace")
