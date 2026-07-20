"""Deterministic, content-hash identities (M5.4B-1).

All prompt ids are pure functions of content (never random, never clock-based), so the
same input always yields the same id — replay-friendly + dedup-friendly. SHA-256 (not
Python's per-process-salted `hash()`) keeps ids stable across processes. Mirrors
`memory.ids` / `knowledge.ids` / `context.ids`.
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


def template_version_for(template_id: str, body: str) -> str:
    """A content-hash version tag for a template body (immutable, versioned by content)."""
    return "t_" + content_hash(template_id, body)


def section_id_for(kind: str, role: str, content: str) -> str:
    """Stable per (kind, role, content) — identical rendered sections collapse to one id."""
    return "ps_" + content_hash(kind, role, content)


def prompt_hash_for(sections: object) -> str:
    """A stable hash over the ordered section ids — the prompt's content identity."""
    return content_hash(*sections)


def package_id_for(session_id: str, prompt_hash: str, seq: int) -> str:
    """A package id stable for a (session, prompt content, assembly ordinal)."""
    return "pp_" + content_hash(session_id, prompt_hash, seq)


def prompt_fingerprint_for(triples: object) -> str:
    """The **provider-independent** semantic identity of a prompt — a hash over each
    section's (kind, role, content). Two providers rendering the same sections into
    different message shapes share this fingerprint though their `prompt_hash` differs.
    Metadata only; no semantic AI. `triples` is an iterable of (kind, role, content)."""
    return "pf_" + content_hash(*[f"{k}|{r}|{c}" for (k, r, c) in triples])
