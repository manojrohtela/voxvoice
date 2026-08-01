"""Conversation Runtime vertical slice (Epic 8, M8.12) tests.

Covers every layer end-to-end: Foundation unit (the deterministic pipeline — **draft never
executes**, variable precedence, prompt assembly with secret masking, tool resolution respecting
ordering/enable/override, adapter requirements, **determinism**, session lifecycle + resume),
Application, persistence durability + **resume across a restart**, the full Control Plane API
(the Definition-of-Done flow), audit reuse, and cross-project/cross-org isolation. The Studio
runtime inspector is served at /studio (verified reachable). **No LLM call, no tool execution,
no voice synthesis.**
"""

from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from api.customer import Container, CustomerApiSettings, create_app
from api.foundation import (
    AgentConfig,
    AgentVariable,
    ConflictError,
    CreateOrganizationRequest,
    CreateProjectRequest,
    CreateWorkspaceRequest,
    FixedClock,
    FoundationFactory,
    ModelConfig,
    PromptConfig,
    RegisterUserRequest,
    SequentialIdGenerator,
    SessionStatus,
    ToolCategory,
    ToolConfig,
    VariableType,
    VoiceConfig,
)

# ── Foundation unit (in-memory wiring) ───────────────────────────────────────────


def _foundation():
    return FoundationFactory.default(ids=SequentialIdGenerator(), clock=FixedClock())


def _published_agent(f, *, with_tools=True, voice=False, secret=False):
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
    p = f.project_lifecycle.update_project(
        owner.id, org.id, ws.id, p.id, metadata={"variables": {"company_name": "Acme"}}
    )
    variables = ()
    if secret:
        variables = (AgentVariable(name="api_key", type=VariableType.SECRET, value="TOPSECRET"),)
    cfg = AgentConfig(
        prompt=PromptConfig(system_prompt="Hi {{company_name}}, tz={{timezone}}"),
        model=ModelConfig(provider="x", model="m"),
        voice=VoiceConfig(
            enabled=voice, provider="v" if voice else "",
            voice_name="nova" if voice else "",
        ),
        variables=variables,
    )
    agent = f.agent_builder.create_agent(owner.id, org.id, ws.id, p.id, name="Bot", config=cfg)
    if with_tools:
        t1 = f.tools.create_tool(
            owner.id, org.id, ws.id, p.id, name="HTTP", category=ToolCategory.HTTP_API,
            config=ToolConfig(settings={"url": "u"}),
        )
        t2 = f.tools.create_tool(
            owner.id, org.id, ws.id, p.id, name="DB", category=ToolCategory.DATABASE
        )
        f.agent_tools.attach_tool(owner.id, org.id, ws.id, p.id, agent.id, t1.id)
        f.agent_tools.attach_tool(owner.id, org.id, ws.id, p.id, agent.id, t2.id)
        f.agent_tools.reorder_tools(owner.id, org.id, ws.id, p.id, agent.id, (t2.id, t1.id))
        f.tools.set_enabled(owner.id, org.id, ws.id, p.id, t1.id, enabled=False)
    f.agent_builder.publish_agent(owner.id, org.id, ws.id, p.id, agent.id, description="v1")
    return owner, org, ws, p, agent


def test_draft_never_executes():
    f = _foundation()
    owner = f.identity.register_user(RegisterUserRequest("o@a.io", "U"))
    org = f.identity.create_organization(
        CreateOrganizationRequest(name="A", owner_user_id=owner.id)
    )
    ws = f.workspaces.create_workspace(
        owner.id, CreateWorkspaceRequest(org_id=org.id, name="W", slug="w")
    )
    p = f.projects.create_project(
        owner.id, CreateProjectRequest(org_id=org.id, workspace_id=ws.id, name="P", slug="p")
    )
    draft = f.agent_builder.create_agent(owner.id, org.id, ws.id, p.id, name="Draft")
    with pytest.raises(ConflictError):
        f.conversation_runtime.create_session(owner.id, org.id, ws.id, p.id, draft.id)


def test_variable_precedence_and_secret_masking():
    f = _foundation()
    owner, org, ws, p, agent = _published_agent(f, secret=True)
    s = f.conversation_runtime.create_session(
        owner.id, org.id, ws.id, p.id, agent.id,
        runtime_variables={"company_name": "Override", "extra": "1"},
    )
    by_name = {v.name: v for v in s.state.resolved_variables}
    # precedence: runtime overrides project; system defaults present; agent secret tracked
    cn = by_name["company_name"]
    assert cn.value == "Override" and cn.source.value == "runtime"
    assert by_name["timezone"].source.value == "system"
    assert by_name["api_key"].is_secret is True
    # secret is masked in the assembled prompt text and redacted in the view
    assert "TOPSECRET" not in s.state.resolved_prompt.text
    view = s.state.as_dict(redact_secrets=True)
    assert [v for v in view["resolved_variables"] if v["name"] == "api_key"][0]["value"] == ""


