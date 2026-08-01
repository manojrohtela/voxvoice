"""Durable Audit & Activity Platform (Epic 8, M8.9) tests.

Covers the hardening end-to-end: Foundation read-model unit behaviour (filtering/pagination/
time-range over the in-memory reader), durable persistence (append-only rows, query, stream),
**durability across a simulated restart** (a fresh app over the same on-disk database keeps all
history), the five projections (organization/workspace/project/recent/user), large-history
pagination, projection rebuild from the durable log, the additive API params + recent/user
endpoints, and a regression guard that the existing Organization/Workspace/Project activity
responses are unchanged in shape.
"""

from __future__ import annotations

import os
import tempfile

from fastapi.testclient import TestClient

from api.application import (
    CreateOrganization,
    CreateProject,
    CreateWorkspace,
    RegisterUser,
    UpdateProject,
)
from api.customer import Container, CustomerApiSettings, create_app
from api.foundation import (
    AuditQuery,
    CreateOrganizationRequest,
    CreateProjectRequest,
    CreateWorkspaceRequest,
    FixedClock,
    FoundationFactory,
    RegisterUserRequest,
    SequentialIdGenerator,
)
from api.persistence import build_persistent_application


def _seed_via_app(app):
    """Seed through the command bus so every write + audit row is committed by the
    SqlUnitOfWork (durable), not merely flushed on an uncommitted session."""
    owner = app.commands.dispatch(RegisterUser(email="owner@acme.io", display_name="O"))
    org = app.commands.dispatch(CreateOrganization(name="Acme", owner_user_id=owner.id))
    ws = app.commands.dispatch(
        CreateWorkspace(actor_user_id=owner.id, org_id=org.id, name="Team", slug="team")
    )
    p = app.commands.dispatch(
        CreateProject(actor_user_id=owner.id, org_id=org.id, workspace_id=ws.id,
                      name="P", slug="p")
    )
    app.commands.dispatch(
        UpdateProject(actor_user_id=owner.id, org_id=org.id, workspace_id=ws.id,
                      project_id=p.id, description="d")
    )
    return owner, org, ws, p

# ── Foundation read-model unit (in-memory reader) ────────────────────────────────


def _foundation():
    return FoundationFactory.default(ids=SequentialIdGenerator(), clock=FixedClock())


def _seed(f):
    owner = f.identity.register_user(RegisterUserRequest("owner@acme.io", "O"))
    org = f.identity.create_organization(
        CreateOrganizationRequest(name="Acme", owner_user_id=owner.id)
    )
    ws = f.workspaces.create_workspace(
        owner.id, CreateWorkspaceRequest(org_id=org.id, name="Team", slug="team")
    )
    p = f.projects.create_project(
        owner.id, CreateProjectRequest(org_id=org.id, workspace_id=ws.id, name="P", slug="p")
    )
    f.project_lifecycle.update_project(owner.id, org.id, ws.id, p.id, description="d")
    return owner, org, ws, p


def test_reader_filter_and_pagination():
    f = _foundation()
    owner, org, ws, p = _seed(f)
    reader = f.audit_reader
    # all org events, most-recent-first
    page = reader.query(AuditQuery(org_id=org.id))
    assert page.total == 4
    assert page.records[0].seq > page.records[-1].seq  # newest first
    # pagination
    first = reader.query(AuditQuery(org_id=org.id, limit=2, offset=0))
    second = reader.query(AuditQuery(org_id=org.id, limit=2, offset=2))
    assert len(first.records) == 2 and first.has_more and first.next_offset == 2
    assert len(second.records) == 2 and not second.has_more
    assert {r.seq for r in first.records}.isdisjoint({r.seq for r in second.records})
    # filter by event type
    only_created = reader.query(AuditQuery(org_id=org.id, event_types=("project.created",)))
    assert [r.type for r in only_created.records] == ["project.created"]
    # workspace/project scoping
    assert reader.query(AuditQuery(org_id=org.id, workspace_id=ws.id)).total >= 2
    assert reader.query(AuditQuery(org_id=org.id, project_id=p.id)).total == 2


def test_reader_time_range_and_actor():
    f = _foundation()
    owner, org, ws, p = _seed(f)
    records = f.audit_reader.query(AuditQuery(org_id=org.id)).records
    mid = sorted(r.ts for r in records)[2]
    since = f.audit_reader.query(AuditQuery(org_id=org.id, since=mid))
    until = f.audit_reader.query(AuditQuery(org_id=org.id, until=mid))
    assert all(r.ts >= mid for r in since.records)
    assert all(r.ts <= mid for r in until.records)
    # actor filter — every seeded event was caused by the owner
    assert f.audit_reader.query(AuditQuery(org_id=org.id, actor=owner.id)).total == 4
    assert f.audit_reader.query(AuditQuery(org_id=org.id, actor="nobody")).total == 0


