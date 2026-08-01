"""Customer Platform Application Layer (Epic 8, M8.3) tests.

Verifies the CQRS orchestration layer over the M8.2 Foundation: command/query dispatch
through the buses; use-case coordination (authorize → transaction → Foundation call →
map → application event) without duplicating domain logic; the Unit of Work boundary
(application events publish on commit, are discarded on rollback); authorization
orchestration via the Foundation authorizer seam; cross-cutting audit (every command
yields use_case.completed / use_case.rejected); DTO mapping to view DTOs; that the
Foundation still emits its own domain events (no duplication, distinct altitude); and the
acyclic `api.application → api.foundation, core, tenant` dependency rule (no FastAPI/HTTP/
DB/frozen-runtime, and the API/transport layer does not exist yet).

Regression anchors are marked `# regression:`.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from api.application import ApplicationEventType as AET
from api.application import (
    ApplicationFactory,
    CreateOrganization,
    CreateProject,
    CreateWorkspace,
    GetOrganization,
    GrantRole,
    InviteMember,
    ListMembers,
    ListWorkspaces,
    RegisterUser,
    RemoveMember,
    UpdatePreferences,
)
from api.foundation import (
    AuthorizationError,
    ConflictError,
    FixedClock,
    MembershipStatus,
    Role,
    RoleScope,
    SequentialIdGenerator,
)
from api.foundation import FoundationEventType as FET


def _app():
    return ApplicationFactory.default(ids=SequentialIdGenerator(), clock=FixedClock())


def _bootstrap(app):
    owner = app.commands.dispatch(RegisterUser("owner@acme.test", "Owner"))
    org = app.commands.dispatch(CreateOrganization(name="Acme", owner_user_id=owner.id))
    return owner, org


# ── command dispatch + orchestration ─────────────────────────────────────────────


def test_bootstrap_via_command_bus_returns_view_dtos():
    app = _app()
    owner, org = _bootstrap(app)
    # regression: dispatch returns response view DTOs, not domain aggregates
    assert type(owner).__name__ == "UserView"
    assert type(org).__name__ == "OrganizationView"
    # and the query side reads it back
    got = app.queries.dispatch(GetOrganization(org.id))
    assert got is not None and got.id == org.id


def test_create_workspace_maps_and_provisions():
    app = _app()
    owner, org = _bootstrap(app)
    ws = app.commands.dispatch(CreateWorkspace(owner.id, org.id, "Web", "web"))
    listing = app.queries.dispatch(ListWorkspaces(org.id))
    assert [w.id for w in listing] == [ws.id]


# ── authorization orchestration ──────────────────────────────────────────────────


def test_unauthorized_command_is_rejected_and_audited():
    app = _app()
    owner, org = _bootstrap(app)
    outsider = app.commands.dispatch(RegisterUser("x@acme.test", "X"))
    app.commands.dispatch(InviteMember(owner.id, org.id, outsider.id))
    # regression: a member with no role cannot create a workspace (authz orchestration)
    with pytest.raises(AuthorizationError):
        app.commands.dispatch(CreateWorkspace(outsider.id, org.id, "Nope", "nope"))
    # regression: the rejection is audited (survives the rolled-back transaction).
    # The cross-cutting bus audit records the command type name.
    rejected = app.emitter.of_type(AET.USE_CASE_REJECTED)
    assert any(e.use_case == "CreateWorkspace" for e in rejected)


def test_grant_role_then_authorized():
    app = _app()
    owner, org = _bootstrap(app)
    dev = app.commands.dispatch(RegisterUser("dev@acme.test", "Dev"))
    app.commands.dispatch(InviteMember(owner.id, org.id, dev.id))
    ws = app.commands.dispatch(CreateWorkspace(owner.id, org.id, "W", "w"))
    app.commands.dispatch(
        GrantRole(owner.id, org.id, dev.id, Role.DEVELOPER, RoleScope.WORKSPACE, ws.id)
    )
    # now the developer can create a project in that workspace
    proj = app.commands.dispatch(CreateProject(dev.id, org.id, ws.id, "P", "p"))
    assert proj.workspace_id == ws.id


# ── audit orchestration (cross-cutting) ──────────────────────────────────────────


def test_every_successful_command_emits_use_case_completed():
    app = _app()
    _bootstrap(app)
    completed = app.emitter.of_type(AET.USE_CASE_COMPLETED)
    names = {e.use_case for e in completed}
    # regression: both bootstrap commands produced a completion audit event
    # (the cross-cutting bus audit records the command type name)
    assert {"RegisterUser", "CreateOrganization"} <= names


# ── unit of work: commit vs rollback ─────────────────────────────────────────────


def test_semantic_events_publish_on_commit():
    app = _app()
    _bootstrap(app)
    # regression: the composite business event was published on commit
    provisioned = app.emitter.of_type(AET.ORGANIZATION_PROVISIONED)
    assert len(provisioned) == 1


def test_failed_command_discards_staged_events_but_keeps_rejection_audit():
    app = _app()
    owner, org = _bootstrap(app)
    # a duplicate slug makes the Foundation raise ConflictError inside the UoW
    app.commands.dispatch(CreateWorkspace(owner.id, org.id, "W", "dup"))
    before = len(app.emitter.of_type(AET.WORKSPACE_PROVISIONED))
    with pytest.raises(ConflictError):
        app.commands.dispatch(CreateWorkspace(owner.id, org.id, "W2", "dup"))
    after = len(app.emitter.of_type(AET.WORKSPACE_PROVISIONED))
    # regression: the rolled-back use case staged NO new semantic event...
    assert after == before
    # ...but the rejection audit was still emitted
    assert app.emitter.of_type(AET.USE_CASE_REJECTED)


# ── no duplication: Foundation still owns domain events ───────────────────────────


def test_foundation_domain_events_still_fire_and_are_distinct():
    app = _app()
    _bootstrap(app)
    foundation_types = {e.type for e in app.foundation.emitter.events}  # type: ignore[attr-defined]
    app_types = {e.type for e in app.emitter.events}  # type: ignore[attr-defined]
    # regression: Foundation emits its own domain vocabulary...
    assert FET.ORGANIZATION_CREATED in foundation_types
    # ...and the application vocabulary is disjoint (no duplication)
    assert AET.ORGANIZATION_PROVISIONED in app_types
    assert {t.value for t in foundation_types}.isdisjoint({t.value for t in app_types})


# ── soft remove maps to Foundation status transition ─────────────────────────────


def test_remove_member_soft_removes_via_suspend():
    app = _app()
    owner, org = _bootstrap(app)
    member = app.commands.dispatch(RegisterUser("m@acme.test", "M"))
    app.commands.dispatch(InviteMember(owner.id, org.id, member.id))
    app.commands.dispatch(RemoveMember(owner.id, org.id, member.id))
    members = {v.user_id: v for v in app.queries.dispatch(ListMembers(org.id))}
    # regression: removal is modeled as SUSPENDED (frozen Foundation, no hard delete)
    assert members[member.id].status == MembershipStatus.SUSPENDED.value


# ── preferences self-service ─────────────────────────────────────────────────────


def test_update_preferences_roundtrip():
    app = _app()
    owner, org = _bootstrap(app)
    saved = app.commands.dispatch(
        UpdatePreferences(owner.id, org.id, theme="light", favorites=("agt_1",))
    )
    assert saved.theme == "light" and saved.favorites == ("agt_1",)


# ── determinism ──────────────────────────────────────────────────────────────────


def test_dispatch_is_deterministic_under_shared_seams():
    def run():
        app = ApplicationFactory.default(ids=SequentialIdGenerator(), clock=FixedClock())
        owner, org = _bootstrap(app)
        return org.id, [(e.type.value, e.use_case) for e in app.emitter.events]  # type: ignore[attr-defined]

    # regression: identical seams → identical ids and application event stream
    assert run() == run()


# ── dependency boundary (acyclic; Foundation/frozen Epics untouched) ─────────────


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


def test_application_imports_only_foundation_core_tenant():
    forbidden = {
        "runtime", "gateway", "replay", "events", "memory", "knowledge", "context",
        "prompt", "reasoning", "provider", "providers", "bridge", "pipeline",
        "product", "tool", "skill", "workflow", "agent", "loop", "mcp", "hooks",
        "integration", "observability", "voicebot",
        "fastapi", "sqlmodel", "pydantic", "starlette", "httpx",
    }
    used = _imports("api/application")
    # regression: no HTTP/DB/frozen-runtime import; the API/transport layer does not exist
    assert not (used & forbidden), used & forbidden
    assert {"api", "tenant"} <= used  # imports the Foundation (api.foundation) + tenant


def test_no_http_router_layer_exists_yet():
    # regression: M8.3 is application-only — no api/routers file references the app layer
    routers = pathlib.Path("api/routers")
    if routers.exists():
        for p in routers.rglob("*.py"):
            assert "application" not in p.read_text(), f"{p} wired the app layer prematurely"