def test_tool_resolution_ordering_enable_and_adapters():
    f = _foundation()
    owner, org, ws, p, agent = _published_agent(f, with_tools=True, voice=True)
    s = f.conversation_runtime.create_session(owner.id, org.id, ws.id, p.id, agent.id)
    steps = s.state.tool_plan.steps
    # reordered so DB is first; the disabled HTTP tool yields a disabled step
    assert [x.name for x in steps] == ["DB", "HTTP"]
    assert [x.enabled for x in steps] == [True, False]
    req = s.state.tool_plan.required_adapters
    assert req["llm"] is True and req["voice"] is True and req["tool_executor"] is True
    assert req["memory"] is False


def test_pipeline_is_deterministic():
    f = _foundation()
    owner, org, ws, p, agent = _published_agent(f)
    a = f.conversation_runtime.create_session(
        owner.id, org.id, ws.id, p.id, agent.id, runtime_variables={"company_name": "X"}
    )
    b = f.conversation_runtime.create_session(
        owner.id, org.id, ws.id, p.id, agent.id, runtime_variables={"company_name": "X"}
    )
    assert a.state.resolved_prompt.text == b.state.resolved_prompt.text
    assert a.state.tool_plan.as_dict() == b.state.tool_plan.as_dict()
    assert [v.as_dict() for v in a.state.resolved_variables] == [
        v.as_dict() for v in b.state.resolved_variables
    ]


def test_session_lifecycle_resume_close():
    f = _foundation()
    owner, org, ws, p, agent = _published_agent(f)
    s = f.conversation_runtime.create_session(owner.id, org.id, ws.id, p.id, agent.id)
    assert s.status is SessionStatus.ACTIVE
    f.conversation_runtime.resume_session(owner.id, org.id, ws.id, p.id, s.id)
    closed = f.conversation_runtime.close_session(owner.id, org.id, ws.id, p.id, s.id)
    assert closed.status is SessionStatus.CLOSED
    # cannot resume or re-close a closed session
    with pytest.raises(ConflictError):
        f.conversation_runtime.resume_session(owner.id, org.id, ws.id, p.id, s.id)
    with pytest.raises(ConflictError):
        f.conversation_runtime.close_session(owner.id, org.id, ws.id, p.id, s.id)


def test_runtime_never_mutates_configuration():
    f = _foundation()
    owner, org, ws, p, agent = _published_agent(f)
    before = f.agent_builder.get_agent(org.id, p.id, agent.id)
    f.conversation_runtime.create_session(owner.id, org.id, ws.id, p.id, agent.id)
    after = f.agent_builder.get_agent(org.id, p.id, agent.id)
    # the agent + its published config are unchanged by running the runtime
    assert before == after


# ── API + persistence integration (Definition of Done) ───────────────────────────


def _client(url: str = "sqlite://") -> TestClient:
    settings = CustomerApiSettings(environment="testing", database_url=url)
    container = Container.build(settings, ids=SequentialIdGenerator(), clock=FixedClock())
    return TestClient(create_app(container=container))


def _bootstrap_published(c):
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
    abase = f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}/agents"
    aid = c.post(
        abase, json={"name": "Bot", "config": {"prompt": {"system_prompt": "Hi {{company_name}}"}}},
        headers=oh,
    ).json()["id"]
    t = c.post(
        f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}/tools",
        json={"name": "HTTP", "category": "http_api"}, headers=oh,
    ).json()["id"]
    c.post(f"{abase}/{aid}/tools", json={"tool_id": t}, headers=oh)
    c.post(f"{abase}/{aid}/publish", json={"description": "v1"}, headers=oh)
    return oh, oid, wid, pid, aid, owner


