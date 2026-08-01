"""Tool Framework vertical slice (Epic 8, M8.11) tests.

Covers every layer end-to-end: Foundation unit behaviour (tool catalog create/update +
**version bump**, duplicate-name, validation incl. **circular reference**, enable/disable,
archive/restore/soft-delete; agent attach/detach/enable/disable/**reorder**/configure with
project isolation), Application, persistence durability across a restart, the full Control
Plane API (the Definition-of-Done flow), audit reuse, validation, and cross-project isolation.
The Studio bundle is served at /studio (verified reachable) — its screens are exercised through
these API paths. **No tool is ever executed.**
"""

from __future__ import annotations

import os
import tempfile

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
    NotFoundError,
    RegisterUserRequest,
    SequentialIdGenerator,
    ToolCategory,
    ToolConfig,
    ToolStatus,
    validate_tool,
)

# ── Foundation unit (in-memory wiring) ───────────────────────────────────────────


def _foundation():
    return FoundationFactory.default(ids=SequentialIdGenerator(), clock=FixedClock())


def _project(f):
    owner = f.identity.register_user(RegisterUserRequest("owner@acme.io", "U"))
    org = f.identity.create_organization(
        CreateOrganizationRequest(name="Acme", owner_user_id=owner.id)
    )
    ws = f.workspaces.create_workspace(
        owner.id, CreateWorkspaceRequest(org_id=org.id, name="Team", slug="team")
    )
    p = f.projects.create_project(
        owner.id, CreateProjectRequest(org_id=org.id, workspace_id=ws.id, name="P", slug="p")
    )
    return owner, org, ws, p


def test_create_update_and_version_bump():
    f = _foundation()
    owner, org, ws, p = _project(f)
    t = f.tools.create_tool(
        owner.id, org.id, ws.id, p.id, name="HTTP", category=ToolCategory.HTTP_API,
        config=ToolConfig(settings={"url": "x"}),
    )
    assert t.status is ToolStatus.PUBLISHED and t.version == 1 and t.enabled is True
    # a config change bumps the version; a metadata-only rename does not.
    v2 = f.tools.update_tool(
        owner.id, org.id, ws.id, p.id, t.id, config_patch={"timeout_ms": 9000}
    )
    assert v2.version == 2 and v2.config.timeout_ms == 9000
    v2b = f.tools.update_tool(owner.id, org.id, ws.id, p.id, t.id, description="desc")
    assert v2b.version == 2  # no config change → no bump


def test_duplicate_name_and_validation():
    f = _foundation()
    owner, org, ws, p = _project(f)
    f.tools.create_tool(owner.id, org.id, ws.id, p.id, name="HTTP")
    with pytest.raises(FoundationValidationError):
        f.tools.create_tool(owner.id, org.id, ws.id, p.id, name="HTTP")
    # bad category + bad timeout rejected by structural validation
    bad = validate_tool("A", "not_a_category", ToolConfig(timeout_ms=-1))
    assert bad.has_errors
    ok = validate_tool("A", "http_api", ToolConfig(timeout_ms=1000))
    assert not ok.has_errors


def test_circular_reference_rejected():
    f = _foundation()
    owner, org, ws, p = _project(f)
    a = f.tools.create_tool(owner.id, org.id, ws.id, p.id, name="A")
    b = f.tools.create_tool(
        owner.id, org.id, ws.id, p.id, name="B",
        config=ToolConfig(depends_on=(a.id,)),
    )
    # A depends on B, B depends on A → cycle
    with pytest.raises(FoundationValidationError):
        f.tools.update_tool(
            owner.id, org.id, ws.id, p.id, a.id, config_patch={"depends_on": [b.id]}
        )


def test_enable_disable_archive_restore_delete():
    f = _foundation()
    owner, org, ws, p = _project(f)
    t = f.tools.create_tool(owner.id, org.id, ws.id, p.id, name="HTTP")
    dis = f.tools.set_enabled(owner.id, org.id, ws.id, p.id, t.id, enabled=False)
    assert dis.enabled is False
    with pytest.raises(ConflictError):
        f.tools.set_enabled(owner.id, org.id, ws.id, p.id, t.id, enabled=False)
    f.tools.set_enabled(owner.id, org.id, ws.id, p.id, t.id, enabled=True)
    arch = f.tools.archive_tool(owner.id, org.id, ws.id, p.id, t.id)
    assert arch.status is ToolStatus.ARCHIVED
    f.tools.restore_tool(owner.id, org.id, ws.id, p.id, t.id)
    f.tools.delete_tool(owner.id, org.id, ws.id, p.id, t.id)
    with pytest.raises(NotFoundError):
        f.tools.get_tool(org.id, p.id, t.id)


