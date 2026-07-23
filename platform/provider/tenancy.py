"""Provider tenancy + isolation (M5.6B-2).

Enforces tenant isolation **before provider selection** and resolves the effective scope
(organization / region). It wires the M5.0 `tenant` seams — `Authorizer`/`QuotaEnforcer`/
`AuditSink` — as *hooks* with permissive no-op defaults (real RBAC/quota/audit are
non-goals), reusing the exact seam Memory/Knowledge/Context/Prompt/Reasoning use
(`provider → tenant` is a leaf foundation dependency; acyclic). A **provider allow-list** and
**regional restrictions** are enforced here too, complementing (not replacing) the
`ProviderPolicyEngine`'s own organization/region policy checks — the router's policy layer is
pure/stateless verdicts; tenancy is the isolation gate + audit trail.

Isolation is deterministic and always on: a request may never execute outside its
`TenantContext`'s org. No authentication — that is out of scope.
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

from .domain import ProviderScope


class ProviderAccessDenied(PlatformError):
    """A provider operation crossed a tenant boundary or was denied by a hook."""


@dataclass(frozen=True, slots=True)
class ResolvedScope:
    """The effective, isolation-enforced scope for an execution."""

    scope: ProviderScope


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


class ProviderTenancy:
    def __init__(
        self,
        *,
        authorizer: Authorizer | None = None,
        quota: QuotaEnforcer | None = None,
        audit: AuditSink | None = None,
        allowed_providers: frozenset[str] = frozenset(),
        allowed_regions: frozenset[str] = frozenset(),
        clock: Callable[[], float] | None = None,
    ) -> None:
        import time

        self._authz = authorizer or AllowAllAuthorizer()
        self._quota = quota or UnlimitedQuota()
        self._audit = audit or NullAudit()
        self._allowed_providers = allowed_providers
        self._allowed_regions = allowed_regions
        self._clock = clock or time.time

    def guard(
        self,
        scope: ProviderScope,
        tenant: TenantContext | None,
        *,
        provider: str | None = None,
        action: str = "execute",
        resource: str = "provider",
    ) -> ResolvedScope:
        """Enforce isolation + provider allow-list + region restriction + hooks and return the
        effective scope. Raises `ProviderAccessDenied` on a cross-tenant attempt or a denial.
        Runs BEFORE provider selection."""
        if tenant is None:
            if provider and self._allowed_providers and provider not in self._allowed_providers:
                raise ProviderAccessDenied(f"provider {provider!r} not allowed")
            if (scope.region and self._allowed_regions
                    and scope.region not in self._allowed_regions):
                raise ProviderAccessDenied(f"region {scope.region!r} not allowed")
            return ResolvedScope(scope=scope)

        if scope.org_id is not None and scope.org_id != tenant.org_id:
            self._deny(tenant, action, resource, "cross-org access")
        if provider and self._allowed_providers and provider not in self._allowed_providers:
            self._deny(tenant, action, resource, f"provider {provider!r} not allowed")
        if scope.region and self._allowed_regions and scope.region not in self._allowed_regions:
            self._deny(tenant, action, resource, f"region {scope.region!r} not allowed")

        decision = self._authz.authorize(tenant, action, resource)
        if not decision.allowed:
            self._deny(tenant, action, resource, decision.reason or "authorizer denied")
        if not self._quota.check(tenant, resource).allowed:
            self._deny(tenant, action, resource, "quota exceeded")

        effective = replace(scope, org_id=tenant.org_id)
        self._audit.record(self._entry(tenant, action, resource, "allow"))
        return ResolvedScope(scope=effective)

    def _deny(self, tenant: TenantContext, action: str, resource: str, reason: str) -> None:
        self._audit.record(self._entry(tenant, action, resource, "deny", reason))
        raise ProviderAccessDenied(reason)

    def _entry(
        self, tenant: TenantContext, action: str, resource: str, outcome: str, detail: str = ""
    ) -> AuditRecord:
        return AuditRecord(
            ts=self._clock(), org_id=tenant.org_id,
            actor=tenant.user_id or tenant.agent_id or "unknown",
            action=action, resource=resource, outcome=outcome, detail=detail,
        )
