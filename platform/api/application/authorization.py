"""Authorization orchestration (M8.3).

The Application Layer orchestrates authorization by calling the **Foundation's**
authorization interface (`tenant.access.Authorizer`, implemented by the Foundation's
`RbacAuthorizer`) — it never reimplements permission logic (no duplication; the single
source of RBAC truth stays in `api.foundation.rbac`). A future API layer therefore holds
**no** permission logic: it dispatches a command, and this orchestrator (via the use
cases) authorizes.

The Foundation's domain services also guard themselves internally (defense in depth, same
evaluator); this orchestration is the explicit application-boundary gate that yields a
clean denial + application audit before the domain call is attempted.
"""

from __future__ import annotations

from api.foundation import AuthorizationError, Permission
from tenant.access import Authorizer
from tenant.context import OrganizationContext, TenantContext


def tenant_for(actor_user_id: str, org_id: str) -> TenantContext:
    """Build the immutable tenant context an authorization check runs against."""
    return TenantContext(org=OrganizationContext(org_id=org_id), user_id=actor_user_id)


class AuthorizationOrchestrator:
    """Thin orchestration over the Foundation authorizer seam. `require` raises the
    Foundation's typed `AuthorizationError` on deny (rooted in `PlatformError`), which
    the dispatcher turns into a `use_case.rejected` audit event."""

    def __init__(self, authorizer: Authorizer) -> None:
        self._authorizer = authorizer

    def require(
        self,
        actor_user_id: str,
        org_id: str,
        permission: Permission,
        *,
        workspace_id: str | None = None,
    ) -> None:
        tenant = tenant_for(actor_user_id, org_id)
        decision = self._authorizer.authorize(
            tenant, permission.value, workspace_id or ""
        )
        if not decision.allowed:
            raise AuthorizationError(
                f"{actor_user_id!r} denied {permission.value} in org {org_id!r}: "
                f"{decision.reason}"
            )
