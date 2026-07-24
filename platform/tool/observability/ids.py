"""Deterministic, content-hash observability identities (M6.1B-3).

Reuses `tool.ids.content_hash` (same package, not a sibling runtime).
"""

from __future__ import annotations

from ..ids import content_hash


def trace_id_for(request_id: str) -> str:
    return "tt_" + content_hash(request_id, "trace")


def replay_id_for(request_id: str, result_hash: str) -> str:
    return "rp_" + content_hash(request_id, result_hash, "replay")


def audit_id_for(request_id: str, action: str, decision: str) -> str:
    return "ad_" + content_hash(request_id, action, decision, "audit")
