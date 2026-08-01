"""Agent Builder vertical slice (Epic 8, M8.10) tests.

Covers every layer end-to-end: Foundation unit behaviour (create/update/config-merge,
validation, duplicate-name, archive/restore, soft-delete, duplicate), immutable versioning +
rollback, secret-variable redaction, the full Control Plane API + persistence integration
(the Definition-of-Done flow), durable version history across a restart, per-agent activity
over the M8.9 audit stream, authorization failure, and cross-project/cross-org isolation. The
Studio bundle is served at /studio (verified reachable) — its screens are exercised through
these API paths.
"""

from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from api.customer import Container, CustomerApiSettings, create_app
from api.foundation import (
    AgentConfig,
    AgentStatus,
    AgentVariable,
    ConflictError,
    CreateOrganizationRequest,
    CreateProjectRequest,
    CreateWorkspaceRequest,
    FixedClock,
    FoundationFactory,
    FoundationValidationError,
    ModelConfig,
    NotFoundError,
    PromptConfig,
    RegisterUserRequest,
    SequentialIdGenerator,
    VariableType,
    validate_agent,
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


def test_create_update_and_config_merge():
    f = _foundation()
    owner, org, ws, p = _project(f)
    a = f.agent_builder.create_agent(
        owner.id, org.id, ws.id, p.id, name="Bot",
        config=AgentConfig(model=ModelConfig(provider="x", model="m", temperature=0.5)),
    )
    assert a.status is AgentStatus.DRAFT and a.config.model.temperature == 0.5
    # A partial config patch merges — only the touched sub-block changes.
    updated = f.agent_builder.update_agent(
        owner.id, org.id, ws.id, p.id, a.id,
        description="support bot", config_patch={"model": {"temperature": 1.1}},
    )
    assert updated.description == "support bot"
    assert updated.config.model.temperature == 1.1
    assert updated.config.model.provider == "x"  # untouched sub-field preserved


def test_duplicate_name_rejected_within_project():
    f = _foundation()
    owner, org, ws, p = _project(f)
    f.agent_builder.create_agent(owner.id, org.id, ws.id, p.id, name="Bot")
    with pytest.raises(FoundationValidationError):
        f.agent_builder.create_agent(owner.id, org.id, ws.id, p.id, name="Bot")


def test_validation_prompt_variable_and_model():
    # Undefined variable reference is rejected.
    bad = validate_agent(
        "A", AgentConfig(prompt=PromptConfig(system_prompt="hi {{unknown_var}}"))
    )
    assert bad.has_errors
    # A standard variable resolves; a declared one resolves; temperature range enforced.
    ok = validate_agent(
        "A",
        AgentConfig(
            prompt=PromptConfig(system_prompt="hi {{company_name}} {{brand}}"),
            model=ModelConfig(temperature=0.5),
            variables=(AgentVariable(name="brand"),),
        ),
    )
    assert not ok.has_errors
    hot = validate_agent("A", AgentConfig(model=ModelConfig(temperature=9.0)))
    assert hot.has_errors


def test_secret_variable_is_redacted_in_view():
    f = _foundation()
    owner, org, ws, p = _project(f)
    a = f.agent_builder.create_agent(
        owner.id, org.id, ws.id, p.id, name="Bot",
        config=AgentConfig(
            variables=(AgentVariable(name="api_key", type=VariableType.SECRET, value="TOPSECRET"),)
        ),
    )
    from api.foundation import AgentView

    view = AgentView.of(a)
    secret = [v for v in view.config["variables"] if v["name"] == "api_key"][0]
    assert secret["value"] == "" and secret["has_value"] is True and secret["is_secret"] is True
    # but the domain retains it (for persistence/runtime later).
    assert a.config.variables[0].value == "TOPSECRET"


def test_publish_creates_immutable_version_and_rollback():
    f = _foundation()
    owner, org, ws, p = _project(f)
    a = f.agent_builder.create_agent(
        owner.id, org.id, ws.id, p.id, name="Bot",
        config=AgentConfig(model=ModelConfig(provider="x", model="m", temperature=0.3)),
    )
    a1, v1 = f.agent_builder.publish_agent(owner.id, org.id, ws.id, p.id, a.id, description="v1")
    assert a1.status is AgentStatus.PUBLISHED and v1.number == 1 and a1.published_version == 1
    # change + republish → v2
    f.agent_builder.update_agent(
        owner.id, org.id, ws.id, p.id, a.id, config_patch={"model": {"temperature": 1.5}}
    )
    a2, v2 = f.agent_builder.publish_agent(owner.id, org.id, ws.id, p.id, a.id, description="v2")
    assert v2.number == 2
    # rollback to v1 → NEW v3 whose config is restored from v1 (history never rewritten)
    a3, v3 = f.agent_builder.rollback_agent(owner.id, org.id, ws.id, p.id, a.id, 1)
    assert v3.number == 3 and v3.rollback_target == 1
    assert a3.config.model.temperature == 0.3
    assert [v.number for v in f.agent_builder.list_versions(org.id, a.id)] == [3, 2, 1]


def test_archive_restore_and_soft_delete():
    f = _foundation()
    owner, org, ws, p = _project(f)
    a = f.agent_builder.create_agent(owner.id, org.id, ws.id, p.id, name="Bot")
    arch = f.agent_builder.archive_agent(owner.id, org.id, ws.id, p.id, a.id)
    assert arch.status is AgentStatus.ARCHIVED
    with pytest.raises(ConflictError):
        f.agent_builder.archive_agent(owner.id, org.id, ws.id, p.id, a.id)
    rest = f.agent_builder.restore_agent(owner.id, org.id, ws.id, p.id, a.id)
    assert rest.status is AgentStatus.DRAFT
    f.agent_builder.delete_agent(owner.id, org.id, ws.id, p.id, a.id)
    with pytest.raises(NotFoundError):
        f.agent_builder.get_agent(org.id, p.id, a.id)
    assert f.agent_builder.list_agents(org.id, p.id) == ()


def test_duplicate_agent_clones_config_as_new_draft():
    f = _foundation()
    owner, org, ws, p = _project(f)
    a = f.agent_builder.create_agent(
        owner.id, org.id, ws.id, p.id, name="Bot",
        config=AgentConfig(model=ModelConfig(provider="x", model="m")),
    )
    f.agent_builder.publish_agent(owner.id, org.id, ws.id, p.id, a.id)
    clone = f.agent_builder.duplicate_agent(owner.id, org.id, ws.id, p.id, a.id)
    assert clone.id != a.id and clone.name == "Bot (copy)"
    assert clone.status is AgentStatus.DRAFT and clone.version == 0
    assert clone.config.model.provider == "x"


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
        base = f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}/agents"
        # 2. create multiple agents
        a = c.post(base, json={"name": "Support"}, headers=oh).json()
        b = c.post(base, json={"name": "Sales"}, headers=oh).json()
        assert a["status"] == "draft" and b["id"] != a["id"]
        assert len(c.get(base, headers=oh).json()) == 2
        # 3-6. configure prompts / model / voice / variables
        cfg = {
            "prompt": {"system_prompt": "Help {{company_name}}.", "instructions": "Be kind."},
            "model": {"provider": "acme-llm", "model": "big", "temperature": 0.4},
            "voice": {"enabled": True, "provider": "acme-voice", "voice_name": "nova"},
            "variables": [{"name": "api_key", "type": "secret", "value": "SECRET"}],
        }
        upd = c.patch(f"{base}/{a['id']}", json={"config_patch": cfg}, headers=oh)
        assert upd.status_code == 200
        got = upd.json()["config"]
        assert got["model"]["provider"] == "acme-llm" and got["voice"]["enabled"] is True
        # secret redacted
        assert [v for v in got["variables"] if v["name"] == "api_key"][0]["value"] == ""
        # 7. save draft (already saved); 8. publish
        pub = c.post(f"{base}/{a['id']}/publish", json={"description": "v1"}, headers=oh)
        assert pub.status_code == 200 and pub.json()["status"] == "published"
        assert pub.json()["version"] == 1
        # 9. browse version history
        vs = c.get(f"{base}/{a['id']}/versions", headers=oh).json()
        assert [v["number"] for v in vs] == [1]
        # version snapshot also redacts the secret
        v1 = c.get(f"{base}/{a['id']}/versions/1", headers=oh).json()
        snap_vars = v1["snapshot"]["config"]["variables"]
        assert [x for x in snap_vars if x["name"] == "api_key"][0]["value"] == ""
        # change + publish v2, then 10. roll back to v1
        patch = {"config_patch": {"model": {"temperature": 1.9}}}
        c.patch(f"{base}/{a['id']}", json=patch, headers=oh)
        c.post(f"{base}/{a['id']}/publish", json={"description": "v2"}, headers=oh)
        rb = c.post(f"{base}/{a['id']}/rollback", json={"target_number": 1}, headers=oh)
        assert rb.json()["version"] == 3
        assert rb.json()["config"]["model"]["temperature"] == 0.4  # restored from v1
        # version comparison
        diff = c.get(f"{base}/{a['id']}/versions/compare?from=1&to=2", headers=oh).json()
        assert any(ch["field"] == "config.model.temperature" for ch in diff["changes"])


