"""M12.2 — console sign-in and organizations (ADR-0055 DP4/DP9, LLD §3, security model).

What is proven here:

* credentials are really verified — there is no path from an identifier alone to a session;
* passwords are stored as scrypt hashes, and no response ever carries a hash or a token;
* the console session is an `HttpOnly`, `SameSite=Strict` cookie, revocable, with absolute and
  idle expiry, and `Secure` everywhere except `APP_ENV=dev`;
* first-run sign-up closes after the first account; `closed` and `open` do what they say;
* mutations require the console header (CSRF);
* the active organization lives on the server-side session, and a user can never select, read
  or rename an organization they are not a member of (404 — existence does not leak);
* the role matrix is enforced server-side;
* console identity and the developer API key are separate credentials, neither accepted in the
  other's place;
* the bootstrap CLI gives an M11-created organization its first console owner.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, delete, select

from api.bootstrap import create_org_with_key
from api.console import ConsoleConfigError, ConsoleSettings, bootstrap_owner, build_console_app
from api.console.identity import service
from api.console.identity.deps import CONSOLE_HEADER, SESSION_COOKIE
from api.console.identity.passwords import hash_password, verify_password
from api.console.identity.throttle import LoginThrottle
from api.console.persistence import ConsoleSessionRow, MembershipRow, UserRow
from api.console.persistence import migrations as dp_migrations
from api.db import engine
from api.models import ApiKey, Org
from api.webrtc.config import WebRTCSettings

HEADERS = {CONSOLE_HEADER: "1"}
PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def _no_console_accounts() -> Iterator[None]:
    """Each test starts with no console accounts, so first-run behaviour is deterministic.
    Only the `dp_*` rows this suite owns are cleared; M11 rows are left alone."""

    def wipe() -> None:
        with DBSession(engine) as db:
            for table in (ConsoleSessionRow, MembershipRow, UserRow):
                db.exec(delete(table))
            db.commit()

    SQLModel.metadata.create_all(engine)
    wipe()
    yield
    wipe()


def _app(settings: ConsoleSettings | None = None, **webrtc: object) -> TestClient:
    return TestClient(
        build_console_app(settings or ConsoleSettings(), webrtc_settings=WebRTCSettings(**webrtc))
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    with _app() as c:
        yield c


def _signup(
    client: TestClient, email: str = "ada@example.com", org: str = "Analytical Engines"
):
    return client.post(
        "/v1/console/auth/signup",
        headers=HEADERS,
        json={
            "email": email,
            "password": PASSWORD,
            "display_name": "Ada",
            "organization_name": org,
        },
    )


def _login(client: TestClient, email: str, password: str = PASSWORD):
    return client.post(
        "/v1/console/auth/login", headers=HEADERS, json={"email": email, "password": password}
    )


def _user(email: str) -> UserRow:
    with DBSession(engine) as db:
        user = service.find_user(db, email)
        assert user is not None
        return user


# ── sign-up ───────────────────────────────────────────────────────────────────


def test_first_run_signup_creates_a_user_an_m11_org_and_an_owner_membership(client):
    response = _signup(client)

    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "ada@example.com"
    assert body["organization"]["name"] == "Analytical Engines"
    assert body["organization"]["role"] == "owner"
    assert body["memberships"] == [body["organization"]]
    with DBSession(engine) as db:
        org = db.get(Org, body["organization"]["id"])
        assert org is not None and org.name == "Analytical Engines"  # the M11 tenant row
        # Sign-up creates no developer API key; that is M12.7's, and an explicit act.
        assert db.exec(select(ApiKey).where(ApiKey.org_id == org.id)).first() is None


def test_the_session_cookie_is_httponly_strict_and_not_in_the_body(client):
    response = _signup(client)

    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Secure" not in cookie  # APP_ENV=dev: http://localhost cannot carry Secure
    token = client.cookies.get(SESSION_COOKIE)
    assert token and token not in response.text


@pytest.mark.parametrize("env,explicit,expected", [("prod", None, True), ("dev", True, True)])
def test_the_cookie_is_secure_outside_dev_or_when_configured(env, explicit, expected):
    with _app(ConsoleSettings(cookie_secure=explicit), app_env=env) as c:
        assert ("Secure" in _signup(c).headers["set-cookie"]) is expected


def test_the_password_is_stored_as_an_scrypt_hash(client):
    _signup(client)
    user = _user("ada@example.com")
    assert user.password_hash.startswith("scrypt$")
    assert PASSWORD not in user.password_hash
    assert verify_password(PASSWORD, user.password_hash)


def test_first_run_signup_closes_after_the_first_account(client):
    assert client.get("/v1/console/meta").json()["signup"] == {
        "mode": "first_run",
        "available": True,
    }
    _signup(client)

    second = _signup(client, email="bob@example.com")
    assert second.status_code == 403
    assert second.json()["error"]["type"] == "forbidden"
    assert client.get("/v1/console/meta").json()["signup"]["available"] is False


def test_closed_signup_refuses_even_the_first_account():
    with _app(ConsoleSettings(signup="closed")) as c:
        assert _signup(c).status_code == 403
        assert c.get("/v1/console/meta").json()["signup"] == {
            "mode": "closed",
            "available": False,
        }


def test_open_signup_accepts_further_accounts_but_not_a_duplicate_email():
    with _app(ConsoleSettings(signup="open")) as c:
        assert _signup(c).status_code == 201
        assert _signup(c, email="bob@example.com").status_code == 201
        duplicate = _signup(c, email="  ADA@example.com ")
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["type"] == "conflict"


@pytest.mark.parametrize(
    "overrides,status",
    [
        ({"password": "short"}, 400),
        ({"email": "not-an-email"}, 400),
        ({"organization_name": "   "}, 400),
        ({"organization_name": "x" * 101}, 400),
    ],
)
def test_signup_validates_its_input(client, overrides, status):
    body = {
        "email": "ada@example.com",
        "password": PASSWORD,
        "organization_name": "Org",
        **overrides,
    }
    response = client.post("/v1/console/auth/signup", headers=HEADERS, json=body)
    assert response.status_code == status
    assert response.json()["error"]["type"] == "invalid_request"
    with DBSession(engine) as db:
        assert service.user_count(db) == 0  # nothing half-created


def test_a_malformed_console_request_uses_the_console_envelope(client):
    response = client.post("/v1/console/auth/signup", headers=HEADERS, json={"email": "x"})
    assert response.status_code == 422
    assert response.json()["error"]["type"] == "invalid_request"


# ── sign-in ───────────────────────────────────────────────────────────────────


def test_login_verifies_the_password_and_starts_in_the_first_organization(client):
    org_id = _signup(client).json()["organization"]["id"]
    client.cookies.clear()

    response = _login(client, "ADA@example.com ")
    assert response.status_code == 200
    assert response.json()["organization"]["id"] == org_id
    assert client.get("/v1/console/me").status_code == 200


def test_a_wrong_password_and_an_unknown_email_are_indistinguishable(client):
    _signup(client)
    client.cookies.clear()

    wrong = _login(client, "ada@example.com", "incorrect horse battery")
    unknown = _login(client, "nobody@example.com")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]
    assert SESSION_COOKIE not in wrong.headers.get("set-cookie", "")


def test_there_is_no_login_by_identifier_alone(client):
    _signup(client)
    client.cookies.clear()
    user_id = _user("ada@example.com").id

    for body in ({"user_id": user_id}, {"email": "ada@example.com"}):
        response = client.post("/v1/console/auth/login", headers=HEADERS, json=body)
        assert response.status_code == 422
    assert client.get("/v1/console/me").status_code == 401


def test_a_disabled_user_cannot_sign_in_and_loses_live_sessions(client):
    _signup(client)
    with DBSession(engine) as db:
        user = service.find_user(db, "ada@example.com")
        user.status = "disabled"
        db.add(user)
        db.commit()

    assert client.get("/v1/console/me").status_code == 401
    client.cookies.clear()
    assert _login(client, "ada@example.com").status_code == 401


def test_repeated_failures_are_throttled_even_for_the_right_password():
    with _app(ConsoleSettings(login_max_failures=3)) as c:
        _signup(c)
        c.cookies.clear()
        for _ in range(3):
            assert _login(c, "ada@example.com", "wrong password here").status_code == 401

        blocked = _login(c, "ada@example.com")
        assert blocked.status_code == 429
        assert blocked.json()["error"]["type"] == "rate_limited"


def test_the_throttle_window_expires():
    now = [0.0]
    throttle = LoginThrottle(2, 60.0, clock=lambda: now[0])
    throttle.record_failure("a@x.io", "c")
    throttle.record_failure("a@x.io", "c")
    assert throttle.blocked("a@x.io", "c")
    assert not throttle.blocked("a@x.io", "other-client")
    now[0] = 61.0
    assert not throttle.blocked("a@x.io", "c")


# ── sessions ──────────────────────────────────────────────────────────────────


def test_protected_routes_need_a_session(client):
    for path in ("/v1/console/me", "/v1/console/organization", "/v1/console/organization/members"):
        response = client.get(path)
        assert response.status_code == 401, path
        assert response.json()["error"]["type"] == "unauthenticated"

    client.cookies.set(SESSION_COOKIE, "forged-token")
    assert client.get("/v1/console/me").status_code == 401


def test_logout_revokes_the_session_server_side(client):
    _signup(client)
    token = client.cookies.get(SESSION_COOKIE)

    assert client.post("/v1/console/auth/logout", headers=HEADERS).status_code == 204
    client.cookies.set(SESSION_COOKIE, token)  # a copied cookie is dead too
    assert client.get("/v1/console/me").status_code == 401
    assert client.post("/v1/console/auth/logout", headers=HEADERS).status_code == 204


@pytest.mark.parametrize("field", ["expires_at", "last_seen_at"])
def test_sessions_expire_absolutely_and_when_idle(client, field):
    _signup(client)
    with DBSession(engine) as db:
        row = db.exec(select(ConsoleSessionRow)).one()
        setattr(row, field, service.now() - timedelta(days=1))
        db.add(row)
        db.commit()

    assert client.get("/v1/console/me").status_code == 401


def test_purging_removes_dead_sessions_only(client):
    _signup(client)
    with DBSession(engine) as db:
        user = service.find_user(db, "ada@example.com")
        service.start_session(db, user=user, org_id=None, absolute=timedelta(hours=-1))
        db.commit()
        assert len(db.exec(select(ConsoleSessionRow)).all()) == 2
        assert service.purge_sessions(db) == 1
        db.commit()

    assert client.get("/v1/console/me").status_code == 200  # the live one survived


# ── CSRF ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/v1/console/auth/signup",
        "/v1/console/auth/login",
        "/v1/console/auth/logout",
        "/v1/console/organizations",
        "/v1/console/session/organization",
    ],
)
def test_mutations_without_the_console_header_are_refused(client, path):
    response = client.post(path, json={})
    assert response.status_code == 403
    assert response.json()["error"]["type"] == "forbidden"


def test_renaming_without_the_console_header_is_refused(client):
    _signup(client)
    assert client.patch("/v1/console/organization", json={"name": "X"}).status_code == 403


# ── organizations & tenant isolation ──────────────────────────────────────────


def test_creating_an_organization_makes_it_active_and_lists_both(client):
    first = _signup(client).json()["organization"]["id"]

    created = client.post("/v1/console/organizations", headers=HEADERS, json={"name": "Second"})
    assert created.status_code == 201
    second = created.json()["organization"]
    assert second["name"] == "Second" and second["role"] == "owner"
    assert {m["id"] for m in created.json()["memberships"]} == {first, second["id"]}
    assert client.get("/v1/console/organization").json()["id"] == second["id"]

    switched = client.post(
        "/v1/console/session/organization", headers=HEADERS, json={"organization_id": first}
    )
    assert switched.status_code == 200
    assert client.get("/v1/console/organization").json()["id"] == first


def _two_tenants() -> tuple[TestClient, TestClient, str, str]:
    a, b = _app(ConsoleSettings(signup="open")), _app(ConsoleSettings(signup="open"))
    a.__enter__()
    b.__enter__()
    org_a = _signup(a, "ada@example.com", "Org A").json()["organization"]["id"]
    org_b = _signup(b, "bob@example.com", "Org B").json()["organization"]["id"]
    return a, b, org_a, org_b


def test_a_user_cannot_select_another_organization():
    a, b, org_a, org_b = _two_tenants()
    try:
        response = b.post(
            "/v1/console/session/organization", headers=HEADERS, json={"organization_id": org_a}
        )
        assert response.status_code == 404
        assert response.json()["error"]["type"] == "not_found"
        # Nothing moved: B is still in B's organization.
        assert b.get("/v1/console/organization").json()["id"] == org_b

        missing = b.post(
            "/v1/console/session/organization",
            headers=HEADERS,
            json={"organization_id": "org_doesnotexist"},
        )
        assert missing.status_code == 404
        assert missing.json()["error"]["message"] == response.json()["error"]["message"]
    finally:
        a.__exit__(None, None, None)
        b.__exit__(None, None, None)


def test_members_and_organization_reads_are_scoped_to_the_session():
    a, b, org_a, org_b = _two_tenants()
    try:
        assert [m["email"] for m in a.get("/v1/console/organization/members").json()] == [
            "ada@example.com"
        ]
        assert [m["email"] for m in b.get("/v1/console/organization/members").json()] == [
            "bob@example.com"
        ]
        assert b.get("/v1/console/organization").json()["id"] == org_b
        # A client-supplied org id is not an input to any read.
        assert b.get(f"/v1/console/organization?org_id={org_a}").json()["id"] == org_b
    finally:
        a.__exit__(None, None, None)
        b.__exit__(None, None, None)


def test_a_session_for_a_revoked_membership_has_no_organization(client):
    org_id = _signup(client).json()["organization"]["id"]
    with DBSession(engine) as db:
        membership = db.exec(select(MembershipRow).where(MembershipRow.org_id == org_id)).one()
        membership.status = "removed"
        db.add(membership)
        db.commit()

    response = client.get("/v1/console/organization")
    assert response.status_code == 409
    assert response.json()["error"]["type"] == "conflict"
    assert client.get("/v1/console/me").json()["organization"] is None


@pytest.mark.parametrize(
    "role,can_rename", [("owner", True), ("admin", False), ("developer", False), ("viewer", False)]
)
def test_the_role_matrix_is_enforced_server_side(role, can_rename):
    with _app(ConsoleSettings(signup="open")) as owner, _app(ConsoleSettings(signup="open")) as c:
        org_id = _signup(owner, "owner@example.com", "Shared").json()["organization"]["id"]
        _signup(c, "member@example.com", "Own")
        with DBSession(engine) as db:
            service.add_membership(
                db, org=db.get(Org, org_id), user=service.find_user(db, "member@example.com"),
                role=role,
            )
            db.commit()
        c.post(
            "/v1/console/session/organization", headers=HEADERS, json={"organization_id": org_id}
        )

        assert c.get("/v1/console/organization").json()["role"] == role
        assert c.get("/v1/console/organization/members").status_code == 200
        rename = c.patch("/v1/console/organization", headers=HEADERS, json={"name": "Renamed"})
        assert rename.status_code == (200 if can_rename else 403)
        with DBSession(engine) as db:
            assert (db.get(Org, org_id).name == "Renamed") is can_rename


# ── credential separation ─────────────────────────────────────────────────────


def test_a_console_session_is_not_a_developer_api_credential(client):
    _signup(client)
    response = client.get("/v1/agents")
    assert response.status_code == 401
    assert response.json() == {"detail": "Missing x-api-key"}


def test_a_developer_api_key_is_not_a_console_credential(client):
    _, key = create_org_with_key("Keyed org")
    response = client.get("/v1/console/me", headers={"x-api-key": key})
    assert response.status_code == 401


def test_no_response_carries_a_hash_or_a_token(client):
    bodies = [_signup(client).text]
    token = client.cookies.get(SESSION_COOKIE)
    for path in ("/v1/console/me", "/v1/console/organization", "/v1/console/organization/members"):
        bodies.append(client.get(path).text)
    for body in bodies:
        assert "scrypt$" not in body
        assert token not in body
        assert "password" not in body.lower()


# ── bootstrap CLI ─────────────────────────────────────────────────────────────


def test_bootstrap_gives_an_m11_org_its_first_console_owner(client):
    org, _ = create_org_with_key("Created by api.bootstrap")
    with DBSession(engine) as db:
        user_id, org_id, created = bootstrap_owner.attach_owner(
            db, email="ops@example.com", password=PASSWORD, org_id=org.id
        )
    assert created and org_id == org.id

    login = _login(client, "ops@example.com")
    assert login.status_code == 200
    assert login.json()["organization"] == {
        "id": org.id,
        "name": "Created by api.bootstrap",
        "role": "owner",
    }


def test_bootstrap_attaches_an_existing_user_without_a_password(client):
    _signup(client)
    with DBSession(engine) as db:
        _, org_id, created = bootstrap_owner.attach_owner(
            db, email="ada@example.com", password=None, new_org="Second org"
        )
    assert not created
    names = {m["name"] for m in client.get("/v1/console/me").json()["memberships"]}
    assert names == {"Analytical Engines", "Second org"}


def test_bootstrap_refuses_an_unknown_org_and_a_passwordless_new_user():
    with DBSession(engine) as db, pytest.raises(ValueError, match="no organization"):
        bootstrap_owner.attach_owner(db, email="x@example.com", password=PASSWORD, org_id="org_x")
    with DBSession(engine) as db, pytest.raises(ValueError, match="password is required"):
        bootstrap_owner.attach_owner(db, email="y@example.com", password=None, new_org="Y")


def test_bootstrap_cli_reads_the_password_from_stdin(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    code = bootstrap_owner._main(
        ["--email", "cli@example.com", "--new-org", "CLI org", "--password-stdin"]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "created" in out and PASSWORD not in out
    assert verify_password(PASSWORD, _user("cli@example.com").password_hash)


# ── schema, passwords, configuration ──────────────────────────────────────────


def test_the_dp_chain_continues_after_the_events_chain():
    from events.persistence import migrations as events_migrations

    assert [m.VERSION for m in dp_migrations.MIGRATIONS] == ["0037"]
    assert dp_migrations.CHAIN_STARTS_AFTER == events_migrations.MIGRATIONS[-1].VERSION
    assert int(dp_migrations.MIGRATIONS[0].VERSION) == int(dp_migrations.CHAIN_STARTS_AFTER) + 1


def test_migration_0037_creates_only_dp_tables_and_is_idempotent():
    fresh = create_engine("sqlite://")
    Org.__table__.create(fresh)  # the M11 tenant the foreign keys point at

    assert dp_migrations.upgrade_all(fresh) == ("0037",)
    assert dp_migrations.upgrade_all(fresh) == ("0037",)
    assert set(inspect(fresh).get_table_names()) == {
        "org",
        "dp_user",
        "dp_membership",
        "dp_console_session",
    }
    uniques = inspect(fresh).get_unique_constraints("dp_membership")
    assert {"org_id", "user_id"} == set(uniques[0]["column_names"])


def test_password_hashes_are_salted_and_malformed_hashes_never_verify():
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first != second
    assert verify_password(PASSWORD, first) and not verify_password("other password!", first)
    for broken in ("", "plain", "bcrypt$1$2$3$4$5", "scrypt$x$8$1$AA$AA"):
        assert verify_password(PASSWORD, broken) is False


@pytest.mark.parametrize(
    "settings",
    [
        ConsoleSettings(signup="sometimes"),
        ConsoleSettings(session_absolute_hours=0),
        ConsoleSettings(session_absolute_hours=1, session_idle_minutes=61),
        ConsoleSettings(login_max_failures=0),
    ],
)
def test_invalid_console_settings_fail_at_startup(settings):
    with pytest.raises(ConsoleConfigError):
        build_console_app(settings, webrtc_settings=WebRTCSettings())


def test_console_settings_are_read_from_the_environment():
    settings = ConsoleSettings.from_env(
        {
            "VOXVOICE_CONSOLE_SIGNUP": "closed",
            "VOXVOICE_CONSOLE_COOKIE_SECURE": "true",
            "VOXVOICE_CONSOLE_SESSION_HOURS": "8",
            "VOXVOICE_CONSOLE_SESSION_IDLE_MINUTES": "30",
            "VOXVOICE_CONSOLE_LOGIN_MAX_FAILURES": "5",
            "VOXVOICE_CONSOLE_LOGIN_WINDOW_MINUTES": "10",
        }
    )
    assert (settings.signup, settings.cookie_secure) == ("closed", True)
    assert (settings.session_absolute_hours, settings.session_idle_minutes) == (8, 30)
    assert (settings.login_max_failures, settings.login_failure_window_minutes) == (5, 10)
    with pytest.raises(ConsoleConfigError):
        ConsoleSettings.from_env({"VOXVOICE_CONSOLE_COOKIE_SECURE": "maybe"})
    with pytest.raises(ConsoleConfigError):
        ConsoleSettings.from_env({"VOXVOICE_CONSOLE_SESSION_HOURS": "twelve"})
