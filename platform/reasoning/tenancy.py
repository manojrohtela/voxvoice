"""Reasoning tenancy + isolation (M5.5B-2).

Enforces tenant isolation **before provider selection** and resolves the effective scope
(organization / session). It wires the M5.0 `tenant` seams — `Authorizer`/`QuotaEnforcer`/
`AuditSink` — as *hooks* with permissive no-op defaults (real RBAC/quota/audit are non-goals),
reusing the exact seam Memory/Knowledge/Context/Prompt use (`reasoning → tenant` is a leaf
foundation dependency; acyclic — DL-0036). A **provider allow-list** is enforced here too
(defaulting to allow-all).

Isolation is deterministic and always on: a request may never run inference outside its
`TenantContext`'s org, and (when a user is set) may not use another user's session. Federated
sessions are a future additive `SessionProvider`, never a boundary widening.
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

from .domain import ReasoningScope


class ReasoningAccessDenied(PlatformError):
    """A reasoning operation crossed a tenant boundary or was denied by a hook."""


@dataclass(frozen=True, slots=True)
class ResolvedScope:
    """The effective, isolation-enforced scope for an inference."""

    scope: ReasoningScope


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


class ReasoningTenancy:
    def __init__(
        self,
        *,
        authorizer: Authorizer | None = None,
        quota: QuotaEnforcer | None = None,
        audit: AuditSink | None = None,
        allowed_providers: frozenset[str] = frozenset(),
        clock: Callable[[], float] | None = None,
    ) -> None:
        import time

        self._authz = authorizer or AllowAllAuthorizer()
        self._quota = quota or UnlimitedQuota()
        self._audit = audit or NullAudit()
        self._allowed_providers = allowed_providers
        self._clock = clock or time.time

    def guard(
        self,
        scope: ReasoningScope,
        tenant: TenantContext | None,
        *,
        provider: str | None = None,
        action: str = "infer",
        resource: str = "reasoning",
    ) -> ResolvedScope:
        """Enforce isolation + provider allow-list + hooks and return the effective scope.
        Raises `ReasoningAccessDenied` on a cross-tenant attempt or a denial. Runs BEFORE
        provider selection."""
        if tenant is None:
            if provider and self._allowed_providers and provider not in self._allowed_providers:
                raise ReasoningAccessDenied(f"provider {provider!r} not allowed")
            return ResolvedScope(scope=scope)

        if scope.org_id is not None and scope.org_id != tenant.org_id:
            self._deny(tenant, action, resource, "cross-org access")
        if (
            scope.user_id is not None
            and tenant.user_id is not None
            and scope.user_id != tenant.user_id
        ):
            self._deny(tenant, action, resource, "cross-user access")
        if provider and self._allowed_providers and provider not in self._allowed_providers:
            self._deny(tenant, action, resource, f"provider {provider!r} not allowed")

        decision = self._authz.authorize(tenant, action, resource)
        if not decision.allowed:
            self._deny(tenant, action, resource, decision.reason or "authorizer denied")
        if not self._quota.check(tenant, resource).allowed:
            self._deny(tenant, action, resource, "quota exceeded")

        effective = replace(scope, org_id=tenant.org_id, user_id=scope.user_id or tenant.user_id)
        self._audit.record(self._entry(tenant, action, resource, "allow"))
        return ResolvedScope(scope=effective)

    def _deny(self, tenant: TenantContext, action: str, resource: str, reason: str) -> None:
        self._audit.record(self._entry(tenant, action, resource, "deny", reason))
        raise ReasoningAccessDenied(reason)

    def _entry(
        self, tenant: TenantContext, action: str, resource: str, outcome: str, detail: str = ""
    ) -> AuditRecord:
        return AuditRecord(
            ts=self._clock(), org_id=tenant.org_id,
            actor=tenant.user_id or tenant.agent_id or "unknown",
            action=action, resource=resource, outcome=outcome, detail=detail,
        )
