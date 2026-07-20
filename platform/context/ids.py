"""Deterministic, content-hash identities (M5.3B-1).

All context ids are pure functions of content (never random, never clock-based), so the
same input always yields the same id — which also gives free item **deduplication**
downstream. SHA-256 (not Python's per-process-salted `hash()`) keeps ids stable across
processes. Mirrors `memory.ids` / `knowledge.ids`.
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


def item_id_for(kind: str, source_runtime: str, source_id: str, content: str) -> str:
    """Stable per (kind, origin, source id, content) — identical items across sources
    collapse to one id (free dedup for the intelligence layer)."""
    return "ci_" + content_hash(kind, source_runtime, source_id, content)


def package_id_for(session_id: str, request_hash: str, seq: int) -> str:
    """A package id stable for a (session, request, assembly ordinal) — deterministic
    under a fixed request + assembly order."""
    return "cp_" + content_hash(session_id, request_hash, seq)


def request_hash_for(*parts: object) -> str:
    """Hash of the salient request fields — used in the package id + dedup of packages."""
    return content_hash(*parts)
