"""GA Certification — repository-wide architecture verification (Epic 8, M8.18).

The formal, executable certification the GA sign-off rests on. It checks the invariants the whole
platform must hold — **no new features**, pure verification:

  * the migration chain is contiguous, ordered, and idempotent;
  * every Control Plane router is mounted and every route carries error responses (OpenAPI
    completeness);
  * the dependency graph is acyclic and the frozen-Epic import boundary holds
    (`api/*` and `actuator/` never import a frozen runtime engine, and the frozen Epics never import
    Epic 8);
  * determinism (a `FixedClock` + `SequentialIdGenerator` reproduce byte-identical output);
  * single-writer / immutability sanity (the audit stream is append-only);
  * backward compatibility (the API version is SemVer; the pre-M8.9 activity feed shape survives).
"""

from __future__ import annotations

import ast
import pathlib

from fastapi.testclient import TestClient

from api.customer import Container, CustomerApiSettings, create_app
from api.foundation import FixedClock, FoundationFactory, SequentialIdGenerator
from api.persistence.migrations import MIGRATIONS


def _client():
    s = CustomerApiSettings(environment="testing", database_url="sqlite://")
    return TestClient(
        create_app(container=Container.build(s, ids=SequentialIdGenerator(), clock=FixedClock()))
    )


def _imported_tops(pkg_path: str) -> set[str]:
    tops: set[str] = set()
    for p in pathlib.Path(pkg_path).rglob("*.py"):
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.ImportFrom) and n.module and not n.level:
                tops.add(n.module.split(".")[0])
            elif isinstance(n, ast.Import):
                for a in n.names:
                    tops.add(a.name.split(".")[0])
    return tops


# ── migration chain ─────────────────────────────────────────────────────────────


def test_migration_chain_is_contiguous_and_ordered():
    versions = [int(m.VERSION) for m in MIGRATIONS]
    # the numbered chain must be contiguous 0001..N with no gaps or reordering
    assert versions == sorted(versions), "migrations must be in ascending order"
    assert versions == list(range(1, 1 + len(versions))), "no gaps"
    assert versions[0] == 1 and versions[-1] == 12  # 0001 (initial) … 0012 (M8.17)
    # every migration exposes upgrade + downgrade (idempotent by construction)
    for m in MIGRATIONS:
        assert hasattr(m, "upgrade") and hasattr(m, "downgrade")


def test_migrations_are_idempotent_on_fresh_db():
    from api.persistence import Database

    db = Database("sqlite://")
    db.create_all()
    # applying the whole chain twice must not raise (each migration is create-if-absent)
    for _ in range(2):
        for m in MIGRATIONS:
            m.upgrade(db)
    db.close()


# ── OpenAPI completeness + router mounting ─────────────────────────────────────────


def test_all_routers_mounted_and_openapi_complete():
    with _client() as c:
        schema = c.get("/openapi.json").json()
        paths = schema["paths"]
        # every Epic-8 surface is present
        for needle in (
            "/v1/auth/register",
            "/v1/organizations",
            "/observability/metrics",
            "/deployments",
            "/knowledge/collections",
            "/agents",
            "/tools",
            "/health",
            "/ready",
            "/version",
        ):
            assert any(needle in p for p in paths), needle
        # OpenAPI completeness: every non-system operation declares error responses
        missing = []
        for path, ops in paths.items():
            if path in ("/health", "/ready", "/version") or path.startswith("/studio"):
                continue
            for method, op in ops.items():
                codes = set(op.get("responses", {}))
                if not ({"401", "403", "404"} & codes):
                    missing.append(f"{method.upper()} {path}")
        assert not missing, f"operations missing error responses: {missing[:5]}"


def test_semver_api_version():
    with _client() as c:
        version = c.get("/version").json()["api"]
    parts = version.split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts), version


# ── dependency boundaries + frozen-Epic freeze ─────────────────────────────────────

_FROZEN_ENGINE = {
    "runtime",
    "gateway",
    "replay",
    "memory",
    "reasoning",
    "provider",
    "providers",
    "bridge",
    "pipeline",
    "voicebot",
    "loop",
    "mcp",
    "workflow",
    "skill",
    "telemetry",
}


def test_epic8_control_plane_does_not_import_frozen_runtime():
    # the Epic-8 control plane binds to core/tenant/product (read-only), never a runtime engine
    for pkg in ("api/foundation", "api/application", "api/customer"):
        used = _imported_tops(pkg)
        assert not (used & _FROZEN_ENGINE), f"{pkg} imports frozen runtime: {used & _FROZEN_ENGINE}"


def test_actuator_dependency_direction():
    used = _imported_tops("actuator")
    # actuator → product (read-only), core, tenant + its own package; never the control plane
    assert "api" not in used, "actuator must not import the control plane (api.*)"
    assert not (used & (_FROZEN_ENGINE - {"telemetry"})), used & _FROZEN_ENGINE


def test_frozen_epics_never_import_epic8():
    # the freeze is one-way: no frozen Epic imports Epic 8 (api.* or actuator.*)
    for engine in ("runtime", "product"):
        if not pathlib.Path(engine).is_dir():
            continue
        used = _imported_tops(engine)
        assert "api" not in used and "actuator" not in used, f"{engine} imports Epic 8"


# ── determinism + immutability ─────────────────────────────────────────────────────


def test_determinism_reproducible():
    def run():
        f = FoundationFactory.default(ids=SequentialIdGenerator(), clock=FixedClock())
        from api.foundation import RegisterUserRequest

        u = f.identity.register_user(RegisterUserRequest("a@b.io", "A"))
        return u.id

    assert run() == run()  # identical seams → identical ids (byte-reproducible)


def test_audit_stream_is_append_only():
    from api.foundation.events import FoundationEvent, FoundationEventType

    f = FoundationFactory.default(ids=SequentialIdGenerator(), clock=FixedClock())
    f.emitter.emit(
        FoundationEvent(
            id="e1",
            type=FoundationEventType.ORGANIZATION_CREATED,
            ts=1.0,
            org_id="o1",
            actor="u1",
            resource="o1",
            data={},
        )
    )
    reader = f.audit_reader
    # the read seam exposes query + stream but no mutate/delete (append-only by construction)
    assert hasattr(reader, "query") and hasattr(reader, "stream")
    assert not any(hasattr(reader, m) for m in ("update", "delete", "remove"))


def test_backward_compatible_activity_feed_shape():
    # the pre-M8.9 feed item shape (type/ts/actor/resource/data) survives — no breaking change
    with _client() as c:
        uid = c.post("/v1/auth/register", json={"email": "f@a.io", "display_name": "U"}).json()[
            "id"
        ]
        tok = c.post("/v1/auth/token", json={"user_id": uid}).json()["access_token"]
        oh = {"Authorization": f"Bearer {tok}"}
        oid = c.post("/v1/organizations", json={"name": "A"}, headers=oh).json()["id"]
        feed = c.get(f"/v1/organizations/{oid}/activity", headers=oh).json()["feed"]
        assert feed and {"type", "ts", "actor", "resource", "data"} <= set(feed[0])
