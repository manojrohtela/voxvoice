"""Prefixed, collision-resistant ids (org_xxx, agt_xxx, ...).

Moved from `api/ids.py` to `core/` at M1 — ids are a shared primitive used
across planes, so they belong on the dependency floor.
"""

from __future__ import annotations

import secrets


def gen_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(12)}"
