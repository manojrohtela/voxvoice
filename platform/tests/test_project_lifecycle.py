"""Project Lifecycle vertical slice (Epic 8, M8.8) tests.

Covers every layer end-to-end: Foundation unit behaviour (rename/settings/archive/restore,
name uniqueness, project membership + org-member-only isolation, per-user environment
selection/switching), and the full Control Plane API + persistence integration — the
Definition-of-Done flow (open workspace → create multiple projects → configure settings →
manage members → switch environments → view activity) plus authorization failures,
cross-workspace + cross-project isolation, and archive/restore. The Studio UI is a static
bundle served at /studio (verified reachable); its behaviour is exercised through these API
paths.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.customer import Container, CustomerApiSettings, create_app
from api.foundation import (
    ConflictError,
    CreateOrganizationRequest,
    CreateProjectRequest,
    CreateWorkspaceRequest,
    FixedClock,
    FoundationFactory,
    FoundationValidationError,
    OrganizationIsolationError,
    ProjectRole,
    ProjectStatus,
    RegisterUserRequest,
    SequentialIdGenerator,
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


def _proj(f, owner, org, ws, name="Proj A", slug="proj-a"):
    return f.projects.create_project(
        owner.id, CreateProjectRequest(org_id=org.id, workspace_id=ws.id, name=name, slug=slug)
    )


def test_update_settings_and_archive_restore():
    f = _foundation()
    owner, org = _org(f)
    ws = _ws(f, owner, org)
    p = _proj(f, owner, org, ws)
    updated = f.project_lifecycle.update_project(
        owner.id, org.id, ws.id, p.id, name="Proj Alpha", description="squad", icon="📦",
    )
    assert updated.name == "Proj Alpha" and updated.icon == "📦"
    archived = f.project_lifecycle.archive_project(owner.id, org.id, ws.id, p.id)
    assert archived.status is ProjectStatus.ARCHIVED
    with pytest.raises(ConflictError):
        f.project_lifecycle.archive_project(owner.id, org.id, ws.id, p.id)
    restored = f.project_lifecycle.restore_project(owner.id, org.id, ws.id, p.id)
    assert restored.status is ProjectStatus.ACTIVE


def test_project_name_uniqueness_within_workspace():
    f = _foundation()
    owner, org = _org(f)
    ws = _ws(f, owner, org)
    _proj(f, owner, org, ws, name="Alpha", slug="alpha")
    other = _proj(f, owner, org, ws, name="Beta", slug="beta")
    with pytest.raises(ConflictError):
        f.project_lifecycle.update_project(owner.id, org.id, ws.id, other.id, name="Alpha")


def test_membership_add_remove_and_org_member_only():
    f = _foundation()
    owner, org = _org(f)
    ws = _ws(f, owner, org)
    p = _proj(f, owner, org, ws)
    member = _user(f, "dev@acme.io")
    f.invitations.invite(owner.id, org.id, member.id)
    f.invitations.accept(member.id, org.id, member.id)
    f.project_members.add_member(owner.id, org.id, ws.id, p.id, member.id, role=ProjectRole.ADMIN)
    assert [m.user_id for m in f.project_members.list_members(org.id, p.id)] == [member.id]
    f.project_members.remove_member(owner.id, org.id, ws.id, p.id, member.id)
    assert f.project_members.list_members(org.id, p.id) == ()
    # a non-org-member cannot be added to a project (isolation)
    stranger = _user(f, "stranger@other.io")
    with pytest.raises(OrganizationIsolationError):
        f.project_members.add_member(owner.id, org.id, ws.id, p.id, stranger.id)


def test_environment_selection_and_switching():
    f = _foundation()
    owner, org = _org(f)
    ws = _ws(f, owner, org)
    p = _proj(f, owner, org, ws)
    assert f.project_environments.environments() == ("development", "staging", "production")
    # default is development; switching persists per-user
    assert f.project_environments.get_environment(org.id, owner.id, p.id) == "development"
    f.project_environments.set_environment(org.id, owner.id, ws.id, p.id, "production")
    assert f.project_environments.get_environment(org.id, owner.id, p.id) == "production"
    sel = f.project_environments.selector(org.id, owner.id, p.id)
    assert sel["current"] == "production" and len(sel["environments"]) == 3
    # an unknown environment is rejected
    with pytest.raises(FoundationValidationError):
        f.project_environments.set_environment(org.id, owner.id, ws.id, p.id, "chaos")


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


def _org_and_ws(c, oh):
    oid = c.post("/v1/organizations", json={"name": "Acme"}, headers=oh).json()["id"]
    wid = c.post(f"/v1/organizations/{oid}/workspaces",
                 json={"name": "Team", "slug": "team"}, headers=oh).json()["id"]
    return oid, wid


def _make_org_member(c, oid, owner_headers, email):
    uid = _register(c, email)
    c.post(f"/v1/organizations/{oid}/invitations",
           json={"user_id": uid, "tier": "member"}, headers=owner_headers)
    c.post(f"/v1/organizations/{oid}/invitations/{uid}/accept", headers=_auth(c, uid))
    return uid


def test_definition_of_done_end_to_end():
    with _client() as c:
        # 1. open a workspace
        owner = _register(c, "founder@acme.io")
        oh = _auth(c, owner)
        oid, wid = _org_and_ws(c, oh)
        base = f"/v1/organizations/{oid}/workspaces/{wid}/projects"
        # 2. create multiple projects
        a = c.post(base, json={"name": "Alpha", "slug": "alpha"}, headers=oh).json()
        b = c.post(base, json={"name": "Beta", "slug": "beta"}, headers=oh).json()
        assert a["status"] == "active" and b["id"] != a["id"]
        assert len(c.get(base, headers=oh).json()) == 2
        # 3. configure project settings
        upd = c.patch(f"{base}/{a['id']}",
                      json={"name": "Alpha One", "description": "core", "icon": "📦"},
                      headers=oh)
        assert upd.status_code == 200 and upd.json()["description"] == "core"
        # 4. manage project members
        member = _make_org_member(c, oid, oh, "teammate@acme.io")
        add = c.post(f"{base}/{a['id']}/members",
                     json={"user_id": member, "role": "admin"}, headers=oh)
        assert add.status_code == 201 and add.json()["role"] == "admin"
        pms = c.get(f"{base}/{a['id']}/members", headers=oh).json()
        assert {m["user_id"] for m in pms} == {member}
        # 5. switch environments
        env = c.post(f"{base}/{a['id']}/environment",
                     json={"environment": "production"}, headers=oh)
        assert env.status_code == 200 and env.json()["environment"] == "production"
        assert c.get(f"{base}/{a['id']}/environment", headers=oh).json()["current"] == "production"
        # 6. view project activity
        act = c.get(f"{base}/{a['id']}/activity", headers=oh).json()
        types = [f["type"] for f in act["feed"]]
        assert "project.created" in types and "project.member_added" in types
        assert act["summary"]["total_members"] == 1


def test_authorization_failure_for_viewer():
    with _client() as c:
        owner = _register(c, "o2@acme.io")
        oh = _auth(c, owner)
        oid, wid = _org_and_ws(c, oh)
        base = f"/v1/organizations/{oid}/workspaces/{wid}/projects"
        proj = c.post(base, json={"name": "P", "slug": "p"}, headers=oh).json()
        # a plain org viewer lacks PROJECT_MANAGE → cannot patch a project
        viewer = _register(c, "viewer@acme.io")
        c.post(f"/v1/organizations/{oid}/invitations",
               json={"user_id": viewer, "tier": "viewer"}, headers=oh)
        vh = _auth(c, viewer)
        c.post(f"/v1/organizations/{oid}/invitations/{viewer}/accept", headers=vh)
        r = c.patch(f"{base}/{proj['id']}", json={"name": "Hacked"}, headers=vh)
        assert r.status_code == 403


def test_cross_workspace_project_isolation():
    with _client() as c:
        owner = _register(c, "iso@acme.io")
        oh = _auth(c, owner)
        oid = c.post("/v1/organizations", json={"name": "Acme"}, headers=oh).json()["id"]
        w1 = c.post(f"/v1/organizations/{oid}/workspaces",
                    json={"name": "W1", "slug": "w1"}, headers=oh).json()["id"]
        w2 = c.post(f"/v1/organizations/{oid}/workspaces",
                    json={"name": "W2", "slug": "w2"}, headers=oh).json()["id"]
        p1 = c.post(f"/v1/organizations/{oid}/workspaces/{w1}/projects",
                    json={"name": "P1", "slug": "p1"}, headers=oh).json()["id"]
        # project p1 belongs to w1, not w2 → patching it under w2's path is not found
        r = c.patch(f"/v1/organizations/{oid}/workspaces/{w2}/projects/{p1}",
                    json={"name": "X"}, headers=oh)
        assert r.status_code == 404
        # and it is not listed under w2
        assert c.get(f"/v1/organizations/{oid}/workspaces/{w2}/projects", headers=oh).json() == []


def test_cross_project_member_isolation():
    with _client() as c:
        owner = _register(c, "cp@acme.io")
        oh = _auth(c, owner)
        oid, wid = _org_and_ws(c, oh)
        base = f"/v1/organizations/{oid}/workspaces/{wid}/projects"
        a = c.post(base, json={"name": "A", "slug": "a"}, headers=oh).json()["id"]
        b = c.post(base, json={"name": "B", "slug": "b"}, headers=oh).json()["id"]
        member = _make_org_member(c, oid, oh, "m@acme.io")
        c.post(f"{base}/{a}/members", json={"user_id": member, "role": "member"}, headers=oh)
        # the member of project A is NOT a member of project B
        assert c.get(f"{base}/{a}/members", headers=oh).json()[0]["user_id"] == member
        assert c.get(f"{base}/{b}/members", headers=oh).json() == []


def test_cross_org_project_isolation():
    with _client() as c:
        a = _register(c, "a@x.io")
        ah = _auth(c, a)
        oid_a, wid_a = _org_and_ws(c, ah)
        pid_a = c.post(f"/v1/organizations/{oid_a}/workspaces/{wid_a}/projects",
                       json={"name": "PA", "slug": "pa"}, headers=ah).json()["id"]
        b = _register(c, "b@x.io")
        bh = _auth(c, b)
        c.post("/v1/organizations", json={"name": "B"}, headers=bh)
        # user B is not a member of org A and cannot manage its project → 403
        r = c.patch(f"/v1/organizations/{oid_a}/workspaces/{wid_a}/projects/{pid_a}",
                    json={"name": "X"}, headers=bh)
        assert r.status_code == 403


def test_archive_hides_from_search():
    with _client() as c:
        owner = _register(c, "d@acme.io")
        oh = _auth(c, owner)
        oid, wid = _org_and_ws(c, oh)
        base = f"/v1/organizations/{oid}/workspaces/{wid}/projects"
        pid = c.post(base, json={"name": "Gamma", "slug": "gamma"}, headers=oh).json()["id"]
        assert c.post(f"{base}/{pid}/archive", headers=oh).json()["status"] == "archived"
        assert c.get(f"{base}/search?q=gamma", headers=oh).json() == []
        assert c.post(f"{base}/{pid}/restore", headers=oh).json()["status"] == "active"
        assert len(c.get(f"{base}/search?q=gamma", headers=oh).json()) == 1


def test_studio_ui_serves_project_screens():
    with _client() as c:
        r = c.get("/studio/")
        assert r.status_code == 200 and "VoxVoice Studio" in r.text
        assert "Create project" in r.text and "Project members" in r.text
