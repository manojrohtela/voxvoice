"""Authorization model (M6.1B-2) — decisions only, never execution.

Mirrors `provider.tenancy`'s permissive-default-hook pattern (`AllowAllAuthorizer`)
but over the local `GovernanceIdentity` shape (User/Organization/Workspace/Service/
future Agent) rather than `tenant.TenantContext` — see `tenancy.py` for why no
`tenant` import exists anywhere in `tool`. `AuthorizationEngine` produces an
`AuthorizationDecision`; it never touches the registry or an adapter.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .domain import AuthorizationDecision, Effect, GovernanceIdentity


class IdentityAuthorizer(ABC):
    """RBAC decision point: may `identity` perform `action` on `resource`? A local
    analog of `tenant.Authorizer` (duplicated shape, not imported — see
    `tenancy.py`)."""

    @abstractmethod
    def authorize(
        self, identity: GovernanceIdentity | None, action: str, resource: str
    ) -> AuthorizationDecision: ...


class AllowAllAuthorizer(IdentityAuthorizer):
    """The permissive default — real RBAC is a named non-goal here (an
    `EnterprisePolicyProvider`/RBAC/ABAC seam is reserved in `extensions.py`)."""

    def authorize(
        self, identity: GovernanceIdentity | None, action: str, resource: str
    ) -> AuthorizationDecision:
        return AuthorizationDecision(effect=Effect.ALLOW, identity=identity, action=action,
                                     resource=resource)


class AuthorizationEngine:
    """Wraps one `IdentityAuthorizer`. No identity → always allowed (governance
    that requires no distinct caller identity, e.g. a system-internal call)."""

    def __init__(self, authorizer: IdentityAuthorizer | None = None) -> None:
        self._authorizer = authorizer or AllowAllAuthorizer()

    def authorize(
        self, identity: GovernanceIdentity | None, *, action: str, resource: str
    ) -> AuthorizationDecision:
        return self._authorizer.authorize(identity, action, resource)
