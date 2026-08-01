"""Observability Platform vertical slice (Epic 8, M8.17) tests.

Covers Observability as a **passive read-model** over the durable audit stream: metric derivation,
log level/category projection, distributed-trace grouping, health aggregation, usage + estimated
cost, performance, the alert evaluator + on-demand alert manager, and the full Control Plane API +
Studio **Definition-of-Done** flow (observe conversations/deployments/providers/knowledge/tools;
view traces/logs/metrics/health/alerts/usage/cost). Also audit-non-duplication, authorization, and
org/workspace/project isolation. **No polling, no execution, no new metric events.**
"""

from __future__ import annotations

import itertools

from fastapi.testclient import TestClient

from api.customer import Container, CustomerApiSettings, create_app
from api.foundation import (
    AlertComparator,
    AlertRule,
    AlertSeverity,
    FixedClock,
    FoundationFactory,
    HealthStatus,
    LogCategory,
    LogLevel,
    LogQuery,
    SequentialIdGenerator,
)
from api.foundation.events import FoundationEvent, FoundationEventType
from api.foundation.observability import AlertEvaluator

# ── foundation projection unit tests (seed events → read projections) ─────────────


def _seed():
    f = FoundationFactory.default(ids=SequentialIdGenerator(), clock=FixedClock())
    c = itertools.count()

    def ev(t, **d):
        f.emitter.emit(
            FoundationEvent(
                id="e" + str(next(c)),
                type=t,
                ts=d.pop("ts", 100.0),
                org_id="o1",
                actor=d.pop("actor", "u1"),
                resource=d.pop("res", "r"),
                data=d,
            )
        )

    ev(
        FoundationEventType.EXECUTION_COMPLETED,
        tokens=1000,
        latency_ms=50,
        provider="anthropic",
        workspace_id="w1",
        project_id="p1",
        session_id="s1",
    )
    ev(
        FoundationEventType.EXECUTION_COMPLETED,
        tokens=500,
        latency_ms=30,
        provider="openai",
        workspace_id="w1",
        project_id="p1",
        session_id="s2",
    )
    ev(
        FoundationEventType.TOOL_EXECUTED,
        workspace_id="w1",
        project_id="p1",
        session_id="s1",
        tool_id="t1",
    )
    ev(FoundationEventType.KNOWLEDGE_RETRIEVED, workspace_id="w1", project_id="p1", hits=3)
    ev(
        FoundationEventType.DEPLOYMENT_COMPLETED,
        workspace_id="w1",
        project_id="p1",
        deployment_id="d1",
        environment="production",
    )
    ev(
        FoundationEventType.DEPLOYMENT_FAILED,
        workspace_id="w1",
        project_id="p1",
        deployment_id="d2",
        error="boom",
    )
    return f


def test_metric_derivation():
    o = _seed().observability
    m = {s.key: s for s in o.metrics.report("o1").snapshots}
    assert m["tokens.total"].total == 1500
    assert m["conversations.completed"].total == 2
    assert m["tools.executed"].total == 1
    assert m["knowledge.retrieved"].total == 1
    assert m["deployments.completed"].total == 1
    assert m["deployments.failed"].total == 1
    assert abs(m["latency.ms"].average - 40.0) < 1e-9  # (50+30)/2


def test_metric_scope_and_series():
    o = _seed().observability
    # project scope filters
    assert o.metrics.value("o1", "tokens.total", project_id="p1") == 1500
    assert o.metrics.value("o1", "tokens.total", project_id="does-not-exist") == 0
    series = o.metrics.series("o1", "tokens.total", bucket_seconds=60.0)
    assert sum(p.value for p in series.points) == 1500


def test_log_projection_levels_and_categories():
    o = _seed().observability
    rep = o.logs.query(LogQuery(org_id="o1"))
    by_type = {e.event_type: e for e in rep.entries}
    assert by_type["deployment.failed"].level is LogLevel.ERROR
    assert by_type["deployment.failed"].category is LogCategory.DEPLOYMENT
    assert by_type["execution.completed"].level is LogLevel.INFO
    assert by_type["execution.completed"].category is LogCategory.RUNTIME
    assert by_type["knowledge.retrieved"].category is LogCategory.KNOWLEDGE
    # level filter
    errs = o.logs.query(LogQuery(org_id="o1", level=LogLevel.ERROR))
    assert all(e.level is LogLevel.ERROR for e in errs.entries) and errs.total >= 1


