"""Knowledge tenancy + isolation (M5.2B-2).

Enforces tenant isolation **before every retrieval** and resolves the effective scope
(organization / private / shared / department knowledge). It wires the M5.0 `tenant`
seams — `Authorizer`, `QuotaEnforcer`, `AuditSink` — as *hooks*: permissive no-op
implementations ship here (real RBAC/quota/audit are non-goals), but the seam is live so
an enterprise implementation drops in without a runtime change.

Isolation is deterministic and always on: a query may never widen past its
`TenantContext`'s org, and a user may not read another user's PRIVATE knowledge. ORG/
SHARED/STATIC/REFERENCE knowledge is visible to every member of the org; PRIVATE is
user-scoped; a matching `department` narrows further. When no `TenantContext` is supplied
(local/dev), the scope passes through unchanged. Federated Knowledge (cross-org) is a
future additive `KnowledgeSource`, never a widening of this boundary.
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

from .domain import Document, KnowledgeScope, KnowledgeType

# Knowledge types visible to every member of an org (not user-private).
_ORG_VISIBLE = frozenset({
    KnowledgeType.ORGANIZATION,
    KnowledgeType.SHARED,
    KnowledgeType.STATIC,
    KnowledgeType.REFERENCE,
    KnowledgeType.GENERATED,
    KnowledgeType.IMPORTED,
})


class KnowledgeAccessDenied(PlatformError):
    """A knowledge operation crossed a tenant boundary or was denied by a hook."""


@dataclass(frozen=True, slots=True)
class ResolvedScope:
    """The effective, isolation-enforced scope for a retrieval."""

    scope: KnowledgeScope


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


class KnowledgeTenancy:
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
        scope: KnowledgeScope,
        tenant: TenantContext | None,
        *,
        action: str = "retrieve",
        resource: str = "knowledge",
    ) -> ResolvedScope:
        """Enforce isolation + hooks and return the effective scope. Raises
        `KnowledgeAccessDenied` on a cross-tenant attempt or a hook denial. Runs BEFORE
        any candidate is fetched."""
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

        effective = replace(
            scope, org_id=tenant.org_id, user_id=scope.user_id or tenant.user_id
        )
        self._audit.record(self._entry(tenant, action, resource, "allow"))
        return ResolvedScope(scope=effective)

    def admits(self, document: Document, scope: KnowledgeScope) -> bool:
        """Deterministic per-document visibility under a resolved scope. Applied while
        merging candidates so shared/org knowledge is visible but another user's PRIVATE
        knowledge is not."""
        if scope.org_id is not None and document.scope.org_id != scope.org_id:
            return False
        if document.knowledge_type in _ORG_VISIBLE:
            visible = True
        else:  # PRIVATE / EXTERNAL → owner only
            visible = scope.user_id is None or document.scope.user_id == scope.user_id
        if not visible:
            return False
        if scope.department is not None and document.scope.department is not None:
            return document.scope.department == scope.department
        return True

    def consume(
        self, tenant: TenantContext | None, *, resource: str = "knowledge", amount: int = 1
    ) -> None:
        if tenant is not None:
            self._quota.consume(tenant, resource, amount)

    def _deny(self, tenant: TenantContext, action: str, resource: str, reason: str) -> None:
        self._audit.record(self._entry(tenant, action, resource, "deny", reason))
        raise KnowledgeAccessDenied(reason)

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