def test_projections_org_workspace_project_recent_user():
    f = _foundation()
    owner, org, ws, p = _seed(f)
    org_feed = [x["type"] for x in f.activity.organization_activity(org.id)["feed"]]
    assert "organization.created" in org_feed and "project.created" in org_feed
    ws_feed = [x["type"] for x in f.activity.workspace_activity(org.id, ws.id)["feed"]]
    assert "workspace.created" in ws_feed
    proj_feed = [x["type"] for x in f.activity.project_activity(org.id, ws.id, p.id)["feed"]]
    assert set(proj_feed) == {"project.created", "project.settings_changed"}
    assert f.activity.recent_activity(org.id)["total"] == 4
    assert f.activity.user_activity(org.id, owner.id)["total"] == 4


def test_cross_org_isolation_in_projections():
    f = _foundation()
    _, org_a, _, _ = _seed(f)
    owner_b = f.identity.register_user(RegisterUserRequest("b@x.io", "B"))
    org_b = f.identity.create_organization(
        CreateOrganizationRequest(name="B", owner_user_id=owner_b.id)
    )
    # org B's feed never contains org A's events (isolation is structural)
    assert all(
        r["type"] == "organization.created"
        for r in f.activity.organization_activity(org_b.id)["feed"]
    )
    assert f.activity.organization_activity(org_b.id)["total"] == 1


# ── durable persistence + restart ────────────────────────────────────────────────


def test_durable_append_query_and_stream():
    pa = build_persistent_application(ids=SequentialIdGenerator(), clock=FixedClock())
    app = pa.application
    owner, org, ws, p = _seed_via_app(app)
    feed = app.foundation.activity.organization_activity(org.id)["feed"]
    assert {"organization.created", "project.created"} <= {x["type"] for x in feed}
    # stream() yields the whole org log oldest-first (rebuild basis)
    stream = list(app.foundation.audit_reader.stream(org.id))
    assert [r.type for r in stream][0] == "organization.created"
    assert [r.seq for r in stream] == sorted(r.seq for r in stream)
    pa.database.close()


def test_history_survives_restart():
    path = tempfile.mktemp(suffix=".db")
    url = f"sqlite:///{path}"
    try:
        pa = build_persistent_application(
            url=url, ids=SequentialIdGenerator(), clock=FixedClock()
        )
        _, org, _, _ = _seed_via_app(pa.application)
        before = pa.application.foundation.activity.organization_activity(org.id)["total"]
        pa.database.close()
        # fresh process: new app over the SAME database file, no create_all
        pa2 = build_persistent_application(
            url=url, ids=SequentialIdGenerator(), clock=FixedClock(), create_all=False
        )
        after = pa2.application.foundation.activity.organization_activity(org.id)
        assert after["total"] == before == 4
        assert {"organization.created", "project.created"} <= {
            x["type"] for x in after["feed"]
        }
        pa2.database.close()
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_large_history_pagination():
    pa = build_persistent_application(ids=SequentialIdGenerator(), clock=FixedClock())
    app = pa.application
    owner = app.foundation.identity.register_user(RegisterUserRequest("o@a.io", "O"))
    org = app.foundation.identity.create_organization(
        CreateOrganizationRequest(name="Acme", owner_user_id=owner.id)
    )
    # generate a large history: 80 workspace.created events (+ org.created)
    for i in range(80):
        app.foundation.workspaces.create_workspace(
            owner.id, CreateWorkspaceRequest(org_id=org.id, name=f"W{i}", slug=f"w-{i}")
        )
    total = app.foundation.activity.organization_activity(org.id, limit=1)["total"]
    assert total == 81
    # walk every page and confirm full, non-overlapping coverage in newest-first order
    seen, offset, last_seq = [], 0, None
    while True:
        page = app.foundation.activity.organization_activity(org.id, limit=25, offset=offset)
        for item in page["feed"]:
            if last_seq is not None:
                assert item["seq"] < last_seq  # strictly descending
            last_seq = item["seq"]
        seen.extend(page["feed"])
        if not page["has_more"]:
            break
        offset = page["next_offset"]
    assert len(seen) == 81 and len({x["seq"] for x in seen}) == 81
    pa.database.close()


