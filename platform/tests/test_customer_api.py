"""Control Plane API (Epic 8, M8.5) tests.

Integration tests over the thin FastAPI transport boundary: full HTTP flow (register →
token → create org → workspace → member → role → project → preferences); authentication
(missing/invalid/expired token → 401); dependency injection (an injected in-memory
`Container` fully replaces production wiring); OpenAPI generation (schema + tags + security
scheme + version); health/ready/version; error translation (validation→422,
authorization→403, conflict→409, not-found→404); and middleware (correlation id, request id,
security headers). Also asserts the controllers own no business logic — no repository /
transaction / permission-evaluation symbol appears in the router source — and that the
lower layers are unchanged (they never import `api.customer`).
"""

from __future__ import annotations

import ast
import pathlib

from fastapi.testclient import TestClient

from api.customer import Container, CustomerApiSettings, create_app
from api.foundation import FixedClock, SequentialIdGenerator


def _client() -> TestClient:
    settings = CustomerApiSettings(environment="testing", database_url="sqlite://")
    container = Container.build(settings, ids=SequentialIdGenerator(), clock=FixedClock())
    return TestClient(create_app(container=container))


def _register(client: TestClient, email: str) -> str:
    r = client.post("/v1/auth/register", json={"email": email, "display_name": "U"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _auth(client: TestClient, user_id: str) -> dict[str, str]:
    r = client.post("/v1/auth/token", json={"user_id": user_id})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ── full flow ────────────────────────────────────────────────────────────────────


def test_full_flow_register_org_workspace_member_role_project():
    with _client() as client:
        owner_id = _register(client, "owner@acme.io")
        h = _auth(client, owner_id)

        org = client.post("/v1/organizations", json={"name": "Acme"}, headers=h)
        assert org.status_code == 201, org.text
        org_id = org.json()["id"]

        ws = client.post(
            f"/v1/organizations/{org_id}/workspaces",
            json={"name": "Web", "slug": "web"}, headers=h,
        )
        assert ws.status_code == 201, ws.text
        ws_id = ws.json()["id"]

        # default dev environment was provisioned by the Application/Foundation
        envs = client.get(f"/v1/organizations/{org_id}/workspaces/{ws_id}/environments", headers=h)
        assert [e["name"] for e in envs.json()] == ["dev"]

        dev_id = _register(client, "dev@acme.io")
        inv = client.post(
            f"/v1/organizations/{org_id}/members", json={"user_id": dev_id}, headers=h
        )
        assert inv.status_code == 201, inv.text

        grant = client.post(
            f"/v1/organizations/{org_id}/members/{dev_id}/roles",
            json={"role": "developer", "scope": "workspace", "scope_id": ws_id}, headers=h,
        )
        assert grant.status_code == 201, grant.text
        assert "developer" in grant.json()["roles"]

        # the developer, using their own token, can now create a project in that workspace
        dh = _auth(client, dev_id)
        proj = client.post(
            f"/v1/organizations/{org_id}/workspaces/{ws_id}/projects",
            json={"name": "Screening", "slug": "screening"}, headers=dh,
        )
        assert proj.status_code == 201, proj.text


def test_preferences_roundtrip_over_http():
    with _client() as client:
        owner_id = _register(client, "o@acme.io")
        h = _auth(client, owner_id)
        org_id = client.post("/v1/organizations", json={"name": "Acme"}, headers=h).json()["id"]
        put = client.put(
            f"/v1/organizations/{org_id}/preferences",
            json={"theme": "light", "favorites": ["agt_1"]}, headers=h,
        )
        assert put.status_code == 200 and put.json()["theme"] == "light"
        got = client.get(f"/v1/organizations/{org_id}/preferences", headers=h)
        assert got.json()["favorites"] == ["agt_1"]


# ── authentication ───────────────────────────────────────────────────────────────


def test_missing_token_is_401():
    with _client() as client:
        r = client.post("/v1/organizations", json={"name": "X"})
        assert r.status_code == 401
        assert r.json()["error"]["type"] == "Unauthorized"


def test_invalid_token_is_401():
    with _client() as client:
        r = client.get("/v1/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
        assert r.status_code == 401


def test_expired_token_is_401():
    with _client() as client:
        settings = client.app.state.container.settings
        owner_id = _register(client, "e@acme.io")
        # mint an already-expired token by issuing with a negative-ttl secret path
        from api.customer.jwt import encode

        token = encode({"sub": owner_id, "exp": 1}, settings.jwt_secret)
        r = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401


def test_me_returns_identity():
    with _client() as client:
        owner_id = _register(client, "me@acme.io")
        r = client.get("/v1/auth/me", headers=_auth(client, owner_id))
        assert r.status_code == 200 and r.json()["user_id"] == owner_id


# ── dependency injection (replaceable wiring) ────────────────────────────────────


def test_injected_container_replaces_production_wiring():
    settings = CustomerApiSettings(environment="testing", database_url="sqlite://")
    container = Container.build(settings, ids=SequentialIdGenerator(), clock=FixedClock())
    app = create_app(container=container)
    with TestClient(app) as client:
        assert client.app.state.container is container  # the injected container is used


# ── error translation ────────────────────────────────────────────────────────────


def test_validation_error_is_422_with_diagnostics():
    with _client() as client:
        r = client.post("/v1/auth/register", json={"email": "not-an-email"})
        assert r.status_code == 422
        body = r.json()["error"]
        assert body["type"] == "FoundationValidationError"
        assert any(d["code"] == "invalid_email" for d in body["diagnostics"])


def test_authorization_error_is_403():
    with _client() as client:
        owner_id = _register(client, "own@acme.io")
        h = _auth(client, owner_id)
        org_id = client.post("/v1/organizations", json={"name": "A"}, headers=h).json()["id"]
        outsider = _register(client, "out@acme.io")
        # invite (so they are a member with no role), then they try a privileged action
        client.post(f"/v1/organizations/{org_id}/members", json={"user_id": outsider}, headers=h)
        oh = _auth(client, outsider)
        r = client.post(
            f"/v1/organizations/{org_id}/workspaces",
            json={"name": "X", "slug": "x"}, headers=oh,
        )
        assert r.status_code == 403
        assert r.json()["error"]["type"] == "AuthorizationError"


def test_conflict_is_409_and_not_found_is_404():
    with _client() as client:
        owner_id = _register(client, "c@acme.io")
        h = _auth(client, owner_id)
        org_id = client.post("/v1/organizations", json={"name": "A"}, headers=h).json()["id"]
        wpath = f"/v1/organizations/{org_id}/workspaces"
        client.post(wpath, json={"name": "W", "slug": "dup"}, headers=h)
        dup = client.post(wpath, json={"name": "W2", "slug": "dup"}, headers=h)
        assert dup.status_code == 409
        missing = client.get("/v1/organizations/org_missing", headers=h)
        assert missing.status_code == 404


# ── health / version ─────────────────────────────────────────────────────────────


def test_health_ready_version():
    with _client() as client:
        assert client.get("/health").json()["status"] == "ok"
        ready = client.get("/ready")
        assert ready.status_code == 200 and ready.json()["ready"] is True
        assert ready.json()["checks"]["database"] is True
        assert client.get("/version").json()["api"] == "1.0.0"


# ── OpenAPI ──────────────────────────────────────────────────────────────────────


def test_openapi_generation():
    with _client() as client:
        spec = client.get("/openapi.json").json()
        assert spec["info"]["version"] == "1.0.0"
        assert "/v1/organizations" in spec["paths"]
        tags = {t["name"] for t in spec["tags"]}
        assert {"auth", "organizations", "members", "system"} <= tags
        # bearer security scheme is present
        schemes = spec["components"]["securitySchemes"]
        assert any(s.get("scheme") == "bearer" for s in schemes.values())


# ── middleware ───────────────────────────────────────────────────────────────────


def test_middleware_sets_correlation_and_security_headers():
    with _client() as client:
        r = client.get("/health", headers={"X-Correlation-ID": "corr-123"})
        assert r.headers["X-Correlation-ID"] == "corr-123"
        assert "X-Request-ID" in r.headers
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert "X-Response-Time-ms" in r.headers


# ── architecture compliance ──────────────────────────────────────────────────────


def _imported_modules(pkg_path: str) -> set[str]:
    mods: set[str] = set()
    for p in pathlib.Path(pkg_path).rglob("*.py"):
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.ImportFrom) and n.module and not n.level:
                mods.add(n.module)
            elif isinstance(n, ast.Import):
                for a in n.names:
                    mods.add(a.name)
    return mods


def test_controllers_have_no_business_logic_or_persistence():
    # Domain controllers only (the system/health router legitimately pings the DB session
    # for its readiness check, which the milestone mandates — it is not a domain controller).
    domain_routers = [
        p for p in pathlib.Path("api/customer/routers").rglob("*.py")
        if p.name not in ("system.py", "__init__.py")
    ]
    router_src = "\n".join(p.read_text() for p in domain_routers)
    # regression: controllers never touch repositories, transactions, or permission eval.
    # NB `.session` (with the dot) targets a *database* session attribute access precisely —
    # the Conversation Runtime (M8.12) legitimately uses "session" as a domain noun in routes
    # and handler names, so the bare token would false-positive; `.session` still catches any
    # real DB-session access (`database.session`, `db.session`, …).
    for banned in ("Repository", "UnitOfWork", "PermissionEvaluator", ".session", "authorize("):
        assert banned not in router_src, f"controller references {banned!r}"


def test_customer_api_boundary_and_lower_layers_unchanged():
    used = {m.split(".")[0] for m in _imported_modules("api/customer")}
    forbidden = {
        "runtime", "gateway", "replay", "memory", "knowledge", "context", "prompt",
        "reasoning", "provider", "providers", "bridge", "pipeline", "product", "tool",
        "skill", "workflow", "agent", "loop", "mcp", "hooks", "integration",
        "observability", "voicebot",
    }
    assert not (used & forbidden), used & forbidden
    # regression: none of the lower layers import the API (direction preserved)
    for layer in ("api/foundation", "api/application", "api/persistence"):
        offenders = {m for m in _imported_modules(layer) if "customer" in m}
        assert not offenders, f"{layer} imports api.customer: {offenders}"
