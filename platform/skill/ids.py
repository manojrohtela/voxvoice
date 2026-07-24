"""Deterministic, content-hash identities (M6.2B-1).

A local copy of the `content_hash`/id-suffix pattern every closed runtime uses
(`tool/ids.py`, `provider/ids.py`, ...) — duplicated, not imported. Skill
Runtime never imports Tool Runtime (an explicit M6.2B-1 constraint), so this
is not a choice here, it is the only option; it also matches the established
"duplicate, don't cross-import" precedent every other closed runtime already
follows for the same primitive.
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
    """The content identity of one registered skill version."""
    return "sk_" + content_hash(namespace, id_, version)


def registration_id_for(namespace: str, id_: str, version: str, registered_at: float) -> str:
    """The content identity of one registration record (temporary repository)."""
    return "sr_" + content_hash(namespace, id_, version, registered_at)
