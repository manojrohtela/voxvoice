"""Canonicalization (M7.0B-1) — the single source of deterministic order.

The Product Manifest must hash identically regardless of authoring order
(§determinism of the M7.0A design). Canonicalization achieves that by, recursively:
  - sorting every mapping's keys, and
  - sorting every list by the canonical JSON of its elements.

So two manifests that differ only in the *order* their agents/workflows/... were
written produce byte-identical canonical JSON and therefore an identical hash. This
is the one place ordering is defined; `ids.manifest_hash` and every diagnostic
`path` build on it. Stateless, no I/O.
"""

from __future__ import annotations

import json
from typing import Any

from .domain import ProductManifest
from .serializer import ProductSerializer

_SERIALIZER = ProductSerializer()


def canonical_form(value: Any) -> Any:
    """Return a recursively order-normalized copy of a JSON-compatible value:
    mapping keys sorted; lists sorted by their elements' canonical JSON."""
    if isinstance(value, dict):
        return {k: canonical_form(value[k]) for k in sorted(value)}
    if isinstance(value, (list, tuple)):
        canon_items = [canonical_form(v) for v in value]
        return sorted(canon_items, key=lambda item: json.dumps(item, sort_keys=True))
    return value


def canonical_manifest_dict(manifest: ProductManifest) -> dict[str, Any]:
    """The manifest as a fully order-normalized plain dict."""
    return canonical_form(_SERIALIZER.manifest_to_dict(manifest))


def canonical_json(manifest: ProductManifest) -> str:
    """The manifest's canonical JSON — the exact byte string identity is hashed
    over. `sort_keys` is redundant with `canonical_form` but kept as a belt-and-
    braces guarantee against any future non-dict container leaking through."""
    return json.dumps(
        canonical_manifest_dict(manifest),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
