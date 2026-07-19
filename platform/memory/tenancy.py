"""Memory tenancy + isolation (M5.1B-2).

Enforces tenant isolation **before every retrieval** and resolves the effective scope
(cross-session / conversation / organization / agent). It wires the M5.0 `tenant`
seams — `Authorizer`, `QuotaEnforcer`, `AuditSink` — as *hooks*: permissive no-op
implementations ship here (real RBAC/quota/audit are non-goals), but the seam is live
so an enterprise implementation drops in without a runtime change.

Isolation itself is deterministic and always on: a query may never widen past its
`TenantContext`'s org/user. When no `TenantContext` is supplied (local/dev), the scope
passes through unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from core.errors import PlatformError
from tenant import (
    AccessDecision,
    AuditRecord,
    AuditSink,
    Authorizer,
    Effect,
    QuotaCheck,
    QuotaEnforcer,
    TenantContext,
)

from .domain import MemoryScope


class MemoryAccessDenied(PlatformError):
    """A memory operation crossed a tenant boundary or was denied by a hook."""


@dataclass(frozen=True, slots=True)
class ResolvedScope:
    """The effective, isolation-enforced scope for an operation."""

    scope: MemoryScope
    agent_id: str | None = None


# ── permissive default hooks (real implementations are non-goals) ────────────
class AllowAllAuthorizer(Authorizer):
    def authorize(self, tenant: TenantContext, action: str, resource: str) -> AccessDecision:
        return AccessDecision(Effect.ALLOW)


class UnlimitedQuota(QuotaEnforcer):
    def check(self, tenant: TenantContext, resource: str, amount: int = 1) -> QuotaCheck:
        return QuotaCheck(allowed=True, remaining=-1, limit=-1)

    def consume(self, tenant: TenantContext, resource: str, amount: int = 1) -> None:
        return None


class NullAudit(AuditSink):
    def record(self, entry: AuditRecord) -> None:
        return None


class MemoryTenancy:
    def __init__(
        self,
        *,
        authorizer: Authorizer | None = None,
        quota: QuotaEnforcer | None = None,
        audit: AuditSink | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        import time

        self._authz = authorizer or AllowAllAuthorizer()
        self._quota = quota or UnlimitedQuota()
        self._audit = audit or NullAudit()
        self._clock = clock or time.time

    def guard(
        self,
        scope: MemoryScope,
        tenant: TenantContext | None,
        *,
        action: str,
        resource: str = "memory",
    ) -> ResolvedScope:
        """Enforce isolation + hooks and return the effective scope. Raises
        `MemoryAccessDenied` on a cross-tenant attempt or a hook denial."""
        if tenant is None:
            return ResolvedScope(scope=scope, agent_id=None)

        # deterministic isolation: never widen past the tenant's org/user
        if scope.org_id is not None and scope.org_id != tenant.org_id:
            self._deny(tenant, action, resource, "cross-org access")
        if (
            scope.user_id is not None
            and tenant.user_id is not None
            and scope.user_id != tenant.user_id
        ):
            self._deny(tenant, action, resource, "cross-user access")

        decision = self._authz.authorize(tenant, action, resource)
        if not decision.allowed:
            self._deny(tenant, action, resource, decision.reason or "authorizer denied")

        check = self._quota.check(tenant, resource)
        if not check.allowed:
            self._deny(tenant, action, resource, "quota exceeded")

        effective = replace(
            scope,
            org_id=tenant.org_id,
            user_id=scope.user_id or tenant.user_id,
        )
        self._audit.record(
            AuditRecord(
                ts=self._clock(),
                org_id=tenant.org_id,
                actor=tenant.user_id or tenant.agent_id or "unknown",
                action=action,
                resource=resource,
                outcome="allow",
            )
        )
        return ResolvedScope(scope=effective, agent_id=tenant.agent_id)

    def consume(
        self, tenant: TenantContext | None, *, resource: str = "memory", amount: int = 1
    ) -> None:
        if tenant is not None:
            self._quota.consume(tenant, resource, amount)

    def _deny(self, tenant: TenantContext, action: str, resource: str, reason: str) -> None:
        self._audit.record(
            AuditRecord(
                ts=self._clock(),
                org_id=tenant.org_id,
                actor=tenant.user_id or tenant.agent_id or "unknown",
                action=action,
                resource=resource,
                outcome="deny",
                detail=reason,
            )
        )
        raise MemoryAccessDenied(reason)
