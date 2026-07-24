"""Deterministic, content-hash identities (M6.3B).

A local copy of the `content_hash`/id-suffix pattern every closed runtime uses
(`tool/ids.py`, `skill/ids.py`, ...) — duplicated, not imported. The Hook
Framework never imports Tool Runtime or Skill Runtime (an explicit M6.3B
constraint).
"""

from __future__ import annotations

import hashlib


def content_hash(*parts: object) -> str:
    """A stable 16-hex-char digest over the given parts (order-sensitive)."""
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else str(p).encode("utf-8"))
        h.update(b"\x00")  # unambiguous field separator
    return h.hexdigest()[:16]


def descriptor_id_for(namespace: str, id_: str, version: str) -> str:
    return "hk_" + content_hash(namespace, id_, version)


def registration_id_for(namespace: str, id_: str, version: str, registered_at: float) -> str:
    return "hr_" + content_hash(namespace, id_, version, registered_at)


def event_id_for(point: str, scope: str, session_id: str, created_at: float) -> str:
    return "he_" + content_hash(point, scope, session_id, created_at)


def trace_id_for(event_id: str) -> str:
    return "ht_" + content_hash(event_id, "trace")


def replay_id_for(event_id: str, result_hash: str) -> str:
    return "hy_" + content_hash(event_id, result_hash, "replay")