def test_agent_attachment_reorder_and_isolation():
    f = _foundation()
    owner, org, ws, p = _project(f)
    agent = f.agent_builder.create_agent(owner.id, org.id, ws.id, p.id, name="Bot")
    t1 = f.tools.create_tool(owner.id, org.id, ws.id, p.id, name="HTTP")
    t2 = f.tools.create_tool(owner.id, org.id, ws.id, p.id, name="DB")
    f.agent_tools.attach_tool(owner.id, org.id, ws.id, p.id, agent.id, t1.id)
    f.agent_tools.attach_tool(owner.id, org.id, ws.id, p.id, agent.id, t2.id)
    # duplicate attach rejected
    with pytest.raises(ConflictError):
        f.agent_tools.attach_tool(owner.id, org.id, ws.id, p.id, agent.id, t1.id)
    ordered = f.agent_tools.reorder_tools(
        owner.id, org.id, ws.id, p.id, agent.id, (t2.id, t1.id)
    )
    assert [a.tool_id for a in ordered] == [t2.id, t1.id]
    dis = f.agent_tools.set_attachment_enabled(
        owner.id, org.id, ws.id, p.id, agent.id, t1.id, enabled=False
    )
    assert dis.enabled is False
    f.agent_tools.detach_tool(owner.id, org.id, ws.id, p.id, agent.id, t2.id)
    assert [a.tool_id for a in f.agent_tools.list_agent_tools(org.id, agent.id)] == [t1.id]
    # a tool from another project cannot be attached (isolation)
    p2 = f.projects.create_project(
        owner.id, CreateProjectRequest(org_id=org.id, workspace_id=ws.id, name="P2", slug="p2")
    )
    other = f.tools.create_tool(owner.id, org.id, ws.id, p2.id, name="Other")
    with pytest.raises(NotFoundError):
        f.agent_tools.attach_tool(owner.id, org.id, ws.id, p.id, agent.id, other.id)


# ── API + persistence integration (Definition of Done) ───────────────────────────


def _client(url: str = "sqlite://") -> TestClient:
    settings = CustomerApiSettings(environment="testing", database_url=url)
    container = Container.build(settings, ids=SequentialIdGenerator(), clock=FixedClock())
    return TestClient(create_app(container=container))


def _bootstrap(c):
    owner = c.post("/v1/auth/register", json={"email": "f@a.io", "display_name": "U"}).json()["id"]
    tok = c.post("/v1/auth/token", json={"user_id": owner}).json()["access_token"]
    oh = {"Authorization": f"Bearer {tok}"}
    oid = c.post("/v1/organizations", json={"name": "Acme"}, headers=oh).json()["id"]
    wid = c.post(
        f"/v1/organizations/{oid}/workspaces", json={"name": "T", "slug": "t"}, headers=oh
    ).json()["id"]
    pid = c.post(
        f"/v1/organizations/{oid}/workspaces/{wid}/projects",
        json={"name": "P", "slug": "p"}, headers=oh,
    ).json()["id"]
    return oh, oid, wid, pid


def test_definition_of_done_end_to_end():
    with _client() as c:
        oh, oid, wid, pid = _bootstrap(c)
        tbase = f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}/tools"
        # 1. create reusable tools
        t1 = c.post(tbase, json={"name": "Weather", "category": "http_api"}, headers=oh).json()
        t2 = c.post(tbase, json={"name": "Lookup", "category": "database"}, headers=oh).json()
        assert t1["status"] == "published" and len(c.get(tbase, headers=oh).json()) == 2
        # 2. configure a tool (typed data)
        cfg = {"config_patch": {"settings": {"url": "https://api"}, "timeout_ms": 5000}}
        upd = c.patch(f"{tbase}/{t1['id']}", json=cfg, headers=oh)
        assert upd.status_code == 200 and upd.json()["config"]["timeout_ms"] == 5000
        assert upd.json()["version"] == 2  # config change bumped the version
        # 3. attach tools to an Agent
        abase = f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}/agents"
        aid = c.post(abase, json={"name": "Bot"}, headers=oh).json()["id"]
        atb = f"{abase}/{aid}/tools"
        assert c.post(atb, json={"tool_id": t1["id"]}, headers=oh).status_code == 201
        c.post(atb, json={"tool_id": t2["id"]}, headers=oh)
        assert {x["tool_id"] for x in c.get(atb, headers=oh).json()} == {t1["id"], t2["id"]}
        # 4. enable/disable a tool (attachment level)
        dis = c.post(f"{atb}/{t1['id']}/disable", headers=oh)
        assert dis.status_code == 200 and dis.json()["enabled"] is False
        c.post(f"{atb}/{t1['id']}/enable", headers=oh)
        # 5. reorder tools
        ro = c.post(f"{atb}/reorder", json={"ordered_tool_ids": [t2["id"], t1["id"]]}, headers=oh)
        assert [x["tool_id"] for x in ro.json()] == [t2["id"], t1["id"]]
        # 6. view tool history
        act = c.get(f"{tbase}/{t1['id']}/activity", headers=oh).json()
        types = {f["type"] for f in act["feed"]}
        assert {"tool.created", "tool.updated"} <= types and act["total"] >= 2


