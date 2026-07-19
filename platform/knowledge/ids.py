"""Deterministic, content-hash identities (M5.2B-1).

All knowledge ids are pure functions of content (never random, never clock-based), so
the same input always yields the same id — which also gives free document/chunk
**deduplication**. SHA-256 (not Python's per-process-salted `hash()`) keeps ids stable
across processes.
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


def document_id_for(source_uri: str) -> str:
    """Stable per logical source (URI) — unchanged across content edits, so re-importing
    the same URI with new content produces a **new version** of the *same* document."""
    return "doc_" + content_hash(source_uri)


def version_id_for(document_id: str, number: int) -> str:
    return f"{document_id}.v{number}"


def chunk_id_for(document_id: str, version: int, index: int, chunk_content: str) -> str:
    return "chk_" + content_hash(document_id, version, index, chunk_content)
