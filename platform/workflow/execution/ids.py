"""Deterministic, content-hash execution identities (M6.4B-2).

Reuses `workflow.ids.content_hash` (same package, not a sibling runtime).
"""

from __future__ import annotations

from ..ids import content_hash


def execution_id_for(namespace: str, workflow_id: str, version: str, started_at: float) -> str:
    return "we_" + content_hash(namespace, workflow_id, version, started_at, "execution")


def context_id_for(execution_id: str, node_id: str, created_at: float) -> str:
    return "wx_" + content_hash(execution_id, node_id, created_at, "context")


def checkpoint_id_for(execution_id: str, node_id: str, sequence: int) -> str:
    return "wc_" + content_hash(execution_id, node_id, sequence, "checkpoint")


def trace_id_for(execution_id: str) -> str:
    return "wt_" + content_hash(execution_id, "trace")


def replay_id_for(execution_id: str, result_hash: str) -> str:
    return "wy_" + content_hash(execution_id, result_hash, "replay")