def test_agent_activity_reuses_audit_platform():
    with _client() as c:
        oh, oid, wid, pid = _bootstrap(c)
        base = f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}/agents"
        a = c.post(base, json={"name": "Bot"}, headers=oh).json()
        c.post(f"{base}/{a['id']}/publish", json={"description": "v1"}, headers=oh)
        act = c.get(f"{base}/{a['id']}/activity", headers=oh).json()
        types = {f["type"] for f in act["feed"]}
        assert {"agent.created", "agent.published", "agent.version_created"} <= types
        assert act["total"] >= 3


def test_history_survives_restart_over_file_db():
    from api.application import GetAgent, ListAgentVersions
    from api.persistence import build_persistent_application

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    url = f"sqlite:///{path}"
    try:
        with _client(url) as c:
            oh, oid, wid, pid = _bootstrap(c)
            base = f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}/agents"
            a = c.post(base, json={"name": "Bot"}, headers=oh).json()
            c.post(f"{base}/{a['id']}/publish", json={"description": "v1"}, headers=oh)
            agent_id = a["id"]
        # A fresh process over the same on-disk DB still has the agent + its version history.
        pa = build_persistent_application(
            url=url, ids=SequentialIdGenerator(), clock=FixedClock(), create_all=False
        )
        agent = pa.application.queries.dispatch(GetAgent(oid, pid, agent_id))
        vs = pa.application.queries.dispatch(ListAgentVersions(oid, agent_id))
        assert agent["status"] == "published"
        assert [v["number"] for v in vs] == [1]
        pa.database.close()
    finally:
        os.unlink(path)


