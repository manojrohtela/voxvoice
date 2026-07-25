"""Deterministic, content-hash replay identities (M6.6B-2).

Reuses `loop.core.ids.content_hash` (same package, not a sibling runtime).
"""

from __future__ import annotations

from ..core.ids import content_hash


def replay_id_for(loop_id: str, replay_hash: str) -> str:
    return "ly_" + content_hash(loop_id, replay_hash, "replay")