def test_definition_of_done_end_to_end():
    with _client() as c:
        oh, oid, wid, pid, aid, _owner = _bootstrap_published(c)  # 1. publish an agent
        sbase = f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}/agents/{aid}/sessions"
        # 2. create a runtime session
        s = c.post(sbase, json={"runtime_variables": {"company_name": "Acme"}}, headers=oh)
        assert s.status_code == 201 and s.json()["status"] == "active"
        sid = s.json()["id"]
        # 3-4. inspect fully resolved prompt + variables
        plan = c.get(f"{sbase}/{sid}/plan", headers=oh).json()
        assert "Acme" in plan["resolved_prompt"]["text"]
        assert {v["name"] for v in plan["resolved_variables"]} >= {"company_name", "timezone"}
        # 5. inspect tool resolution
        assert [x["name"] for x in plan["tool_plan"]["steps"]] == ["HTTP"]
        assert plan["tool_plan"]["required_adapters"]["llm"] is True
        # 6. inspect runtime state
        state = c.get(f"{sbase}/{sid}/state", headers=oh).json()
        assert state["status"] == "active" and state["agent_version"] == 1
        # 7. resume the session
        assert c.post(f"{sbase}/{sid}/resume", headers=oh).json()["status"] == "active"
        # 8. close the session
        assert c.post(f"{sbase}/{sid}/close", headers=oh).json()["status"] == "closed"


def test_session_created_and_planned_audited():
    with _client() as c:
        oh, oid, wid, pid, aid, _owner = _bootstrap_published(c)
        sbase = f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}/agents/{aid}/sessions"
        c.post(sbase, json={}, headers=oh)
        # session + planning facts land in the durable project activity (M8.9 reuse)
        act = c.get(
            f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}/activity", headers=oh
        ).json()
        types = {f["type"] for f in act["feed"]}
        assert {"session.created", "execution.planned"} <= types


def test_session_survives_restart_and_resumes():
    """A fresh process over the same DB retains the resolved state and can resume the
    session over the API — the runtime carries nothing but the persisted ConversationState."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    url = f"sqlite:///{path}"
    try:
        with _client(url) as c:
            oh, oid, wid, pid, aid, owner = _bootstrap_published(c)
            sbase = f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}/agents/{aid}/sessions"
            body = {"runtime_variables": {"company_name": "Acme"}}
            sid = c.post(sbase, json=body, headers=oh).json()["id"]
        # fresh app + container over the same on-disk DB
        settings = CustomerApiSettings(environment="testing", database_url=url)
        container = Container.build(settings, ids=SequentialIdGenerator(), clock=FixedClock())
        with TestClient(create_app(container=container)) as c2:
            tok = c2.post("/v1/auth/token", json={"user_id": owner}).json()["access_token"]
            oh2 = {"Authorization": f"Bearer {tok}"}
            sbase = f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}/agents/{aid}/sessions"
            state = c2.get(f"{sbase}/{sid}/state", headers=oh2).json()
            assert state["status"] == "active" and state["agent_version"] == 1
            assert c2.post(f"{sbase}/{sid}/resume", headers=oh2).json()["status"] == "active"
    finally:
        os.unlink(path)


def test_isolation_cross_org_and_viewer():
    with _client() as c:
        oh, oid, wid, pid, aid, _owner = _bootstrap_published(c)
        sbase = f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}/agents/{aid}/sessions"
        c.post(sbase, json={}, headers=oh)
        # a different org's owner cannot create a session against this project (403)
        b = c.post("/v1/auth/register", json={"email": "b@x.io", "display_name": "B"}).json()["id"]
        btok = c.post("/v1/auth/token", json={"user_id": b}).json()["access_token"]
        bh = {"Authorization": f"Bearer {btok}"}
        c.post("/v1/organizations", json={"name": "B"}, headers=bh)
        assert c.post(sbase, json={}, headers=bh).status_code == 403
        # and a viewer cannot create a session (needs PROJECT_MANAGE)
        v = c.post("/v1/auth/register", json={"email": "v@a.io", "display_name": "V"}).json()["id"]
        c.post(f"/v1/organizations/{oid}/invitations",
               json={"user_id": v, "tier": "viewer"}, headers=oh)
        vtok = c.post("/v1/auth/token", json={"user_id": v}).json()["access_token"]
        vh = {"Authorization": f"Bearer {vtok}"}
        c.post(f"/v1/organizations/{oid}/invitations/{v}/accept", headers=vh)
        assert c.post(sbase, json={}, headers=vh).status_code == 403


def test_studio_ui_serves_runtime_screens():
    with _client() as c:
        r = c.get("/studio/")
        assert r.status_code == 200 and "VoxVoice Studio" in r.text
        for marker in ("Runtime Inspector", "Execution Plan", "Session Viewer",
                       "Variable Inspector", "Tool Plan", "Diagnostics"):
            assert marker in r.text, marker
