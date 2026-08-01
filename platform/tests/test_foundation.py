"""Customer Platform Foundation (Epic 8, M8.2) tests.

Verifies: immutable aggregates; the identity→organization→workspace bootstrap
(org auto-provisions an ACTIVE owner + ORG_OWNER binding, workspace auto-provisions a
default dev environment); RBAC permission evaluation across org/workspace scopes;
organization isolation (no cross-org read or grant); deterministic validation + fail-fast
services; typed domain events / audit trail; deterministic feature-flag resolution under
fixed precedence; the `tenant.access.Authorizer` bridge; deterministic construction under
injected seams; and the acyclic `api.foundation → core, tenant` dependency rule (zero
import of any frozen runtime/execution/product package or FastAPI).

Regression anchors are marked `# regression:` — each pins a guarantee that must fail on a
naive reimplementation.
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import FrozenInstanceError

import pytest

from api.foundation import (
    AuthorizationError,
    ConflictError,
    CreateOrganizationRequest,
    CreateProjectRequest,
    CreateWorkspaceRequest,
    FeatureFlag,
    FixedClock,
    FoundationFactory,
    FoundationValidationError,
    GrantRoleRequest,
    InviteMemberRequest,
    MembershipStatus,
    OrganizationIsolationError,
    Permission,
    RegisterUserRequest,
    Role,
    RoleScope,
    SequentialIdGenerator,
    SettingScope,
    UpdatePreferencesRequest,
    permissions_for,
)
from tenant.access import Effect
from tenant.context import OrganizationContext, TenantContext


def _foundation():
    return FoundationFactory.default(ids=SequentialIdGenerator(), clock=FixedClock())


def _bootstrap_org(f):
    owner = f.identity.register_user(RegisterUserRequest("owner@acme.test", "Owner"))
    org = f.identity.create_organization(
        CreateOrganizationRequest(name="Acme", owner_user_id=owner.id)
    )
    return owner, org


# ── identity + bootstrap ─────────────────────────────────────────────────────────


def test_create_organization_auto_provisions_active_owner_and_role():
    f = _foundation()
    owner, org = _bootstrap_org(f)
    membership = f.membership.list_members(org.id)
    # regression: exactly one member, ACTIVE, and the creator
    assert len(membership) == 1
    assert membership[0].user_id == owner.id
    assert membership[0].status is MembershipStatus.ACTIVE
    # regression: owner holds an org-scoped ORG_OWNER binding (never left admin-less)
    roles = f.access.roles_of(org.id, owner.id)
    assert [b.role for b in roles] == [Role.ORG_OWNER]
    assert roles[0].scope is RoleScope.ORGANIZATION and roles[0].scope_id == org.id


def test_register_user_rejects_bad_email_and_duplicates():
    f = _foundation()
    with pytest.raises(FoundationValidationError) as ei:
        f.identity.register_user(RegisterUserRequest("not-an-email", "X"))
    assert ei.value.diagnostics.has_errors  # carries full diagnostics
    f.identity.register_user(RegisterUserRequest("a@b.test", "A"))
    with pytest.raises(ConflictError):
        f.identity.register_user(RegisterUserRequest("a@b.test", "A2"))


def test_create_workspace_provisions_default_dev_environment():
    f = _foundation()
    owner, org = _bootstrap_org(f)
    ws = f.workspaces.create_workspace(
        owner.id, CreateWorkspaceRequest(org.id, "Web", "web")
    )
    envs = f.workspaces.list_environments(org.id, ws.id)
    # regression: a fresh workspace always has a dev environment to build in
    assert [e.name for e in envs] == ["dev"]


def test_create_project_enforces_slug_and_uniqueness():
    f = _foundation()
    owner, org = _bootstrap_org(f)
    ws = f.workspaces.create_workspace(owner.id, CreateWorkspaceRequest(org.id, "W", "w"))
    proj = f.projects.create_project(
        owner.id, CreateProjectRequest(org.id, ws.id, "Screening", "screening")
    )
    assert f.projects.list_projects(org.id, ws.id) == (proj,)
    # regression: an invalid slug fails fast with diagnostics
    with pytest.raises(FoundationValidationError):
        f.projects.create_project(
            owner.id, CreateProjectRequest(org.id, ws.id, "Bad", "Bad Slug!")
        )
    # regression: a duplicate slug in the same workspace is rejected
    with pytest.raises(ConflictError):
        f.projects.create_project(
            owner.id, CreateProjectRequest(org.id, ws.id, "Dup", "screening")
        )


# ── RBAC + authorization ─────────────────────────────────────────────────────────


def test_permission_map_covers_all_eleven_roles():
    # regression: every ADR-0021 role has an explicit permission set
    assert all(role in permissions_for.__globals__["ROLE_PERMISSIONS"] for role in Role)
    assert Permission.BILLING_MANAGE in permissions_for(Role.ORG_OWNER)
    assert Permission.BILLING_MANAGE not in permissions_for(Role.DEVELOPER)
    assert permissions_for(Role.READ_ONLY) and Permission.ORG_MANAGE not in permissions_for(
        Role.READ_ONLY
    )


def test_workspace_scoped_role_does_not_leak_across_workspaces():
    f = _foundation()
    owner, org = _bootstrap_org(f)
    dev = f.identity.register_user(RegisterUserRequest("dev@acme.test", "Dev"))
    f.membership.invite_member(owner.id, InviteMemberRequest(org.id, dev.id))
    ws_a = f.workspaces.create_workspace(owner.id, CreateWorkspaceRequest(org.id, "A", "a"))
    ws_b = f.workspaces.create_workspace(owner.id, CreateWorkspaceRequest(org.id, "B", "b"))
    f.access.grant_role(
        owner.id,
        GrantRoleRequest(org.id, dev.id, Role.DEVELOPER, RoleScope.WORKSPACE, ws_a.id),
    )
    # regression: the workspace-scoped grant authorizes ws_a but NOT ws_b
    assert f.access.can(dev.id, Permission.PROJECT_MANAGE, org_id=org.id, workspace_id=ws_a.id)
    assert not f.access.can(
        dev.id, Permission.PROJECT_MANAGE, org_id=org.id, workspace_id=ws_b.id
    )


def test_service_denies_unauthorized_actor():
    f = _foundation()
    owner, org = _bootstrap_org(f)
    outsider = f.identity.register_user(RegisterUserRequest("nobody@acme.test", "N"))
    f.membership.invite_member(owner.id, InviteMemberRequest(org.id, outsider.id))
    # regression: a member with no role cannot create a workspace
    with pytest.raises(AuthorizationError):
        f.workspaces.create_workspace(
            outsider.id, CreateWorkspaceRequest(org.id, "X", "x")
        )


def test_org_owner_org_binding_authorizes_any_workspace():
    f = _foundation()
    owner, org = _bootstrap_org(f)
    ws = f.workspaces.create_workspace(owner.id, CreateWorkspaceRequest(org.id, "W", "w"))
    # an ORGANIZATION binding covers every workspace in the org
    assert f.access.can(owner.id, Permission.PROJECT_MANAGE, org_id=org.id, workspace_id=ws.id)


# ── organization isolation ───────────────────────────────────────────────────────


def test_cannot_grant_role_into_foreign_org_workspace():
    f = _foundation()
    owner_a, org_a = _bootstrap_org(f)
    owner_b = f.identity.register_user(RegisterUserRequest("b@b.test", "B"))
    org_b = f.identity.create_organization(
        CreateOrganizationRequest(name="Beta", owner_user_id=owner_b.id)
    )
    ws_b = f.workspaces.create_workspace(owner_b.id, CreateWorkspaceRequest(org_b.id, "B", "b"))
    member = f.identity.register_user(RegisterUserRequest("m@acme.test", "M"))
    f.membership.invite_member(owner_a.id, InviteMemberRequest(org_a.id, member.id))
    # regression: granting a role in org_a that targets org_b's workspace is rejected
    with pytest.raises(OrganizationIsolationError):
        f.access.grant_role(
            owner_a.id,
            GrantRoleRequest(org_a.id, member.id, Role.DEVELOPER, RoleScope.WORKSPACE, ws_b.id),
        )


def test_workspace_lookup_is_org_scoped():
    f = _foundation()
    owner_a, org_a = _bootstrap_org(f)
    owner_b = f.identity.register_user(RegisterUserRequest("b2@b.test", "B"))
    org_b = f.identity.create_organization(
        CreateOrganizationRequest(name="Beta", owner_user_id=owner_b.id)
    )
    ws_b = f.workspaces.create_workspace(owner_b.id, CreateWorkspaceRequest(org_b.id, "B", "b"))
    # regression: org_a can never see org_b's workspace, even by id
    assert f.workspaces.list_workspaces(org_a.id) == ()
    assert f.workspaces._workspaces.get(org_a.id, ws_b.id) is None  # type: ignore[attr-defined]


# ── events / audit ───────────────────────────────────────────────────────────────


def test_mutations_emit_typed_events_including_access_denied():
    f = _foundation()
    owner, org = _bootstrap_org(f)
    outsider = f.identity.register_user(RegisterUserRequest("o@acme.test", "O"))
    f.membership.invite_member(owner.id, InviteMemberRequest(org.id, outsider.id))
    with pytest.raises(AuthorizationError):
        f.workspaces.create_workspace(outsider.id, CreateWorkspaceRequest(org.id, "X", "x"))
    types = [e.type.value for e in f.emitter.events]  # type: ignore[attr-defined]
    assert "organization.created" in types
    assert "member.invited" in types
    # regression: a denied action is audited, never silent
    assert "access.denied" in types


# ── feature flags ────────────────────────────────────────────────────────────────


def test_feature_flag_resolution_precedence_is_deterministic():
    f = _foundation()
    flags = (
        FeatureFlag("beta", enabled=False, scope=SettingScope.ORGANIZATION, scope_id="org_1"),
        FeatureFlag("beta", enabled=True, scope=SettingScope.WORKSPACE, scope_id="ws_1"),
    )
    state = f.configuration.resolve_flag("beta", flags)
    # regression: workspace scope wins over organization scope
    assert state.enabled is True and state.decided_by is SettingScope.WORKSPACE
    # unknown key falls back to the default, provenance None
    unknown = f.configuration.resolve_flag("missing", flags, default=False)
    assert unknown.enabled is False and unknown.decided_by is None


# ── authorizer seam bridge ───────────────────────────────────────────────────────


def test_rbac_authorizer_bridges_tenant_seam():
    f = _foundation()
    owner, org = _bootstrap_org(f)
    tenant = TenantContext(org=OrganizationContext(org_id=org.id), user_id=owner.id)
    decision = f.authorizer.authorize(tenant, Permission.WORKSPACE_MANAGE.value, "")
    assert decision.effect is Effect.ALLOW and decision.allowed
    # unknown action denies; a subject with no matching binding denies
    assert f.authorizer.authorize(tenant, "bogus.action", "").effect is Effect.DENY


# ── preferences (Consumer-owned) ─────────────────────────────────────────────────


def test_preferences_default_dark_and_roundtrip():
    f = _foundation()
    owner, org = _bootstrap_org(f)
    default = f.configuration.get_preferences(org.id, owner.id)
    assert default.theme == "dark"  # Blueprint §19 default
    saved = f.configuration.update_preferences(
        UpdatePreferencesRequest(owner.id, org.id, theme="light", favorites=("agt_x",))
    )
    assert saved.theme == "light"
    assert f.configuration.get_preferences(org.id, owner.id).favorites == ("agt_x",)


# ── immutability + determinism ───────────────────────────────────────────────────


def test_aggregates_are_immutable():
    f = _foundation()
    _, org = _bootstrap_org(f)
    with pytest.raises(FrozenInstanceError):
        org.name = "mutated"  # type: ignore[misc]


def test_construction_is_deterministic_under_injected_seams():
    def build():
        f = FoundationFactory.default(ids=SequentialIdGenerator(), clock=FixedClock())
        owner, org = _bootstrap_org(f)
        return org.id, owner.id, [e.type.value for e in f.emitter.events]  # type: ignore[attr-defined]

    # regression: identical seams → identical ids and event stream
    assert build() == build()


# ── dependency boundary (acyclic; frozen Epics untouched) ────────────────────────


def _imports(pkg_path: str) -> set[str]:
    tops: set[str] = set()
    for p in pathlib.Path(pkg_path).rglob("*.py"):
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.ImportFrom) and n.module and not n.level:
                tops.add(n.module.split(".")[0])
            elif isinstance(n, ast.Import):
                for a in n.names:
                    tops.add(a.name.split(".")[0])
    return tops


def test_foundation_imports_only_core_and_tenant():
    forbidden = {
        # frozen runtime / execution / product packages
        "runtime", "gateway", "replay", "events", "memory", "knowledge", "context",
        "prompt", "reasoning", "provider", "providers", "bridge", "pipeline",
        "product", "tool", "skill", "workflow", "agent", "loop", "mcp", "hooks",
        "integration", "observability", "voicebot",
        # the domain layer must not depend on the web framework or ORM
        "fastapi", "sqlmodel", "pydantic",
    }
    used = _imports("api/foundation")
    # regression: foundation domain imports nothing frozen and no web/ORM framework
    assert not (used & forbidden), used & forbidden
    # and it does use exactly its allowed dependency floor
    assert {"core", "tenant"} <= used
