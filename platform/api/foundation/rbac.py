"""RBAC foundation: permissions, the role→permission map, and deterministic
permission evaluation (M8.2).

Realizes ADR-0021 §5 (the 11-role model) and Constitution §A11.2 (authorization scoped
to org on every query). Two layers:

  * `PermissionEvaluator` — pure, deterministic: given a user's role bindings and a
    required permission at a target scope, decide ALLOW/DENY. No I/O.
  * `RbacAuthorizer` — implements the frozen `tenant.access.Authorizer` seam so the
    rest of the platform can ask "may this tenant do X?" through the existing contract,
    backed by role-binding lookups.

Organization isolation is structural: the evaluator only ever considers bindings within
the target org, and an ORGANIZATION binding covers every workspace in that org while a
WORKSPACE binding covers exactly its own workspace. Cross-org access can never be
granted because bindings from another org are never in scope.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from tenant.access import AccessDecision, Authorizer, Effect
from tenant.context import TenantContext

from .domain import Role, RoleBinding, RoleScope


class Permission(StrEnum):
    """The foundation-governed permission vocabulary (identity/platform/security/
    config). Later Epic 8 milestones add their own permissions (agent/deploy/...)
    additively and extend `ROLE_PERMISSIONS`; nothing here presumes them."""

    ORG_READ = "org.read"
    ORG_MANAGE = "org.manage"
    MEMBER_READ = "member.read"
    MEMBER_MANAGE = "member.manage"
    ROLE_MANAGE = "role.manage"
    WORKSPACE_READ = "workspace.read"
    WORKSPACE_MANAGE = "workspace.manage"
    PROJECT_READ = "project.read"
    PROJECT_MANAGE = "project.manage"
    ENVIRONMENT_MANAGE = "environment.manage"
    SECRET_MANAGE = "secret.manage"
    SETTINGS_READ = "settings.read"
    SETTINGS_MANAGE = "settings.manage"
    FEATURE_FLAG_MANAGE = "feature_flag.manage"
    BILLING_MANAGE = "billing.manage"
    DEPLOY_APPROVE = "deploy.approve"
    PREFERENCES_MANAGE = "preferences.manage"


# Every permission the foundation knows about — the "all" set for org-wide roles.
_ALL: frozenset[Permission] = frozenset(Permission)

# Declared role→permission map (ADR-0021 §5). Extensible: later milestones union in
# their own permissions per role without changing this module's shape.
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.PLATFORM_SUPER_ADMIN: _ALL,
    Role.ORG_OWNER: _ALL,
    Role.BILLING_ADMIN: frozenset(
        {Permission.BILLING_MANAGE, Permission.ORG_READ, Permission.SETTINGS_READ}
    ),
    Role.WORKSPACE_ADMIN: frozenset(
        {
            Permission.WORKSPACE_READ,
            Permission.WORKSPACE_MANAGE,
            Permission.PROJECT_READ,
            Permission.PROJECT_MANAGE,
            Permission.ENVIRONMENT_MANAGE,
            Permission.SECRET_MANAGE,
            Permission.MEMBER_READ,
            Permission.SETTINGS_READ,
            Permission.SETTINGS_MANAGE,
            Permission.PREFERENCES_MANAGE,
        }
    ),
    Role.DEVELOPER: frozenset(
        {
            Permission.WORKSPACE_READ,
            Permission.PROJECT_READ,
            Permission.PROJECT_MANAGE,
            Permission.ENVIRONMENT_MANAGE,
            Permission.SETTINGS_READ,
            Permission.PREFERENCES_MANAGE,
        }
    ),
    Role.AGENT_DESIGNER: frozenset(
        {
            Permission.WORKSPACE_READ,
            Permission.PROJECT_READ,
            Permission.SETTINGS_READ,
            Permission.PREFERENCES_MANAGE,
        }
    ),
    Role.QA_REVIEWER: frozenset(
        {Permission.WORKSPACE_READ, Permission.PROJECT_READ, Permission.PREFERENCES_MANAGE}
    ),
    Role.HR_RECRUITER: frozenset(
        {Permission.WORKSPACE_READ, Permission.PROJECT_READ, Permission.PREFERENCES_MANAGE}
    ),
    Role.ANALYST: frozenset(
        {
            Permission.WORKSPACE_READ,
            Permission.PROJECT_READ,
            Permission.SETTINGS_READ,
            Permission.PREFERENCES_MANAGE,
        }
    ),
    Role.APPROVER: frozenset(
        {
            Permission.DEPLOY_APPROVE,
            Permission.WORKSPACE_READ,
            Permission.PROJECT_READ,
            Permission.PREFERENCES_MANAGE,
        }
    ),
    Role.READ_ONLY: frozenset(
        {
            Permission.ORG_READ,
            Permission.WORKSPACE_READ,
            Permission.PROJECT_READ,
            Permission.MEMBER_READ,
            Permission.SETTINGS_READ,
        }
    ),
    # M8.6 — org-membership tiers (additive).
    Role.ORG_ADMIN: frozenset(
        {
            Permission.ORG_READ,
            Permission.ORG_MANAGE,
            Permission.MEMBER_READ,
            Permission.MEMBER_MANAGE,
            Permission.ROLE_MANAGE,
            Permission.SETTINGS_READ,
            Permission.SETTINGS_MANAGE,
            Permission.WORKSPACE_READ,
            Permission.PREFERENCES_MANAGE,
        }
    ),
    Role.ORG_MEMBER: frozenset(
        {
            Permission.ORG_READ,
            Permission.MEMBER_READ,
            Permission.WORKSPACE_READ,
            Permission.PROJECT_READ,
            Permission.SETTINGS_READ,
            Permission.PREFERENCES_MANAGE,
        }
    ),
    Role.ORG_VIEWER: frozenset(
        {
            Permission.ORG_READ,
            Permission.MEMBER_READ,
            Permission.WORKSPACE_READ,
            Permission.PROJECT_READ,
            Permission.SETTINGS_READ,
        }
    ),
    # M8.7 — workspace-membership tiers (additive). Admin reuses WORKSPACE_ADMIN above.
    Role.WS_MEMBER: frozenset(
        {
            Permission.WORKSPACE_READ,
            Permission.PROJECT_READ,
            Permission.PROJECT_MANAGE,
            Permission.SETTINGS_READ,
            Permission.PREFERENCES_MANAGE,
        }
    ),
    Role.WS_VIEWER: frozenset(
        {
            Permission.WORKSPACE_READ,
            Permission.PROJECT_READ,
            Permission.SETTINGS_READ,
        }
    ),
}

# M8.6 — the four org tiers map to concrete role bindings. Owner reuses ORG_OWNER.
ORG_ROLE_BINDING: dict[str, Role] = {
    "owner": Role.ORG_OWNER,
    "admin": Role.ORG_ADMIN,
    "member": Role.ORG_MEMBER,
    "viewer": Role.ORG_VIEWER,
}


def role_for_org_tier(tier: str) -> Role:
    """Resolve an OrgRole tier value ('owner'/'admin'/'member'/'viewer') to its `Role`."""
    return ORG_ROLE_BINDING.get(tier, Role.ORG_MEMBER)


# M8.7 — the three workspace tiers map to concrete role bindings. Admin reuses
# WORKSPACE_ADMIN; the bindings are granted at WORKSPACE scope (one workspace only).
WS_ROLE_BINDING: dict[str, Role] = {
    "admin": Role.WORKSPACE_ADMIN,
    "member": Role.WS_MEMBER,
    "viewer": Role.WS_VIEWER,
}


def role_for_ws_tier(tier: str) -> Role:
    """Resolve a WsRole tier value ('admin'/'member'/'viewer') to its `Role`."""
    return WS_ROLE_BINDING.get(tier, Role.WS_MEMBER)


def permissions_for(role: Role) -> frozenset[Permission]:
    """The permissions a role grants (empty for an unmapped role — never raises)."""
    return ROLE_PERMISSIONS.get(role, frozenset())


class PermissionEvaluator:
    """Pure, deterministic RBAC decision function over a user's role bindings.

    `can(...)` returns True iff at least one of the user's bindings both (a) grants the
    required permission and (b) is in scope for the target. An ORGANIZATION binding is
    in scope for anything in the org; a WORKSPACE binding is in scope only for its own
    workspace. Bindings are assumed already filtered to the target org by the caller —
    the evaluator additionally asserts `binding.org_id == org_id` so isolation cannot be
    bypassed even if a caller passes a wider set."""

    def can(
        self,
        bindings: Iterable[RoleBinding],
        permission: Permission,
        *,
        org_id: str,
        workspace_id: str | None = None,
    ) -> bool:
        for b in bindings:
            if b.org_id != org_id:
                continue  # isolation: never honour a foreign-org binding
            if permission not in permissions_for(b.role):
                continue
            if self._in_scope(b, org_id=org_id, workspace_id=workspace_id):
                return True
        return False

    @staticmethod
    def _in_scope(b: RoleBinding, *, org_id: str, workspace_id: str | None) -> bool:
        if b.scope is RoleScope.ORGANIZATION:
            return b.scope_id == org_id
        # WORKSPACE binding: in scope only when a workspace target matches exactly.
        return workspace_id is not None and b.scope_id == workspace_id


# Map coarse `Authorizer.authorize(action, ...)` strings onto foundation permissions.
# Actions are `"<permission-value>"` (e.g. "project.manage"); unknown actions deny.
def _permission_from_action(action: str) -> Permission | None:
    try:
        return Permission(action)
    except ValueError:
        return None


class RbacAuthorizer(Authorizer):
    """Bridges foundation RBAC onto the frozen `tenant.access.Authorizer` seam.

    `resource` is interpreted as an optional target workspace id (or empty for an
    org-wide check); `tenant.roles` are ignored in favour of the authoritative role
    bindings supplied by an injected lookup, so a caller cannot self-assert roles."""

    def __init__(self, binding_lookup, evaluator: PermissionEvaluator | None = None) -> None:
        # binding_lookup: (org_id, user_id) -> Iterable[RoleBinding]
        self._lookup = binding_lookup
        self._evaluator = evaluator or PermissionEvaluator()

    def authorize(self, tenant: TenantContext, action: str, resource: str) -> AccessDecision:
        permission = _permission_from_action(action)
        if permission is None:
            return AccessDecision(Effect.DENY, reason=f"unknown action {action!r}")
        if tenant.user_id is None:
            return AccessDecision(Effect.DENY, reason="no subject")
        bindings = self._lookup(tenant.org_id, tenant.user_id)
        workspace_id = resource or None
        allowed = self._evaluator.can(
            bindings, permission, org_id=tenant.org_id, workspace_id=workspace_id
        )
        if allowed:
            return AccessDecision(Effect.ALLOW)
        return AccessDecision(Effect.DENY, reason=f"missing {permission.value}")
