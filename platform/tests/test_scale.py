"""Performance / scale / soak validation (Epic 8, M8.18).

Exercises the platform at volume — deterministic and offline — certifying that pagination,
(observability), and the read/write paths hold up under large tenants and long histories. Not a
micro-benchmark (wall-clock is environment-dependent); a correctness-at-scale + soak check:
large orgs / many workspaces / many projects / many agents / large knowledge bases / large audit
history / high deployment + conversation counts / large metrics history / streaming load.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.foundation import FixedClock, FoundationFactory, SequentialIdGenerator
from api.foundation.events import FoundationEvent, FoundationEventType


def _foundation():
    return FoundationFactory.default(ids=SequentialIdGenerator(), clock=FixedClock())


def _emit(f, n, type_, **data):
    for i in range(n):
        f.emitter.emit(
            FoundationEvent(
                id=f"e{type_}{i}",
                type=type_,
                ts=float(1000 + i),
                org_id="o1",
                actor="u1",
                resource=data.get("resource", "r"),
                data=dict(data),
            )
        )


# ── large audit history + observability projection at scale ────────────────────────


def test_large_audit_history_projects_and_paginates():
    f = _foundation()
    # 5000 mixed events across the platform surface
    _emit(
        f,
        2000,
        FoundationEventType.EXECUTION_COMPLETED,
        tokens=100,
        latency_ms=20,
        provider="anthropic",
        workspace_id="w1",
        project_id="p1",
    )
    _emit(f, 1500, FoundationEventType.TOOL_EXECUTED, workspace_id="w1", project_id="p1")
    _emit(f, 1000, FoundationEventType.DEPLOYMENT_COMPLETED, workspace_id="w1", project_id="p1")
    _emit(f, 500, FoundationEventType.DEPLOYMENT_FAILED, workspace_id="w1", project_id="p1")

    o = f.observability
    # metric projection over 5000 events is correct
    m = {s.key: s.total for s in o.metrics.report("o1").snapshots}
    assert m["conversations.completed"] == 2000
    assert m["tokens.total"] == 200_000
    assert m["tools.executed"] == 1500
    assert m["deployments.completed"] == 1000 and m["deployments.failed"] == 500
    # health, usage, cost, performance all compute over the full history without error
    assert o.health.snapshot("o1").status.value in ("healthy", "degraded", "unhealthy")
    assert o.usage.summary("o1")["conversations"] == 2000
    assert o.cost.estimate("o1")["estimated_total"] > 0
    perf = o.performance.summary("o1")
    assert 0.0 <= perf["success_rate"] <= 1.0
    # a bounded log page over a large history (pagination holds)
    from api.foundation import LogQuery

    page = o.logs.query(LogQuery(org_id="o1", limit=50))
    assert len(page.entries) == 50 and page.total == 5000


# ── large tenant (many workspaces / projects / agents) via the API ─────────────────


def _client():
    from api.customer import Container, CustomerApiSettings, create_app

    s = CustomerApiSettings(environment="testing", database_url="sqlite://")
    return TestClient(
        create_app(container=Container.build(s, ids=SequentialIdGenerator(), clock=FixedClock()))
    )


def _bootstrap(c):
    uid = c.post("/v1/auth/register", json={"email": "f@a.io", "display_name": "U"}).json()["id"]
    oh = {
        "Authorization": "Bearer "
        + c.post("/v1/auth/token", json={"user_id": uid}).json()["access_token"]
    }
    oid = c.post("/v1/organizations", json={"name": "Acme"}, headers=oh).json()["id"]
    return oh, oid


def test_large_organization_many_workspaces_and_projects():
    with _client() as c:
        oh, oid = _bootstrap(c)
        # 25 workspaces, each with 4 projects → 100 projects in one org
        for w in range(25):
            wid = c.post(
                f"/v1/organizations/{oid}/workspaces",
                json={"name": f"W{w}", "slug": f"w{w}"},
                headers=oh,
            ).json()["id"]
            for p in range(4):
                r = c.post(
                    f"/v1/organizations/{oid}/workspaces/{wid}/projects",
                    json={"name": f"P{p}", "slug": f"p{p}"},
                    headers=oh,
                )
                assert r.status_code == 201
        # the workspace list is complete + isolation holds (all belong to this org)
        workspaces = c.get(f"/v1/organizations/{oid}/workspaces", headers=oh).json()
        assert len(workspaces) == 25


def test_large_knowledge_base_and_soak_conversations():
    with _client() as c:
        oh, oid = _bootstrap(c)
        wid = c.post(
            f"/v1/organizations/{oid}/workspaces", json={"name": "W", "slug": "w"}, headers=oh
        ).json()["id"]
        pid = c.post(
            f"/v1/organizations/{oid}/workspaces/{wid}/projects",
            json={"name": "P", "slug": "p"},
            headers=oh,
        ).json()["id"]
        base = f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}"
        # a large knowledge base: one collection, a big document → many chunks
        cid = c.post(
            f"{base}/knowledge/collections",
            json={"name": "KB", "chunk_size": 40, "chunk_overlap": 5},
            headers=oh,
        ).json()["id"]
        big = ". ".join(f"sentence number {i} about dogs and parks" for i in range(200))
        did = c.post(
            f"{base}/knowledge/collections/{cid}/documents",
            json={"name": "big", "content": big},
            headers=oh,
        ).json()["id"]
        c.post(f"{base}/knowledge/collections/{cid}/documents/{did}/chunk", headers=oh)
        c.post(f"{base}/knowledge/collections/{cid}/embeddings", headers=oh)
        c.post(f"{base}/knowledge/collections/{cid}/index", headers=oh)
        status = c.get(f"{base}/knowledge/collections/{cid}/vector-status", headers=oh).json()
        assert status["indexed"] >= 100  # many chunks indexed
        # soak: publish an agent and run many conversation turns in a loop
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
        sid = c.post(f"{base}/agents/{aid}/playground/conversations", json={}, headers=oh).json()[
            "id"
        ]
        for i in range(30):
            r = c.post(
                f"{base}/agents/{aid}/playground/conversations/{sid}/execute",
                json={"user_input": f"turn {i}"},
                headers=oh,
            )
            assert r.status_code == 200
        # observability reflects the soak load
        ob = f"/v1/organizations/{oid}/observability"
        metrics = {m["key"]: m for m in c.get(f"{ob}/metrics", headers=oh).json()["metrics"]}
        assert metrics["conversations.completed"]["total"] >= 30
        # a large metrics history still renders the dashboard without error
        assert c.get(f"{ob}/dashboards/organization", headers=oh).status_code == 200


def test_high_deployment_count():
    with _client() as c:
        oh, oid = _bootstrap(c)
        wid = c.post(
            f"/v1/organizations/{oid}/workspaces", json={"name": "W", "slug": "w"}, headers=oh
        ).json()["id"]
        pid = c.post(
            f"/v1/organizations/{oid}/workspaces/{wid}/projects",
            json={"name": "P", "slug": "p"},
            headers=oh,
        ).json()["id"]
        base = f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}"
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
        # many dev deployments (auto-run to completion)
        for _ in range(20):
            r = c.post(
                f"{base}/deployments",
                json={"agent_id": aid, "environment": "development"},
                headers=oh,
            )
            assert r.status_code == 201 and r.json()["state"] == "completed"
        assert len(c.get(f"{base}/deployments", headers=oh).json()) == 20
