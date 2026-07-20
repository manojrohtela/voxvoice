"""Context tenancy + isolation (M5.3B-2).

Enforces tenant isolation **before assembly** (isolation-first) and resolves the effective
scope (organization / conversation / session; private vs shared). It wires the M5.0
`tenant` seams — `Authorizer`/`QuotaEnforcer`/`AuditSink` — as *hooks* with permissive
no-op defaults (real RBAC/quota/audit are non-goals), reusing the exact seam Memory and
Knowledge use (`context → tenant` is a foundation dependency — leaf package, acyclic).

Isolation is deterministic and always on: a request may never widen past its
`TenantContext`'s org, and (when a user is set) may not cross into another user's session.
The resolved scope is injected into every downstream `MemoryQuery`/`KnowledgeQuery`, so each
source runtime *also* enforces its own isolation — defence in depth. Multi-agent / federated
context is a future additive `ContextSource`, never a widening of this boundary.
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

from .domain import ContextScope


class ContextAccessDenied(PlatformError):
    """A context operation crossed a tenant boundary or was denied by a hook."""


@dataclass(frozen=True, slots=True)
class ResolvedScope:
    """The effective, isolation-enforced scope for an assembly."""

    scope: ContextScope


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


class ContextTenancy:
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
        scope: ContextScope,
        tenant: TenantContext | None,
        *,
        action: str = "assemble",
        resource: str = "context",
    ) -> ResolvedScope:
        """Enforce isolation + hooks and return the effective scope. Raises
        `ContextAccessDenied` on a cross-tenant attempt or a hook denial. Runs BEFORE any
        source is fetched."""
        if tenant is None:
            return ResolvedScope(scope=scope)

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

        effective = replace(scope, org_id=tenant.org_id, user_id=scope.user_id or tenant.user_id)
        self._audit.record(self._entry(tenant, action, resource, "allow"))
        return ResolvedScope(scope=effective)

    def _deny(self, tenant: TenantContext, action: str, resource: str, reason: str) -> None:
        self._audit.record(self._entry(tenant, action, resource, "deny", reason))
        raise ContextAccessDenied(reason)

    def _entry(
        self, tenant: TenantContext, action: str, resource: str, outcome: str, detail: str = ""
    ) -> AuditRecord:
        return AuditRecord(
            ts=self._clock(),
            org_id=tenant.org_id,
            actor=tenant.user_id or tenant.agent_id or "unknown",
            action=action,
            resource=resource,
            outcome=outcome,
            detail=detail,
        )
