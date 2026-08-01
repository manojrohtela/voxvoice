"""DTO mapping (M8.3): Application command → Foundation request → domain → view DTO.

One place the boundary translation lives. Application commands (client intent) map to the
Foundation's request DTOs; the Foundation returns immutable domain aggregates; those map
to Foundation view DTOs (the response projections). No domain logic — pure, total,
deterministic translation. Keeping it here means the use cases read as orchestration, not
plumbing, and a future transport layer (API) maps HTTP↔command without touching this.
"""

from __future__ import annotations

from api.foundation import (
    CreateEnvironmentRequest,
    CreateOrganizationRequest,
    CreateProjectRequest,
    CreateWorkspaceRequest,
    Environment,
    EnvironmentView,
    GrantRoleRequest,
    InviteMemberRequest,
    Membership,
    Organization,
    Project,
    ProjectView,
    RegisterUserRequest,
    RoleBinding,
    UpdatePreferencesRequest,
    User,
    UserPreferences,
    UserView,
    Workspace,
    WorkspaceView,
)
from api.foundation import (
    MemberView as _MemberView,
)
from api.foundation import (
    OrganizationView as _OrganizationView,
)
from api.foundation import (
    PreferencesView as _PreferencesView,
)

from . import commands as c

# ── command → Foundation request ─────────────────────────────────────────────────


def to_register_request(cmd: c.RegisterUser) -> RegisterUserRequest:
    return RegisterUserRequest(email=cmd.email, display_name=cmd.display_name)


def to_create_org_request(cmd: c.CreateOrganization) -> CreateOrganizationRequest:
    return CreateOrganizationRequest(
        name=cmd.name, owner_user_id=cmd.owner_user_id, plan=cmd.plan, region=cmd.region
    )


def to_create_workspace_request(cmd: c.CreateWorkspace) -> CreateWorkspaceRequest:
    return CreateWorkspaceRequest(org_id=cmd.org_id, name=cmd.name, slug=cmd.slug)


def to_create_environment_request(cmd: c.CreateEnvironment) -> CreateEnvironmentRequest:
    return CreateEnvironmentRequest(
        org_id=cmd.org_id, workspace_id=cmd.workspace_id, name=cmd.name, kind=cmd.kind
    )


def to_invite_request(cmd: c.InviteMember) -> InviteMemberRequest:
    return InviteMemberRequest(org_id=cmd.org_id, user_id=cmd.user_id)


def to_grant_role_request(cmd: c.GrantRole) -> GrantRoleRequest:
    return GrantRoleRequest(
        org_id=cmd.org_id,
        user_id=cmd.user_id,
        role=cmd.role,
        scope=cmd.scope,
        scope_id=cmd.scope_id,
    )


def to_create_project_request(cmd: c.CreateProject) -> CreateProjectRequest:
    return CreateProjectRequest(
        org_id=cmd.org_id, workspace_id=cmd.workspace_id, name=cmd.name, slug=cmd.slug
    )


def to_update_preferences_request(cmd: c.UpdatePreferences) -> UpdatePreferencesRequest:
    return UpdatePreferencesRequest(
        user_id=cmd.actor_user_id,
        org_id=cmd.org_id,
        theme=cmd.theme,
        locale=cmd.locale,
        keyboard_shortcuts_enabled=cmd.keyboard_shortcuts_enabled,
        favorites=cmd.favorites,
    )


# ── domain → view DTO ────────────────────────────────────────────────────────────


def user_view(user: User) -> UserView:
    return UserView.of(user)


def organization_view(org: Organization) -> _OrganizationView:
    return _OrganizationView.of(org)


def workspace_view(ws: Workspace) -> WorkspaceView:
    return WorkspaceView.of(ws)


def environment_view(env: Environment) -> EnvironmentView:
    return EnvironmentView.of(env)


def project_view(project: Project) -> ProjectView:
    return ProjectView.of(project)


def preferences_view(prefs: UserPreferences) -> _PreferencesView:
    return _PreferencesView.of(prefs)


def member_view(m: Membership, bindings: tuple[RoleBinding, ...] = ()) -> _MemberView:
    return _MemberView.of(m, bindings)
