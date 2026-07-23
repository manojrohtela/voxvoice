"""Deterministic, content-hash identities (M6.1B-1).

All tool ids are pure functions of content (never random, never clock-based), so the
same input always yields the same id — replay-friendly. SHA-256 (not Python's
per-process-salted `hash()`) keeps ids stable across processes. Mirrors the closed
runtimes' `ids.py` modules (a local copy per package — no cross-runtime import; see
`provider/ids.py`).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def content_hash(*parts: object) -> str:
    """A stable 16-hex-char digest over the given parts (order-sensitive)."""
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else str(p).encode("utf-8"))
        h.update(b"\x00")  # unambiguous field separator
    return h.hexdigest()[:16]


def _stable_json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, default=str)


def descriptor_id_for(namespace: str, name: str, version: str) -> str:
    """The content identity of one registered tool version."""
    return "tl_" + content_hash(namespace, name, version)


def request_hash_for(
    namespace: str, tool: str, version: str | None, arguments: Mapping[str, Any]
) -> str:
    """The content identity of a tool call (namespace + tool + version + arguments)."""
    return "tc_" + content_hash(namespace, tool, version or "", _stable_json(arguments))


def result_hash_for(request_hash: str, output: object) -> str:
    """The content identity of a result — over the request + the produced output."""
    return "tr_" + content_hash(request_hash, repr(output))