def test_trace_grouping():
    o = _seed().observability
    report = o.traces.traces("o1")
    # session s1 groups its execution + tool spans into one trace
    multi = [t for t in report.traces if len(t.spans) >= 2]
    assert multi, "expected at least one multi-span trace (correlated events)"
    # a deployment.failed trace is marked error
    assert any(t.status == "error" for t in report.traces)


def test_health_aggregation_ignores_no_signal_components():
    o = _seed().observability
    agg = o.health.snapshot("o1")
    comps = {c.component: c.status for c in agg.components}
    # deployment has 1 ok / 1 fail → 50% → unhealthy
    assert comps["deployment"] is HealthStatus.UNHEALTHY
    # runtime is all-success → healthy
    assert comps["runtime"] is HealthStatus.HEALTHY
    # overall = worst component *with signal* (unhealthy), never dragged to UNKNOWN
    assert agg.status is HealthStatus.UNHEALTHY


def test_usage_and_cost_estimate():
    o = _seed().observability
    usage = o.usage.summary("o1")
    assert usage["tokens"] == 1500 and usage["conversations"] == 2
    assert usage["by_provider"] == {"anthropic": 1, "openai": 1}
    cost = o.cost.estimate("o1")
    assert cost["token_cost"] == round(1500 * 0.000002, 6)
    assert cost["estimated_total"] > 0 and cost["currency"] == "USD"
    assert set(cost["provider_breakdown"]) == {"anthropic", "openai"}


def test_performance_summary():
    o = _seed().observability
    perf = o.performance.summary("o1")
    assert perf["avg_latency_ms"] == 40.0 and perf["executions"] == 2
    assert perf["success_rate"] == 1.0  # 2 completed, 0 runtime failures


def test_alert_evaluator_comparators():
    ev = AlertEvaluator()
    rule = AlertRule(
        id="a1",
        org_id="o1",
        name="hi",
        metric_key="tokens.total",
        comparator=AlertComparator.GT,
        threshold=1000.0,
        severity=AlertSeverity.WARNING,
    )
    assert ev.evaluate(rule, 1500.0, 1.0).state.value == "firing"
    assert ev.evaluate(rule, 500.0, 1.0).state.value == "ok"


# ── Control Plane API + Studio (Definition of Done) ────────────────────────────────


def _client() -> TestClient:
    settings = CustomerApiSettings(environment="testing", database_url="sqlite://")
    return TestClient(
        create_app(
            container=Container.build(settings, ids=SequentialIdGenerator(), clock=FixedClock())
        )
    )


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
    return oh, oid, wid, pid, f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}"


def _generate_events(c, oh, base):
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
    c.post(f"{base}/deployments", json={"agent_id": aid, "environment": "development"}, headers=oh)
    sid = c.post(f"{base}/agents/{aid}/playground/conversations", json={}, headers=oh).json()["id"]
    c.post(
        f"{base}/agents/{aid}/playground/conversations/{sid}/execute",
        json={"user_input": "hi"},
        headers=oh,
    )
    return aid


def test_definition_of_done_end_to_end():
    with _client() as c:
        oh, oid, wid, pid, base = _bootstrap(c)
        _generate_events(c, oh, base)
        ob = f"/v1/organizations/{oid}/observability"
        # 1. observe conversations, 3. providers, 4. knowledge, 5. tools, 8. metrics
        metrics = {m["key"]: m for m in c.get(f"{ob}/metrics", headers=oh).json()["metrics"]}
        assert metrics["conversations.completed"]["total"] >= 1
        assert metrics["tokens.total"]["total"] >= 1
        # 2. observe deployments
        assert metrics["deployments.completed"]["total"] >= 1
        # 6. view traces
        assert c.get(f"{ob}/traces", headers=oh).json()["total"] >= 1
        # 7. view logs
        assert c.get(f"{ob}/logs", headers=oh).json()["total"] >= 1
        # 9. view health
        assert c.get(f"{ob}/health", headers=oh).json()["status"] in (
            "healthy",
            "degraded",
            "unhealthy",
            "unknown",
        )
        # 11. usage analytics + 12. estimated cost
        assert c.get(f"{ob}/usage", headers=oh).json()["conversations"] >= 1
        assert c.get(f"{ob}/cost", headers=oh).json()["estimated_total"] >= 0
        # dashboards (all 8 kinds resolve)
        for kind in (
            "organization",
            "workspace",
            "project",
            "agent",
            "deployment",
            "knowledge",
            "provider",
            "system",
        ):
            r = c.get(f"{ob}/dashboards/{kind}?workspace_id={wid}&project_id={pid}", headers=oh)
            assert r.status_code == 200 and r.json()["kind"] == kind
        # 10. view alerts (create rule → evaluate → history → delete)
        rid = c.post(
            f"{ob}/alerts",
            json={
                "name": "deploys",
                "metric_key": "deployments.completed",
                "comparator": "gte",
                "threshold": 1,
            },
            headers=oh,
        ).json()["id"]
        assert c.get(f"{ob}/alerts/evaluate", headers=oh).json()["alerts"][0]["state"] == "firing"
        assert len(c.get(f"{ob}/alerts/{rid}/history", headers=oh).json()["events"]) >= 1
        assert c.delete(f"{ob}/alerts/{rid}", headers=oh).status_code == 200


