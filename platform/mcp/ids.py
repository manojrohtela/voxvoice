"""Deterministic, content-hash identities (M6.8B-1).

A local copy of the `content_hash`/id-suffix pattern every closed runtime uses
(`tool/ids.py`, `skill/ids.py`, ...) — duplicated, not imported. The MCP Runtime
imports no sibling runtime (an M6.8A constraint), so this is the only option; it
also matches the established "duplicate, don't cross-import" precedent.
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


def server_id_for(namespace: str, id_: str, version: str) -> str:
    """The content identity of one registered MCP server version."""
    return "ms_" + content_hash(namespace, id_, version)


def descriptor_id_for(namespace: str, id_: str, version: str) -> str:
    """The content identity of one registration record."""
    return "md_" + content_hash(namespace, id_, version)


def connection_id_for(server_id: str, attempt: int, created_at: float) -> str:
    """The content identity of one connection instance."""
    return "mc_" + content_hash(server_id, attempt, created_at)


def session_id_for(connection_id: str, created_at: float) -> str:
    """The content identity of one session instance."""
    return "msess_" + content_hash(connection_id, created_at)


def snapshot_id_for(server_id: str, protocol_version: str, negotiated_at: float) -> str:
    """The content identity of one negotiated capability snapshot."""
    return "msnap_" + content_hash(server_id, protocol_version, negotiated_at)