def test_projection_rebuild_from_durable_log():
    path = tempfile.mktemp(suffix=".db")
    url = f"sqlite:///{path}"
    try:
        pa = build_persistent_application(
            url=url, ids=SequentialIdGenerator(), clock=FixedClock()
        )
        _, org, ws, p = _seed_via_app(pa.application)
        original = [x["type"] for x in pa.application.foundation.activity.project_activity(
            org.id, ws.id, p.id
        )["feed"]]
        pa.database.close()
        # rebuild: a brand-new reader over the persisted, append-only log reproduces the
        # exact projection (projections are pure functions of the durable event log).
        pa2 = build_persistent_application(
            url=url, ids=SequentialIdGenerator(), clock=FixedClock(), create_all=False
        )
        rebuilt = [x["type"] for x in pa2.application.foundation.activity.project_activity(
            org.id, ws.id, p.id
        )["feed"]]
        assert rebuilt == original
        pa2.database.close()
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_audit_store_is_append_only():
    from api.persistence import SqlAuditStore

    # the durable store exposes only append (emit) + read (query/stream) — no update/delete.
    for banned in ("update", "delete", "remove"):
        assert not hasattr(SqlAuditStore, banned), f"audit store must not expose {banned!r}"


# ── API integration (durable, additive params + endpoints) ───────────────────────


def _client() -> TestClient:
    settings = CustomerApiSettings(environment="testing", database_url="sqlite://")
    container = Container.build(settings, ids=SequentialIdGenerator(), clock=FixedClock())
    return TestClient(create_app(container=container))


def _register(c, email):
    return c.post("/v1/auth/register", json={"email": email, "display_name": "U"}).json()["id"]


def _auth(c, uid):
    tok = c.post("/v1/auth/token", json={"user_id": uid}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def test_api_activity_params_and_new_endpoints():
    with _client() as c:
        owner = _register(c, "founder@acme.io")
        oh = _auth(c, owner)
        oid = c.post("/v1/organizations", json={"name": "Acme"}, headers=oh).json()["id"]
        wid = c.post(f"/v1/organizations/{oid}/workspaces",
                     json={"name": "Team", "slug": "team"}, headers=oh).json()["id"]
        c.post(f"/v1/organizations/{oid}/workspaces/{wid}/projects",
               json={"name": "P", "slug": "p"}, headers=oh)
        # existing route still returns summary + feed (backward-compatible) + pagination meta
        base = c.get(f"/v1/organizations/{oid}/activity", headers=oh).json()
        assert "summary" in base and "feed" in base and base["total"] == 3
        # pagination
        pg = c.get(f"/v1/organizations/{oid}/activity",
                   params={"limit": 2}, headers=oh).json()
        assert pg["has_more"] is True and pg["next_offset"] == 2 and len(pg["feed"]) == 2
        # event-type filter
        ft = c.get(f"/v1/organizations/{oid}/activity",
                   params={"event_type": ["project.created"]}, headers=oh).json()
        assert [f["type"] for f in ft["feed"]] == ["project.created"]
        # recent + user endpoints (new, additive)
        rec = c.get(f"/v1/organizations/{oid}/activity/recent", headers=oh).json()
        assert rec["total"] == 3 and "summary" not in rec
        usr = c.get(f"/v1/organizations/{oid}/activity/users/{owner}", headers=oh).json()
        assert usr["total"] == 3
        assert c.get(f"/v1/organizations/{oid}/activity/users/nobody",
                     headers=oh).json()["total"] == 0


def test_api_activity_survives_restart_over_file_db():
    path = tempfile.mktemp(suffix=".db")
    url = f"sqlite:///{path}"
    try:
        settings = CustomerApiSettings(environment="testing", database_url=url)
        container = Container.build(settings, ids=SequentialIdGenerator(), clock=FixedClock())
        with TestClient(create_app(container=container)) as c:
            owner = _register(c, "d@acme.io")
            oh = _auth(c, owner)
            oid = c.post("/v1/organizations", json={"name": "Acme"}, headers=oh).json()["id"]
            before = c.get(f"/v1/organizations/{oid}/activity", headers=oh).json()["total"]
        container.database.close()
        # new container/app over the same file — history is intact after "restart"
        container2 = Container.build(
            CustomerApiSettings(environment="testing", database_url=url),
            ids=SequentialIdGenerator(), clock=FixedClock(),
        )
        with TestClient(create_app(container=container2)) as c2:
            oh2 = _auth(c2, owner)
            after = c2.get(f"/v1/organizations/{oid}/activity", headers=oh2).json()
            assert after["total"] == before >= 1
            assert "organization.created" in [f["type"] for f in after["feed"]]
        container2.database.close()
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_studio_activity_screens_have_filters_and_infinite_scroll():
    with _client() as c:
        r = c.get("/studio/")
        assert r.status_code == 200
        # the reusable durable activity feed ships filter + time-range + load-more controls
        assert "event type(s)" in r.text and "Load more" in r.text
        assert 'type="datetime-local"' in r.text
