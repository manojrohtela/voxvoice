"""Deterministic, content-hash identities (M5.5B-1).

All reasoning ids are pure functions of content (never random, never clock-based), so the
same input always yields the same id — replay-friendly. SHA-256 (not Python's
per-process-salted `hash()`) keeps ids stable across processes. Mirrors the closed runtimes'
id modules.
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


def request_hash_for(provider: str, model: str, prompt_hash: str, generation: object) -> str:
    """The content identity of an inference request (provider + model + prompt + params)."""
    return "ir_" + content_hash(provider, model, prompt_hash, generation)


def response_hash_for(request_hash: str, chunks: object) -> str:
    """The content identity of a response — over the request + the ordered chunk deltas."""
    return "rp_" + content_hash(request_hash, *chunks)


def chunk_id_for(request_hash: str, index: int, delta: str) -> str:
    """Stable per (request, index, delta) — deterministic streaming chunk id."""
    return "rc_" + content_hash(request_hash, index, delta)


def package_id_for(session_id: str, response_hash: str, seq: int) -> str:
    """A package id stable for a (session, response content, ordinal)."""
    return "rz_" + content_hash(session_id, response_hash, seq)
