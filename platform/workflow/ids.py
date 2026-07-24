"""Deterministic, content-hash identities (M6.4B-1).

A local copy of the `content_hash`/id-suffix pattern every closed runtime uses
(`tool/ids.py`, `skill/ids.py`, `hooks/ids.py`) — duplicated, not imported.
Workflow Runtime never imports Tool Runtime, Skill Runtime, or Hook Framework
(an explicit M6.4B-1 constraint).
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
    return "wf_" + content_hash(namespace, id_, version)


def registration_id_for(namespace: str, id_: str, version: str, registered_at: float) -> str:
    return "wr_" + content_hash(namespace, id_, version, registered_at)
