"""Prefixed, collision-resistant ids (org_xxx, agt_xxx, ...)."""

from __future__ import annotations

import secrets


def gen_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(12)}"
