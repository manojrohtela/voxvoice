"""Deployment Actuator vertical slice (Epic 8, M8.16) tests — ADR-0022.

Covers the Deployment Actuator as the **sole owner of deployment execution**: the deterministic
lifecycle FSM, the infrastructure-adapter registry (Local executes; production declared), the Local
adapter capabilities, the engine (submit → approve → execute → complete, plus reject / cancel /
retry / rollback / health-triggered rollback), separation-of-duties approval, and the full Control
Plane API + Studio **Definition-of-Done** flow (publish agent → plan → submit → approve → execute →
view progress → cancel → roll back → logs/health). Also audit + org/workspace/project isolation.

**No network, no containers, no cloud** — only the deterministic Local adapter executes; Product
Runtime (Epic 7) is consumed read-only and never modified.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from actuator import (
    ApprovalError,
    DeploymentActuator,
    DeploymentEngine,
    DeploymentExecutionState,
    DeploymentJob,
    IllegalTransitionError,
    InfrastructureRegistry,
    InMemoryDeploymentExecutionRepository,
    LocalDeploymentAdapter,
    ProductionAdapterUnavailableError,
    assert_transition,
    can_transition,
)
from actuator.lifecycle import TERMINAL_STATES
from api.customer import Container, CustomerApiSettings, create_app
from api.foundation import FixedClock, SequentialIdGenerator

S = DeploymentExecutionState


# ── deterministic test seams ──────────────────────────────────────────────────────


class _Clock:
    def now(self) -> float:
        return 100.0


class _Ids:
    def __init__(self) -> None:
        self._n = 0

    def new(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}_{self._n}"


def _engine(registry: InfrastructureRegistry | None = None) -> DeploymentEngine:
    return DeploymentEngine(
        repository=InMemoryDeploymentExecutionRepository(),
        registry=registry or InfrastructureRegistry(),
        clock=_Clock(),
        ids=_Ids(),
    )


def _job(**over) -> DeploymentJob:
    base = dict(
        id="j1",
        deployment_id="dp_x",
        org_id="o",
        workspace_id="w",
        project_id="p",
        source_ref="v1",
        environment="development",
        profile="development",
        strategy="rolling",
        target="local",
        requires_approval=False,
        rollback_mode="previous_version",
        rollback_version="v0",
        requested_by="u1",
        requested_at=1.0,
    )
    base.update(over)
    return DeploymentJob(**base)


# ── lifecycle FSM (pure, table-driven) ─────────────────────────────────────────────


def test_lifecycle_fsm_legal_and_illegal():
    assert can_transition(S.REQUESTED, S.VALIDATING)
    assert can_transition(S.VALIDATING, S.PENDING_APPROVAL)
    assert can_transition(S.PENDING_APPROVAL, S.QUEUED)
    assert can_transition(S.QUEUED, S.EXECUTING)
    assert can_transition(S.EXECUTING, S.VERIFYING)
    assert can_transition(S.VERIFYING, S.COMPLETED)
    assert can_transition(S.VERIFYING, S.ROLLING_BACK)
    assert can_transition(S.ROLLING_BACK, S.ROLLED_BACK)
    assert can_transition(S.FAILED, S.QUEUED)  # retry
    # illegal jumps raise
    assert not can_transition(S.REQUESTED, S.COMPLETED)
    assert not can_transition(S.QUEUED, S.COMPLETED)
    with pytest.raises(IllegalTransitionError):
        assert_transition(S.REQUESTED, S.EXECUTING)
    assert S.COMPLETED in TERMINAL_STATES and S.ROLLED_BACK in TERMINAL_STATES


# ── infrastructure adapters (registry + Local executes; production declared) ───────


def test_infrastructure_registry_targets_and_capabilities():
    reg = InfrastructureRegistry()
    desc = InfrastructureRegistry.describe()
    assert set(desc) == {
        "local",
        "docker",
        "kubernetes",
        "gitops",
        "terraform",
        "aws_ecs",
        "azure",
        "gcp",
        "nomad",
    }
    assert desc["local"]["executes"] is True
    assert all(desc[t]["executes"] is False for t in desc if t != "local")
    # only Local actuates; a production adapter raises a typed error on apply
    assert isinstance(reg.get("local"), LocalDeploymentAdapter)
    with pytest.raises(ProductionAdapterUnavailableError):
        reg.get("kubernetes").apply("dp", "v1", "production")
    # declared capabilities differ per target (k8s canary, terraform none)
    assert reg.capabilities("kubernetes").supports_canary is True
    assert reg.capabilities("terraform").supports_canary is False


def test_local_adapter_capabilities():
    a = LocalDeploymentAdapter()
    assert a.status("d") == "absent"
    r = a.apply("d", "v1", "dev")
    assert r.ok and r.live_version == "v1" and a.status("d") == "running"
    assert a.health("d").live is True
    a.stop("d")
    assert a.status("d") == "stopped" and a.health("d").live is False
    a.restart("d")
    assert a.status("d") == "running"
    a.rollback("d", "v0")
    assert a.status("d") == "running"
    assert any("rolled back to v0" in m for m in a.logs("d"))


# ── engine lifecycle (deterministic, offline) ──────────────────────────────────────


def test_engine_submit_approve_execute_complete():
    eng = _engine()
    ex = eng.submit(_job(requires_approval=True, environment="production"))
    assert ex.state is S.PENDING_APPROVAL
    ex = eng.approve("o", ex.id, "approver")
    assert ex.state is S.QUEUED
    ex = eng.execute("o", ex.id)
    assert ex.state is S.COMPLETED and ex.result.live_version == "v1"
    # a full transition timeline is recorded
    states = [t.to_state for t in ex.transitions]
    assert S.EXECUTING in states and S.VERIFYING in states and S.COMPLETED in states


def test_engine_auto_approve_and_no_approval_paths():
    eng = _engine()
    ex = DeploymentActuator(eng).deploy(_job(environment="development"))
    assert ex.state is S.COMPLETED  # no approval required → runs straight through
    eng2 = _engine()
    ex2 = DeploymentActuator(eng2).deploy(
        _job(requires_approval=True, environment="production"), auto_approve=True
    )
    assert ex2.state is S.COMPLETED and ex2.approvals[-1].actor == "system"


def test_engine_separation_of_duties():
    eng = _engine()
    ex = eng.submit(_job(requires_approval=True, requested_by="u1", environment="production"))
    with pytest.raises(ApprovalError):
        eng.approve("o", ex.id, "u1")  # requester cannot approve their own
    ex = eng.approve("o", ex.id, "u2")
    assert ex.state is S.QUEUED


def test_engine_reject_and_cancel():
    eng = _engine()
    ex = eng.submit(_job(requires_approval=True, environment="production"))
    rej = eng.reject("o", ex.id, "u2", "not now")
    assert rej.state is S.FAILED and rej.result.status == "failed"
    eng2 = _engine()
    ex2 = eng2.submit(_job(requires_approval=True, environment="production"))
    can = eng2.cancel("o", ex2.id, "u1")
    assert can.state is S.CANCELLED and can.is_terminal


def test_engine_failure_retry_and_rollback():
    eng = _engine()
    # a failing apply (deterministic Local sentinel) drives EXECUTING → FAILED
    ex = DeploymentActuator(eng).deploy(_job(source_ref="fail:bad"))
    assert ex.state is S.FAILED
    # retry re-queues (budget) and — still failing source — fails again deterministically
    ex = eng.retry("o", ex.id, "u1")
    assert ex.state is S.QUEUED and ex.attempts == 2
    # a completed deploy can be rolled back manually
    eng2 = _engine()
    ok = DeploymentActuator(eng2).deploy(_job())
    assert ok.state is S.COMPLETED
    rb = eng2.rollback("o", ok.id, "u1", "manual")
    assert rb.state is S.ROLLED_BACK and rb.rollbacks[-1].target.version == "v0"


def test_engine_health_failed_triggers_rollback():
    # a Local adapter whose health never passes → VERIFYING routes to rollback
    class _Unhealthy(LocalDeploymentAdapter):
        def health(self, deployment_id):
            probe = super().health(deployment_id)
            return probe.__class__(startup_ok=True, ready=False, live=False, detail="never ready")

    class _Reg(InfrastructureRegistry):
        def get(self, target):
            return self._unhealthy

        _unhealthy = _Unhealthy()

    eng = _engine(registry=_Reg())
    ex = DeploymentActuator(eng).deploy(_job())
    assert ex.state is S.ROLLED_BACK
    assert any(h.healthy is False for h in ex.health)


# ── Control Plane API + Studio (Definition of Done) ────────────────────────────────


def _client() -> TestClient:
    settings = CustomerApiSettings(environment="testing", database_url="sqlite://")
    container = Container.build(settings, ids=SequentialIdGenerator(), clock=FixedClock())
    return TestClient(create_app(container=container))


def _auth(c, uid):
    return {
        "Authorization": "Bearer "
        + c.post("/v1/auth/token", json={"user_id": uid}).json()["access_token"]
    }


def _bootstrap(c):
    owner = c.post("/v1/auth/register", json={"email": "f@a.io", "display_name": "U"}).json()["id"]
    oh = _auth(c, owner)
    oid = c.post("/v1/organizations", json={"name": "Acme"}, headers=oh).json()["id"]
    wid = c.post(
        f"/v1/organizations/{oid}/workspaces", json={"name": "T", "slug": "t"}, headers=oh
    ).json()["id"]
    pid = c.post(
        f"/v1/organizations/{oid}/workspaces/{wid}/projects",
        json={"name": "P", "slug": "p"},
        headers=oh,
    ).json()["id"]
    base = f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}"
    return oh, oid, wid, pid, base


def _published_agent(c, oh, base):
    aid = c.post(
        f"{base}/agents",
        json={
            "name": "Bot",
            "config": {
                "prompt": {"system_prompt": "Hi"},
                "model": {"provider": "anthropic", "model": "claude"},
            },
        },
        headers=oh,
    ).json()["id"]
    c.post(f"{base}/agents/{aid}/publish", json={"description": "v1"}, headers=oh)
    return aid


def _workspace_admin(c, oid, wid, oh, email):
    uid = c.post("/v1/auth/register", json={"email": email, "display_name": "A"}).json()["id"]
    c.post(
        f"/v1/organizations/{oid}/invitations", json={"user_id": uid, "tier": "member"}, headers=oh
    )
    c.post(f"/v1/organizations/{oid}/invitations/{uid}/accept", headers=_auth(c, uid))
    c.post(
        f"/v1/organizations/{oid}/workspaces/{wid}/members",
        json={"user_id": uid, "role": "admin"},
        headers=oh,
    )
    return uid, _auth(c, uid)


def test_definition_of_done_end_to_end():
    with _client() as c:
        oh, oid, wid, pid, base = _bootstrap(c)
        aid = _published_agent(c, oh, base)
        dep = f"{base}/deployments"
        # 3. create a deployment plan (read-only validation — no execution)
        plan = c.post(
            f"{dep}/plan", json={"agent_id": aid, "environment": "production"}, headers=oh
        ).json()
        assert plan["valid"] and plan["requires_approval"] and plan["target"] == "local"
        # 4. submit deployment (production → approval-gated)
        ex = c.post(dep, json={"agent_id": aid, "environment": "production"}, headers=oh).json()
        assert ex["state"] == "pending_approval"
        did = ex["id"]
        # 5. approve (separation of duties — a workspace admin, not the requester)
        _, ah = _workspace_admin(c, oid, wid, oh, "approver@a.io")
        approved = c.post(f"{dep}/{did}/approve", json={"reason": "ship it"}, headers=ah).json()
        # 6. execute local deployment (approval triggers execution) → 7. progress visible
        assert approved["state"] == "completed" and approved["live_version"] == "agv_1"
        # 10. view health + logs
        health = c.get(f"{dep}/{did}/health", headers=oh).json()
        assert health["healthy"] is True and health["infra_status"] == "running"
        logs = c.get(f"{dep}/{did}/logs", headers=oh).json()["logs"]
        assert any("applied" in ln["message"] for ln in logs)
        # 9. roll back deployment
        rb = c.post(f"{dep}/{did}/rollback", json={"reason": "regression"}, headers=oh).json()
        assert rb["state"] == "rolled_back"
        # 8. cancel a fresh (pending) deployment
        ex2 = c.post(dep, json={"agent_id": aid, "environment": "production"}, headers=oh).json()
        cancelled = c.post(f"{dep}/{ex2['id']}/cancel", headers=oh).json()
        assert cancelled["state"] == "cancelled"
        # history + targets visible
        assert len(c.get(dep, headers=oh).json()) == 2
        assert set(c.get(f"{dep}/targets", headers=oh).json()) >= {"local", "kubernetes"}


def test_dev_deploy_runs_without_approval():
    with _client() as c:
        oh, oid, wid, pid, base = _bootstrap(c)
        aid = _published_agent(c, oh, base)
        ex = c.post(
            f"{base}/deployments", json={"agent_id": aid, "environment": "development"}, headers=oh
        ).json()
        assert ex["state"] == "completed"
        # timeline shows the deterministic FSM path
        tl = c.get(f"{base}/deployments/{ex['id']}/timeline", headers=oh).json()
        assert [t["to"] for t in tl["transitions"]][-1] == "completed"


def test_draft_agent_cannot_deploy():
    with _client() as c:
        oh, oid, wid, pid, base = _bootstrap(c)
        aid = c.post(
            f"{base}/agents",
            json={
                "name": "Draft",
                "config": {
                    "prompt": {"system_prompt": "Hi"},
                    "model": {"provider": "anthropic", "model": "claude"},
                },
            },
            headers=oh,
        ).json()["id"]
        r = c.post(f"{base}/deployments", json={"agent_id": aid}, headers=oh)
        assert r.status_code == 409  # no published version


def test_separation_of_duties_over_api():
    with _client() as c:
        oh, oid, wid, pid, base = _bootstrap(c)
        aid = _published_agent(c, oh, base)
        did = c.post(
            f"{base}/deployments", json={"agent_id": aid, "environment": "production"}, headers=oh
        ).json()["id"]
        # the requester (owner) cannot approve their own production deployment
        r = c.post(f"{base}/deployments/{did}/approve", json={"reason": "x"}, headers=oh)
        assert r.status_code == 409


def test_deployment_pipeline_audited():
    with _client() as c:
        oh, oid, wid, pid, base = _bootstrap(c)
        aid = _published_agent(c, oh, base)
        c.post(
            f"{base}/deployments", json={"agent_id": aid, "environment": "development"}, headers=oh
        )
        act = c.get(f"/v1/organizations/{oid}/activity", headers=oh).json()
        types = {f["type"] for f in act["feed"]}
        assert {
            "deployment.requested",
            "deployment.validated",
            "deployment.queued",
            "deployment.started",
            "deployment.verifying",
            "deployment.completed",
        } <= types


def test_isolation_cross_project_and_viewer():
    with _client() as c:
        oh, oid, wid, pid, base = _bootstrap(c)
        aid = _published_agent(c, oh, base)
        did = c.post(
            f"{base}/deployments", json={"agent_id": aid, "environment": "development"}, headers=oh
        ).json()["id"]
        # a second project cannot see the deployment
        p2 = c.post(
            f"/v1/organizations/{oid}/workspaces/{wid}/projects",
            json={"name": "P2", "slug": "p2"},
            headers=oh,
        ).json()["id"]
        base2 = f"/v1/organizations/{oid}/workspaces/{wid}/projects/{p2}"
        assert c.get(f"{base2}/deployments", headers=oh).json() == []
        assert c.get(f"{base2}/deployments/{did}", headers=oh).status_code == 404
        # a viewer cannot submit a deployment
        v = c.post("/v1/auth/register", json={"email": "v@a.io", "display_name": "V"}).json()["id"]
        c.post(
            f"/v1/organizations/{oid}/invitations",
            json={"user_id": v, "tier": "viewer"},
            headers=oh,
        )
        vh = _auth(c, v)
        c.post(f"/v1/organizations/{oid}/invitations/{v}/accept", headers=vh)
        assert c.post(f"{base}/deployments", json={"agent_id": aid}, headers=vh).status_code == 403


def test_studio_ui_serves_deployment_screens():
    with _client() as c:
        r = c.get("/studio/")
        assert r.status_code == 200 and "VoxVoice Studio" in r.text
        for marker in (
            "Deployment Dashboard",
            "Deployment Timeline",
            "Approval Queue",
            "Health Viewer",
            "Execution Logs",
            "Rollback Manager",
            "Environment Selector",
            "Deployment History",
        ):
            assert marker in r.text, marker
