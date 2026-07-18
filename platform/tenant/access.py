"""Access-control, quota, audit + tenant-policy interfaces (M5.0 — interfaces only).

The seams enterprise concerns plug into. Defining them now lets every AI runtime
module accept a `TenantContext` + these ports and enforce isolation uniformly, with
implementations (real RBAC, quota backends, audit sinks) landing later. No
implementation at M5.0 (non-goal). No authentication.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum

from .context import TenantContext


class Effect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class AccessDecision:
    effect: Effect
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.effect is Effect.ALLOW


class Authorizer(ABC):
    """RBAC decision point: may `tenant` perform `action` on `resource`?"""

    @abstractmethod
    def authorize(self, tenant: TenantContext, action: str, resource: str) -> AccessDecision: ...


@dataclass(frozen=True, slots=True)
class QuotaCheck:
    allowed: bool
    remaining: int
    limit: int


class QuotaEnforcer(ABC):
    """Usage limits per tenant (requests, tokens, storage, cost)."""

    @abstractmethod
    def check(self, tenant: TenantContext, resource: str, amount: int = 1) -> QuotaCheck: ...
    @abstractmethod
    def consume(self, tenant: TenantContext, resource: str, amount: int = 1) -> None: ...


@dataclass(frozen=True, slots=True)
class AuditRecord:
    ts: float
    org_id: str
    actor: str
    action: str
    resource: str
    outcome: str
    detail: str = ""


class AuditSink(ABC):
    """Immutable audit trail for compliance."""

    @abstractmethod
    def record(self, entry: AuditRecord) -> None: ...


class TenantPolicy(ABC):
    """Per-tenant policy resolution (data residency, retention, model allow-lists)."""

    @abstractmethod
    def get(self, tenant: TenantContext, key: str) -> str | None: ...
