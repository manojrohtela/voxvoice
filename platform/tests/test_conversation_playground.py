"""Conversation Playground vertical slice (Epic 8, M8.13) tests.

Covers every layer end-to-end: the deterministic **mock adapters** (LLM/Tool/Voice/Memory),
the Foundation playground service (start/continue/reset/close, **draft never executes**, config
never mutated), Application, the full Control Plane API (the Definition-of-Done flow), audit
reuse, **determinism** (replaying a conversation yields identical turns), and cross-org
isolation. The Studio playground is served at /studio (verified reachable). **No real provider,
no real LLM, no real tool execution, no audio synthesis.**
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.customer import Container, CustomerApiSettings, create_app
from api.foundation import (
    AgentConfig,
    ConflictError,
    CreateOrganizationRequest,
    CreateProjectRequest,
    CreateWorkspaceRequest,
    FixedClock,
    FoundationFactory,
    MockAdapterSet,
    ModelConfig,
    PromptConfig,
    RegisterUserRequest,
    SequentialIdGenerator,
    SessionStatus,
    ToolCategory,
    ToolConfig,
    VoiceConfig,
)

# ── mock adapters (deterministic, no network) ────────────────────────────────────


def test_mock_adapters_are_deterministic():
    m = MockAdapterSet.default()
    a = m.llm.complete("hello world", options={"model": "x"})
    b = m.llm.complete("hello world", options={"model": "x"})
    assert a == b  # identical input → identical output
    assert a["provider"] == "mock" and a["token_estimate"] > 0
    # tools + voice are deterministic and metadata-only
    t = m.tools.invoke("tool_1", arguments={"input": "x"}, config={"url": "u"})
    assert t == m.tools.invoke("tool_1", arguments={"input": "x"}, config={"url": "u"})
    assert t["status"] == "mock" and t["output"] == "mock-output::tool_1"
    v = m.voice.synthesize("hi", options={"voice_name": "nova"})
    assert v["audio"] is None and v["voice_name"] == "nova"
    assert m.describe() == {
        "llm": "mock", "tool_executor": "mock", "voice": "mock", "memory": "mock",
    }


# ── Foundation unit ──────────────────────────────────────────────────────────────


def _foundation():
    return FoundationFactory.default(ids=SequentialIdGenerator(), clock=FixedClock())


def _published_agent(f, *, voice=False, tools=True):
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
    cfg = AgentConfig(
        prompt=PromptConfig(system_prompt="Hi {{company_name}}"),
        model=ModelConfig(provider="x", model="m"),
        voice=VoiceConfig(
            enabled=voice, provider="v" if voice else "",
            voice_name="nova" if voice else "",
        ),
    )
    agent = f.agent_builder.create_agent(owner.id, org.id, ws.id, p.id, name="Bot", config=cfg)
    if tools:
        t = f.tools.create_tool(
            owner.id, org.id, ws.id, p.id, name="HTTP", category=ToolCategory.HTTP_API,
            config=ToolConfig(settings={"url": "u"}),
        )
        f.agent_tools.attach_tool(owner.id, org.id, ws.id, p.id, agent.id, t.id)
    f.agent_builder.publish_agent(owner.id, org.id, ws.id, p.id, agent.id, description="v1")
    return owner, org, ws, p, agent


def test_draft_never_runs_in_playground():
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
        f.playground.start_conversation(owner.id, org.id, ws.id, p.id, draft.id)


def test_turn_runs_mock_adapters_and_appends_history():
    f = _foundation()
    owner, org, ws, p, agent = _published_agent(f, voice=True)
    s = f.playground.start_conversation(
        owner.id, org.id, ws.id, p.id, agent.id, runtime_variables={"company_name": "Acme"}
    )
    turn = f.playground.continue_conversation(
        owner.id, org.id, ws.id, p.id, s.id, user_input="hello"
    )
    assert turn.index == 0 and turn.llm_output["provider"] == "mock"
    assert len(turn.tool_invocations) == 1
    assert turn.tool_invocations[0]["output"].startswith("mock-output")
    assert turn.voice_output is not None and turn.voice_output["audio"] is None
    # a second turn appends
    f.playground.continue_conversation(owner.id, org.id, ws.id, p.id, s.id, user_input="again")
    turns = f.playground.turns(org.id, s.id)
    assert [t.index for t in turns] == [0, 1]


def test_playground_is_deterministic_replay():
    """Two identical conversations (same published version + same turns) produce identical
    turn outputs — the mock runtime is deterministic."""
    def run():
        f = _foundation()
        owner, org, ws, p, agent = _published_agent(f)
        s = f.playground.start_conversation(
            owner.id, org.id, ws.id, p.id, agent.id, runtime_variables={"company_name": "Acme"}
        )
        t0 = f.playground.continue_conversation(
            owner.id, org.id, ws.id, p.id, s.id, user_input="hi"
        )
        t1 = f.playground.continue_conversation(
            owner.id, org.id, ws.id, p.id, s.id, user_input="bye"
        )
        return [t0.as_dict(), t1.as_dict()]

    a = run()
    b = run()
    # created_at uses the deterministic FixedClock, so full turn dicts match
    assert a == b


def test_reset_clears_turns_keeps_plan():
    f = _foundation()
    owner, org, ws, p, agent = _published_agent(f)
    s = f.playground.start_conversation(owner.id, org.id, ws.id, p.id, agent.id)
    f.playground.continue_conversation(owner.id, org.id, ws.id, p.id, s.id, user_input="hi")
    plan_before = f.playground.get_conversation(org.id, s.id).state.resolved_prompt.text
    reset = f.playground.reset_conversation(owner.id, org.id, ws.id, p.id, s.id)
    assert reset.state.history == ()
    # the resolved plan (prompt) is intact after reset
    assert reset.state.resolved_prompt.text == plan_before


def test_playground_never_mutates_configuration():
    f = _foundation()
    owner, org, ws, p, agent = _published_agent(f)
    before = f.agent_builder.get_agent(org.id, p.id, agent.id)
    s = f.playground.start_conversation(owner.id, org.id, ws.id, p.id, agent.id)
    f.playground.continue_conversation(owner.id, org.id, ws.id, p.id, s.id, user_input="hi")
    after = f.agent_builder.get_agent(org.id, p.id, agent.id)
    assert before == after


def test_close_conversation_terminal():
    f = _foundation()
    owner, org, ws, p, agent = _published_agent(f)
    s = f.playground.start_conversation(owner.id, org.id, ws.id, p.id, agent.id)
    closed = f.playground.close_conversation(owner.id, org.id, ws.id, p.id, s.id)
    assert closed.status is SessionStatus.CLOSED
    with pytest.raises(ConflictError):
        f.playground.continue_conversation(owner.id, org.id, ws.id, p.id, s.id, user_input="x")


# ── API + persistence integration (Definition of Done) ───────────────────────────


def _client() -> TestClient:
    settings = CustomerApiSettings(environment="testing", database_url="sqlite://")
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
    return oh, oid, wid, pid, aid


def test_definition_of_done_end_to_end():
    with _client() as c:
        oh, oid, wid, pid, aid = _bootstrap_published(c)  # 1. publish an agent
        base = f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}/agents/{aid}"
        pg = f"{base}/playground/conversations"
        # 2. start a conversation
        s = c.post(pg, json={"runtime_variables": {"company_name": "Acme"}}, headers=oh)
        assert s.status_code == 201 and s.json()["status"] == "active"
        sid = s.json()["id"]
        # 3. send multiple turns
        t0 = c.post(f"{pg}/{sid}/turns", json={"user_input": "hello"}, headers=oh)
        assert t0.status_code == 201 and t0.json()["llm_output"]["provider"] == "mock"
        c.post(f"{pg}/{sid}/turns", json={"user_input": "again"}, headers=oh)
        conv = c.get(f"{pg}/{sid}", headers=oh).json()
        assert len(conv["turns"]) == 2 and len(conv["messages"]) == 4
        # 4-5. inspect prompts + variables (reuses the runtime plan)
        plan = c.get(f"{pg}/{sid}/plan", headers=oh).json()
        assert "Acme" in plan["resolved_prompt"]["text"]
        assert any(v["name"] == "company_name" for v in plan["resolved_variables"])
        # 6. inspect tool planning
        assert [x["name"] for x in plan["tool_plan"]["steps"]] == ["HTTP"]
        # 7. inspect adapter outputs
        ad = c.get(f"{pg}/{sid}/adapters", headers=oh).json()
        assert ad["adapters"]["llm"] == "mock" and ad["latest_turn"]["index"] == 1
        assert ad["latest_turn"]["tool_invocations"][0]["output"].startswith("mock-output")
        # 8. reset the conversation
        c.post(f"{pg}/{sid}/reset", headers=oh)
        assert c.get(f"{pg}/{sid}/turns", headers=oh).json() == []
        # 9. close the conversation
        assert c.post(f"{pg}/{sid}/close", headers=oh).json()["status"] == "closed"


def test_playground_lifecycle_audited():
    with _client() as c:
        oh, oid, wid, pid, aid = _bootstrap_published(c)
        base = f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}/agents/{aid}"
        pg = f"{base}/playground/conversations"
        sid = c.post(pg, json={}, headers=oh).json()["id"]
        c.post(f"{pg}/{sid}/turns", json={"user_input": "hi"}, headers=oh)
        act = c.get(
            f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}/activity", headers=oh
        ).json()
        types = {f["type"] for f in act["feed"]}
        assert {"conversation.started", "conversation.turn_planned",
                "conversation.turn_completed"} <= types


def test_isolation_cross_org_and_viewer():
    with _client() as c:
        oh, oid, wid, pid, aid = _bootstrap_published(c)
        base = f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}/agents/{aid}"
        pg = f"{base}/playground/conversations"
        c.post(pg, json={}, headers=oh)
        # a different org's owner cannot start a conversation here (403)
        b = c.post("/v1/auth/register", json={"email": "b@x.io", "display_name": "B"}).json()["id"]
        btok = c.post("/v1/auth/token", json={"user_id": b}).json()["access_token"]
        bh = {"Authorization": f"Bearer {btok}"}
        c.post("/v1/organizations", json={"name": "B"}, headers=bh)
        assert c.post(pg, json={}, headers=bh).status_code == 403
        # a viewer cannot start a conversation (needs PROJECT_MANAGE)
        v = c.post("/v1/auth/register", json={"email": "v@a.io", "display_name": "V"}).json()["id"]
        c.post(f"/v1/organizations/{oid}/invitations",
               json={"user_id": v, "tier": "viewer"}, headers=oh)
        vtok = c.post("/v1/auth/token", json={"user_id": v}).json()["access_token"]
        vh = {"Authorization": f"Bearer {vtok}"}
        c.post(f"/v1/organizations/{oid}/invitations/{v}/accept", headers=vh)
        assert c.post(pg, json={}, headers=vh).status_code == 403


def test_studio_ui_serves_playground_screens():
    with _client() as c:
        r = c.get("/studio/")
        assert r.status_code == 200 and "VoxVoice Studio" in r.text
        for marker in ("Conversation Playground", "Chat Window", "Prompt Inspector",
                       "Variable Inspector", "Execution Timeline", "Tool Invocation Viewer",
                       "Adapter Output Viewer", "Diagnostics Panel", "Session Browser"):
            assert marker in r.text, marker