def test_history_survives_restart_over_file_db():
    from api.application import GetTool, ListAgentTools
    from api.persistence import build_persistent_application

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    url = f"sqlite:///{path}"
    try:
        with _client(url) as c:
            oh, oid, wid, pid = _bootstrap(c)
            tbase = f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}/tools"
            t = c.post(tbase, json={"name": "HTTP", "category": "http_api"}, headers=oh).json()
            abase = f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}/agents"
            aid = c.post(abase, json={"name": "Bot"}, headers=oh).json()["id"]
            c.post(f"{abase}/{aid}/tools", json={"tool_id": t["id"]}, headers=oh)
            tid = t["id"]
        pa = build_persistent_application(
            url=url, ids=SequentialIdGenerator(), clock=FixedClock(), create_all=False
        )
        tool = pa.application.queries.dispatch(GetTool(oid, pid, tid))
        ats = pa.application.queries.dispatch(ListAgentTools(oid, aid))
        assert tool["name"] == "HTTP"
        assert [a["tool_id"] for a in ats] == [tid]
        pa.database.close()
    finally:
        os.unlink(path)


def test_authorization_failure_for_viewer():
    with _client() as c:
        oh, oid, wid, pid = _bootstrap(c)
        tbase = f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}/tools"
        viewer = c.post(
            "/v1/auth/register", json={"email": "v@a.io", "display_name": "V"}
        ).json()["id"]
        c.post(f"/v1/organizations/{oid}/invitations",
               json={"user_id": viewer, "tier": "viewer"}, headers=oh)
        vtok = c.post("/v1/auth/token", json={"user_id": viewer}).json()["access_token"]
        vh = {"Authorization": f"Bearer {vtok}"}
        c.post(f"/v1/organizations/{oid}/invitations/{viewer}/accept", headers=vh)
        r = c.post(tbase, json={"name": "Nope"}, headers=vh)
        assert r.status_code == 403


def test_cross_project_and_cross_org_isolation():
    with _client() as c:
        oh, oid, wid, pid = _bootstrap(c)
        tbase = f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}/tools"
        t = c.post(tbase, json={"name": "HTTP"}, headers=oh).json()["id"]
        # second project — the tool is not visible there
        p2 = c.post(
            f"/v1/organizations/{oid}/workspaces/{wid}/projects",
            json={"name": "P2", "slug": "p2"}, headers=oh,
        ).json()["id"]
        tbase2 = f"/v1/organizations/{oid}/workspaces/{wid}/projects/{p2}/tools"
        assert c.get(tbase2, headers=oh).json() == []
        assert c.get(f"{tbase2}/{t}", headers=oh).status_code == 404
        # cross-org: a different org's owner cannot manage this tool → 403
        b = c.post("/v1/auth/register", json={"email": "b@x.io", "display_name": "B"}).json()["id"]
        btok = c.post("/v1/auth/token", json={"user_id": b}).json()["access_token"]
        bh = {"Authorization": f"Bearer {btok}"}
        c.post("/v1/organizations", json={"name": "B"}, headers=bh)
        r = c.patch(f"{tbase}/{t}", json={"name": "Hacked"}, headers=bh)
        assert r.status_code == 403


def test_studio_ui_serves_tool_screens():
    with _client() as c:
        r = c.get("/studio/")
        assert r.status_code == 200 and "VoxVoice Studio" in r.text
        for marker in ("Tool Catalog", "Tool Library", "Tool Details", "Tool Configuration",
                       "Agent Tool Assignment", "Tool Ordering", "Register tool"):
            assert marker in r.text, marker
