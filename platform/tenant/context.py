"""Tenant + organization context (M5.0).

The immutable identity carried through every request so isolation, quotas, and audit
have a single source of truth. Value objects only — no enforcement here (that's the
interfaces in `access.py`, unimplemented at M5.0).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class OrganizationContext:
    org_id: str
    name: str = ""
    plan: str = "free"
    region: str = "us"  # data residency
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Who/what a unit of work runs as — the isolation boundary for every module."""

    org: OrganizationContext
    user_id: str | None = None
    agent_id: str | None = None
    roles: tuple[str, ...] = ()
    request_id: str | None = None

    @property
    def org_id(self) -> str:
        return self.org.org_id