def test_observability_does_not_duplicate_audit_events():
    with _client() as c:
        oh, oid, wid, pid, base = _bootstrap(c)
        _generate_events(c, oh, base)
        ob = f"/v1/organizations/{oid}/observability"
        before = c.get(f"/v1/organizations/{oid}/activity?limit=200", headers=oh).json()["total"]
        # reading every projection must NOT append events (passive consumer)
        for path in (
            "/metrics",
            "/logs",
            "/traces",
            "/health",
            "/usage",
            "/cost",
            "/performance",
            "/dashboards/organization",
        ):
            c.get(ob + path, headers=oh)
        after = c.get(f"/v1/organizations/{oid}/activity?limit=200", headers=oh).json()["total"]
        assert after == before  # projections add no facts


def test_alert_rule_audited():
    with _client() as c:
        oh, oid, wid, pid, base = _bootstrap(c)
        ob = f"/v1/organizations/{oid}/observability"
        c.post(
            f"{ob}/alerts",
            json={"name": "r", "metric_key": "tokens.total", "comparator": "gt", "threshold": 0},
            headers=oh,
        )
        types = {
            f["type"] for f in c.get(f"/v1/organizations/{oid}/activity", headers=oh).json()["feed"]
        }
        assert "observability.alert_rule_created" in types


def test_viewer_can_read_but_not_configure_alerts():
    with _client() as c:
        oh, oid, wid, pid, base = _bootstrap(c)
        _generate_events(c, oh, base)
        ob = f"/v1/organizations/{oid}/observability"
        v = c.post("/v1/auth/register", json={"email": "v@a.io", "display_name": "V"}).json()["id"]
        c.post(
            f"/v1/organizations/{oid}/invitations",
            json={"user_id": v, "tier": "viewer"},
            headers=oh,
        )
        vh = _auth(c, v)
        c.post(f"/v1/organizations/{oid}/invitations/{v}/accept", headers=vh)
        # a viewer may observe
        assert c.get(f"{ob}/metrics", headers=vh).status_code == 200
        assert c.get(f"{ob}/dashboards/organization", headers=vh).status_code == 200
        # but not create an alert rule (requires ORG_MANAGE)
        assert (
            c.post(
                f"{ob}/alerts",
                json={
                    "name": "x",
                    "metric_key": "tokens.total",
                    "comparator": "gt",
                    "threshold": 0,
                },
                headers=vh,
            ).status_code
            == 403
        )


def test_scope_isolation():
    with _client() as c:
        oh, oid, wid, pid, base = _bootstrap(c)
        _generate_events(c, oh, base)
        ob = f"/v1/organizations/{oid}/observability"
        # a second empty project sees no conversation metrics
        p2 = c.post(
            f"/v1/organizations/{oid}/workspaces/{wid}/projects",
            json={"name": "P2", "slug": "p2"},
            headers=oh,
        ).json()["id"]
        m = {
            x["key"]: x
            for x in c.get(f"{ob}/metrics?project_id={p2}", headers=oh).json()["metrics"]
        }
        assert m["conversations.completed"]["total"] == 0
        # unknown metric series → 404
        assert c.get(f"{ob}/metrics/not_a_metric/series", headers=oh).status_code == 404


def test_studio_ui_serves_observability_screens():
    with _client() as c:
        r = c.get("/studio/")
        assert r.status_code == 200 and "VoxVoice Studio" in r.text
        for marker in (
            "Metrics Dashboard",
            "Trace Viewer",
            "Log Explorer",
            "Health Dashboard",
            "Usage Dashboard",
            "Cost Dashboard",
            "Alert Center",
            "Provider Analytics",
            "Knowledge Analytics",
            "Deployment Analytics",
        ):
            assert marker in r.text, marker
