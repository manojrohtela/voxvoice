"""Workspace Lifecycle vertical slice (Epic 8, M8.7) tests.

Covers every layer end-to-end: Foundation unit behaviour (rename/settings/archive/restore,
name uniqueness, workspace membership + tier bindings, navigation), and the full Control
Plane API + persistence integration — the Definition-of-Done flow (open org → create
multiple workspaces → switch → manage members → configure settings → view activity) plus
authorization failures, cross-workspace + cross-org isolation, and archive/restore. The
Studio UI is a static bundle served at /studio (verified reachable); its behaviour is
exercised through these API paths.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.customer import Container, CustomerApiSettings, create_app
from api.foundation import (
    ConflictError,
    CreateOrganizationRequest,
    CreateWorkspaceRequest,
    FixedClock,
    FoundationFactory,
    OrganizationIsolationError,
    RegisterUserRequest,
    SequentialIdGenerator,
    WorkspaceStatus,
    WsRole,
)

# ── Foundation unit (in-memory wiring) ───────────────────────────────────────────


def _foundation():
    return FoundationFactory.default(ids=SequentialIdGenerator(), clock=FixedClock())


def _user(f, email):
    return f.identity.register_user(RegisterUserRequest(email, "U"))


def _org(f):
    owner = _user(f, "owner@acme.io")
    org = f.identity.create_organization(
        CreateOrganizationRequest(name="Acme", owner_user_id=owner.id)
    )
    return owner, org


def _ws(f, owner, org, name="Team A", slug="team-a"):
    return f.workspaces.create_workspace(
        owner.id, CreateWorkspaceRequest(org_id=org.id, name=name, slug=slug)
    )


def test_update_settings_and_archive_restore():
    f = _foundation()
    owner, org = _org(f)
    ws = _ws(f, owner, org)
    updated = f.workspace_lifecycle.update_workspace(
        owner.id, org.id, ws.id, name="Team Alpha", description="squad", icon="🚀",
        color="#4c8bf5",
    )
    assert updated.name == "Team Alpha" and updated.icon == "🚀"
    archived = f.workspace_lifecycle.archive_workspace(owner.id, org.id, ws.id)
    assert archived.status is WorkspaceStatus.ARCHIVED
    # archiving twice is rejected; restore flips it back
    with pytest.raises(ConflictError):
        f.workspace_lifecycle.archive_workspace(owner.id, org.id, ws.id)
    restored = f.workspace_lifecycle.restore_workspace(owner.id, org.id, ws.id)
    assert restored.status is WorkspaceStatus.ACTIVE


def test_workspace_name_uniqueness_within_org():
    f = _foundation()
    owner, org = _org(f)
    _ws(f, owner, org, name="Alpha", slug="alpha")
    other = _ws(f, owner, org, name="Beta", slug="beta")
    with pytest.raises(ConflictError):
        f.workspace_lifecycle.update_workspace(owner.id, org.id, other.id, name="Alpha")


def test_membership_add_grants_ws_binding_and_remove_revokes():
    f = _foundation()
    owner, org = _org(f)
    ws = _ws(f, owner, org)
    member = _user(f, "dev@acme.io")
    f.invitations.invite(owner.id, org.id, member.id)
    f.invitations.accept(member.id, org.id, member.id)
    f.workspace_members.add_member(owner.id, org.id, ws.id, member.id, role=WsRole.ADMIN)
    roles = [b.role.value for b in f.access.roles_of(org.id, member.id)]
    assert "workspace_admin" in roles
    assert [m.user_id for m in f.workspace_members.list_members(org.id, ws.id)] == [member.id]
    # removing revokes the workspace-scoped binding and drops the member from the list
    f.workspace_members.remove_member(owner.id, org.id, ws.id, member.id)
    roles_after = [b.role.value for b in f.access.roles_of(org.id, member.id)]
    assert "workspace_admin" not in roles_after
    assert f.workspace_members.list_members(org.id, ws.id) == ()


def test_only_org_members_can_join_a_workspace():
    f = _foundation()
    owner, org = _org(f)
    ws = _ws(f, owner, org)
    stranger = _user(f, "stranger@other.io")  # never an org member
    with pytest.raises(OrganizationIsolationError):
        f.workspace_members.add_member(owner.id, org.id, ws.id, stranger.id)


def test_navigation_default_favorite_recent_search():
    f = _foundation()
    owner, org = _org(f)
    a = _ws(f, owner, org, name="Alpha", slug="alpha")
    b = _ws(f, owner, org, name="Beta", slug="beta")
    f.workspace_navigation.set_default(org.id, owner.id, a.id)
    assert f.workspace_navigation.toggle_favorite(org.id, owner.id, b.id) is True
    f.workspace_navigation.record_recent(org.id, owner.id, b.id)
    nav = f.workspace_navigation.navigation(org.id, owner.id)
    assert nav["default_workspace_id"] == a.id
    assert nav["favorites"] == [b.id] and nav["recent"] == [b.id]
    assert {w.id for w in nav["workspaces"]} == {a.id, b.id}
    # search is case-insensitive substring on name/slug
    assert [w.name for w in f.workspace_lifecycle.search(org.id, "ph")] == ["Alpha"]


# ── API + persistence integration (end-to-end) ───────────────────────────────────


def _client() -> TestClient:
    settings = CustomerApiSettings(environment="testing", database_url="sqlite://")
    container = Container.build(settings, ids=SequentialIdGenerator(), clock=FixedClock())
    return TestClient(create_app(container=container))


def _register(c, email):
    return c.post("/v1/auth/register", json={"email": email, "display_name": "U"}).json()["id"]


def _auth(c, uid):
    tok = c.post("/v1/auth/token", json={"user_id": uid}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def _make_org_member(c, oid, owner_headers, email):
    """Register a user, invite+accept them into the org, return their id."""
    uid = _register(c, email)
    c.post(f"/v1/organizations/{oid}/invitations",
           json={"user_id": uid, "tier": "member"}, headers=owner_headers)
    c.post(f"/v1/organizations/{oid}/invitations/{uid}/accept", headers=_auth(c, uid))
    return uid


def test_definition_of_done_end_to_end():
    with _client() as c:
        # 1. open an organization
        owner = _register(c, "founder@acme.io")
        oh = _auth(c, owner)
        oid = c.post("/v1/organizations", json={"name": "Acme"}, headers=oh).json()["id"]
        base = f"/v1/organizations/{oid}/workspaces"
        # 2. create multiple workspaces
        a = c.post(base, json={"name": "Alpha", "slug": "alpha"}, headers=oh).json()
        b = c.post(base, json={"name": "Beta", "slug": "beta"}, headers=oh).json()
        assert a["status"] == "active" and b["id"] != a["id"]
        # 3. switch between workspaces (navigation switcher + recent)
        c.post(f"/v1/organizations/{oid}/workspace-navigation/{a['id']}/recent", headers=oh)
        nav = c.get(f"/v1/organizations/{oid}/workspace-navigation", headers=oh).json()
        assert {w["id"] for w in nav["workspaces"]} == {a["id"], b["id"]}
        assert nav["recent"] == [a["id"]]
        # 4. manage workspace members (add an org member to workspace Alpha)
        member = _make_org_member(c, oid, oh, "teammate@acme.io")
        add = c.post(f"{base}/{a['id']}/members",
                     json={"user_id": member, "role": "admin"}, headers=oh)
        assert add.status_code == 201 and add.json()["role"] == "admin"
        members = c.get(f"{base}/{a['id']}/members", headers=oh).json()
        assert {m["user_id"] for m in members} == {member}
        # 5. configure workspace settings
        upd = c.patch(f"{base}/{a['id']}",
                      json={"name": "Alpha Squad", "description": "core", "icon": "🚀",
                            "color": "#4c8bf5"}, headers=oh)
        assert upd.status_code == 200 and upd.json()["description"] == "core"
        # 6. view workspace activity
        act = c.get(f"{base}/{a['id']}/activity", headers=oh).json()
        types = [f["type"] for f in act["feed"]]
        assert "workspace.created" in types and "workspace.member_added" in types
        assert act["summary"]["total_members"] == 1


def test_authorization_failure_for_viewer():
    with _client() as c:
        owner = _register(c, "o2@acme.io")
        oh = _auth(c, owner)
        oid = c.post("/v1/organizations", json={"name": "A"}, headers=oh).json()["id"]
        base = f"/v1/organizations/{oid}/workspaces"
        ws = c.post(base, json={"name": "W", "slug": "w"}, headers=oh).json()
        # a plain org viewer lacks WORKSPACE_MANAGE → cannot patch a workspace
        viewer = _register(c, "viewer@acme.io")
        c.post(f"/v1/organizations/{oid}/invitations",
               json={"user_id": viewer, "tier": "viewer"}, headers=oh)
        vh = _auth(c, viewer)
        c.post(f"/v1/organizations/{oid}/invitations/{viewer}/accept", headers=vh)
        r = c.patch(f"{base}/{ws['id']}", json={"name": "Hacked"}, headers=vh)
        assert r.status_code == 403


def test_cross_org_workspace_isolation():
    with _client() as c:
        a = _register(c, "a@x.io")
        ah = _auth(c, a)
        org_a = c.post("/v1/organizations", json={"name": "A"}, headers=ah).json()["id"]
        ws_a = c.post(f"/v1/organizations/{org_a}/workspaces",
                      json={"name": "WA", "slug": "wa"}, headers=ah).json()["id"]
        b = _register(c, "b@x.io")
        bh = _auth(c, b)
        c.post("/v1/organizations", json={"name": "B"}, headers=bh)
        # user B is not a member of org A and cannot manage its workspace → 403
        r = c.patch(f"/v1/organizations/{org_a}/workspaces/{ws_a}",
                    json={"name": "X"}, headers=bh)
        assert r.status_code == 403
        # and A's workspace is unaffected
        assert c.get(f"/v1/organizations/{org_a}/workspaces/{ws_a}",
                     headers=ah).json()["name"] == "WA"


def test_cross_workspace_member_isolation():
    with _client() as c:
        owner = _register(c, "iso@acme.io")
        oh = _auth(c, owner)
        oid = c.post("/v1/organizations", json={"name": "Acme"}, headers=oh).json()["id"]
        base = f"/v1/organizations/{oid}/workspaces"
        a = c.post(base, json={"name": "A", "slug": "a"}, headers=oh).json()["id"]
        b = c.post(base, json={"name": "B", "slug": "b"}, headers=oh).json()["id"]
        member = _make_org_member(c, oid, oh, "m@acme.io")
        c.post(f"{base}/{a}/members", json={"user_id": member, "role": "member"}, headers=oh)
        # the member of workspace A is NOT a member of workspace B
        assert c.get(f"{base}/{a}/members", headers=oh).json()[0]["user_id"] == member
        assert c.get(f"{base}/{b}/members", headers=oh).json() == []


def test_archive_hides_from_switcher_and_search():
    with _client() as c:
        owner = _register(c, "d@acme.io")
        oh = _auth(c, owner)
        oid = c.post("/v1/organizations", json={"name": "Acme"}, headers=oh).json()["id"]
        base = f"/v1/organizations/{oid}/workspaces"
        ws = c.post(base, json={"name": "Gamma", "slug": "gamma"}, headers=oh).json()["id"]
        assert c.post(f"{base}/{ws}/archive", headers=oh).json()["status"] == "archived"
        # archived workspaces drop out of the switcher and default search
        nav = c.get(f"/v1/organizations/{oid}/workspace-navigation", headers=oh).json()
        assert nav["workspaces"] == []
        assert c.get(f"{base}/search?q=gamma", headers=oh).json() == []
        # restore brings it back
        assert c.post(f"{base}/{ws}/restore", headers=oh).json()["status"] == "active"
        nav2 = c.get(f"/v1/organizations/{oid}/workspace-navigation", headers=oh).json()
        assert {w["id"] for w in nav2["workspaces"]} == {ws}


def test_studio_ui_serves_workspace_screens():
    with _client() as c:
        r = c.get("/studio/")
        assert r.status_code == 200 and "VoxVoice Studio" in r.text
        # the workspace screens are present in the served bundle
        assert "Create workspace" in r.text and "Workspace members" in r.text
