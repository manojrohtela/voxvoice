"""Deterministic, content-hash identities (M7.0B-1).

A local copy of the `content_hash`/id-suffix pattern every closed runtime uses
(`skill/ids.py`, `tool/ids.py`, ...) — duplicated, not imported. Product Runtime
imports no sibling runtime, so this is the only option; it also matches the
established "duplicate, don't cross-import" precedent (ARCHITECTURE_RULES AR-15,
AI_AGENT_RULES #14 — reuse of a *pattern*, not copy-paste of behaviour that could
drift).

Identity is derived from the manifest's *canonical* form, so two manifests with
identical content in different authoring order hash identically (§determinism of
the M7.0A design).
"""

from __future__ import annotations

import hashlib

from .canonical import canonical_json
from .domain import ProductManifest


def content_hash(*parts: object) -> str:
    """A stable 16-hex-char digest over the given parts (order-sensitive)."""
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else str(p).encode("utf-8"))
        h.update(b"\x00")  # unambiguous field separator
    return h.hexdigest()[:16]


def manifest_hash(manifest: ProductManifest) -> str:
    """The full-length content digest of a manifest's canonical form. Authoring
    order does not affect it (canonicalization sorts every collection)."""
    return hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()


def definition_id_for(manifest: ProductManifest) -> str:
    """The content identity of one product definition, prefixed `pd_`."""
    return "pd_" + manifest_hash(manifest)[:16]