def test_authorization_failure_for_viewer():
    with _client() as c:
        oh, oid, wid, pid = _bootstrap(c)
        base = f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}/agents"
        viewer = c.post(
            "/v1/auth/register", json={"email": "v@a.io", "display_name": "V"}
        ).json()["id"]
        c.post(f"/v1/organizations/{oid}/invitations",
               json={"user_id": viewer, "tier": "viewer"}, headers=oh)
        vtok = c.post("/v1/auth/token", json={"user_id": viewer}).json()["access_token"]
        vh = {"Authorization": f"Bearer {vtok}"}
        c.post(f"/v1/organizations/{oid}/invitations/{viewer}/accept", headers=vh)
        r = c.post(base, json={"name": "Nope"}, headers=vh)
        assert r.status_code == 403


def test_cross_project_and_cross_org_isolation():
    with _client() as c:
        oh, oid, wid, pid = _bootstrap(c)
        base = f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}/agents"
        a = c.post(base, json={"name": "Bot"}, headers=oh).json()["id"]
        # second project in same workspace — the agent is not visible there
        p2 = c.post(
            f"/v1/organizations/{oid}/workspaces/{wid}/projects",
            json={"name": "P2", "slug": "p2"}, headers=oh,
        ).json()["id"]
        base2 = f"/v1/organizations/{oid}/workspaces/{wid}/projects/{p2}/agents"
        assert c.get(base2, headers=oh).json() == []
        assert c.get(f"{base2}/{a}", headers=oh).status_code == 404
        # cross-org: a different org's owner cannot manage this agent → 403
        b = c.post("/v1/auth/register", json={"email": "b@x.io", "display_name": "B"}).json()["id"]
        btok = c.post("/v1/auth/token", json={"user_id": b}).json()["access_token"]
        bh = {"Authorization": f"Bearer {btok}"}
        c.post("/v1/organizations", json={"name": "B"}, headers=bh)
        r = c.patch(f"{base}/{a}", json={"name": "Hacked"}, headers=bh)
        assert r.status_code == 403


def test_studio_ui_serves_agent_screens():
    with _client() as c:
        r = c.get("/studio/")
        assert r.status_code == 200 and "VoxVoice Studio" in r.text
        for marker in ("Create agent", "Prompt Builder", "Variables", "Version history",
                       "Model configuration", "Voice configuration"):
            assert marker in r.text, marker
