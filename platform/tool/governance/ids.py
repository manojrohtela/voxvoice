"""Deterministic, content-hash governance identities (M6.1B-2).

Reuses `tool.ids.content_hash` (same package, not a sibling runtime — a plain
relative import, not the cross-runtime duplication `tool/_run.py` avoids).
"""

from __future__ import annotations

from ..ids import content_hash


def approval_id_for(request_id: str) -> str:
    """One approval per request — deterministic, replay-safe."""
    return "ap_" + content_hash(request_id, "approval")


def policy_eval_id_for(request_id: str, verdict: str) -> str:
    return "pe_" + content_hash(request_id, "policy", verdict)


def authorization_id_for(request_id: str, identity_id: str) -> str:
    return "az_" + content_hash(request_id, "authorization", identity_id)


def rate_limit_id_for(scope: str, key: str, now: float) -> str:
    return "rl_" + content_hash(scope, key, now)
