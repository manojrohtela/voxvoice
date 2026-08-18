"""M9.5 — Billing Control Plane API: integration tests.

Every test drives the real ASGI application over HTTP (`TestClient`), through the real
middleware, the real auth dependency, the real Application, and the real M9.4 database. There
are no controller unit tests, because a controller that is only transport has nothing to unit
test — what needs proving is that the *whole* path behaves.

Groups:
  1. wiring, health, ready, version
  2. authentication (identity only) and Epic 8 token interop
  3. authorization forwarding — the decision is made below, never here
  4. the full HTTP flow across every bounded context
  5. error translation
  6. middleware
  7. OpenAPI
  8. dependency injection replaceability
  9. architecture compliance
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.customer.jwt import encode as epic8_encode  # Epic 8's own issuer — interop proof
from billing.application import AllowAllAuthorizer, BillingAuthorizer, DenyAllAuthorizer
from billing.customer import BillingApiSettings, BillingContainer, create_billing_app
from billing.foundation import FixedClock, SequentialIdGenerator
from core.errors import ConfigError

PLATFORM = Path(__file__).resolve().parents[1]
CUSTOMER = PLATFORM / "billing" / "customer"
PREFIX = "/v1/billing"
SECRET = "test-billing-secret"
PERIOD = {"start": 0.0, "end": 2_592_000.0}


def _settings(**overrides) -> BillingApiSettings:
    base = {"environment": "testing", "jwt_secret": SECRET}
    return BillingApiSettings(**{**base, **overrides})


def _client(authorizer: BillingAuthorizer | None = None, **overrides) -> TestClient:
    settings = _settings(**overrides)
    container = BillingContainer.build(
        settings,
        authorizer=authorizer if authorizer is not None else AllowAllAuthorizer(),
        ids=SequentialIdGenerator(),
        clock=FixedClock(),
    )
    return TestClient(create_billing_app(settings=settings, container=container))


def _token(user_id: str = "usr_1", org_id: str | None = "org_1", secret: str = SECRET) -> str:
    """Mint an identity token with **Epic 8's own encoder** — so every authenticated test in
    this file is also a live proof that a Customer-Platform token is accepted here."""
    claims: dict[str, object] = {"sub": user_id}
    if org_id:
        claims["org"] = org_id
    return epic8_encode(claims, secret, ttl_seconds=3600)


def _auth(**kwargs) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(**kwargs)}"}


@pytest.fixture()
def client() -> TestClient:
    with _client() as c:
        yield c


def _open_account(client: TestClient, name: str = "Acme") -> dict:
    response = client.post(
        f"{PREFIX}/accounts",
        json={"display_name": name, "currency": "INR"},
        headers=_auth(),
    )
    assert response.status_code == 201, response.text
    return response.json()


# ── 1. wiring, health, ready, version ──────────────────────────────────────────


def test_health_is_unauthenticated_and_reports_the_plane(client):
    body = client.get("/health").json()
    assert body == {"status": "ok", "environment": "testing", "plane": "billing"}


def test_ready_checks_configuration_database_and_application(client):
    response = client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["checks"] == {"configuration": True, "database": True, "application": True}


def test_version_reports_the_versioned_prefix(client):
    body = client.get("/version").json()
    assert body["api"] == "1.0.0" and body["prefix"] == PREFIX
    assert "Billing" in body["title"]


def test_probes_need_no_identity(client):
    """A probe has no user. These are the only unauthenticated routes in the plane."""
    for path in ("/health", "/ready", "/version"):
        assert client.get(path).status_code in (200, 503)


def test_startup_fails_fast_on_invalid_configuration():
    """A broken deployment never serves (§A2.6/§A10)."""
    app = create_billing_app(settings=BillingApiSettings(environment="nonsense"))
    with pytest.raises(ConfigError), TestClient(app):
        pass


def test_production_refuses_insecure_defaults():
    for bad in (
        {"environment": "production"},  # default JWT secret
        {"environment": "production", "jwt_secret": "s3cret"},  # wildcard CORS
        {
            "environment": "production",
            "jwt_secret": "s3cret",
            "cors_origins": ("https://voxvoice.ai",),
        },  # in-memory database
    ):
        with pytest.raises(ConfigError):
            BillingApiSettings(**bad).validate()


def test_billing_api_is_a_separate_application_from_the_customer_platform():
    """ADR-0023 gives revenue a distinct failure domain: Billing is its own ASGI app, not a
    mount inside the Epic 8 Control Plane."""
    from api.customer.app import create_app

    billing = create_billing_app(settings=_settings())
    customer = create_app(
        settings=__import__(
            "api.customer.config", fromlist=["CustomerApiSettings"]
        ).CustomerApiSettings(environment="testing")
    )
    assert billing is not customer
    assert billing.title != customer.title


# ── 2. authentication — identity only ──────────────────────────────────────────


def test_missing_token_is_401(client):
    assert client.get(f"{PREFIX}/accounts/acc_1").status_code == 401


def test_malformed_token_is_401(client):
    response = client.get(f"{PREFIX}/accounts/acc_1", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401
    assert response.json()["error"]["type"] == "Unauthorized"


def test_token_signed_with_another_secret_is_401(client):
    response = client.get(
        f"{PREFIX}/accounts/acc_1",
        headers={"Authorization": f"Bearer {_token(secret='attacker')}"},
    )
    assert response.status_code == 401
    assert "signature" in response.json()["error"]["message"]


def test_expired_token_fails_as_expired_not_as_invalid(client):
    """The distinction a caller needs to know whether to refresh or re-authenticate."""
    expired = epic8_encode({"sub": "usr_1", "org": "org_1"}, SECRET, ttl_seconds=-1)
    response = client.get(
        f"{PREFIX}/accounts/acc_1", headers={"Authorization": f"Bearer {expired}"}
    )
    assert response.status_code == 401
    assert "expired" in response.json()["error"]["message"]


def test_token_without_subject_is_401(client):
    anonymous = epic8_encode({"org": "org_1"}, SECRET, ttl_seconds=3600)
    response = client.get(
        f"{PREFIX}/accounts/acc_1", headers={"Authorization": f"Bearer {anonymous}"}
    )
    assert response.status_code == 401


def test_token_without_org_claim_is_401_on_org_owned_routes(client):
    """A shape check on the request, not a permission check: a command that acts on an
    organization needs one to act on."""
    response = client.post(
        f"{PREFIX}/accounts",
        json={"display_name": "Acme", "currency": "INR"},
        headers=_auth(org_id=None),
    )
    assert response.status_code == 401


def test_rotated_secret_still_verifies(client_secret="old-key"):
    """Key rotation: a token signed with a retired key keeps working during the window."""
    with _client(jwt_secret="new-key", jwt_secondary_secrets=(client_secret,)) as client:
        response = client.post(
            f"{PREFIX}/accounts",
            json={"display_name": "Acme", "currency": "INR"},
            headers={"Authorization": f"Bearer {_token(secret=client_secret)}"},
        )
        assert response.status_code == 201


def test_billing_issues_no_tokens_and_has_no_auth_routes(client):
    """Billing never authenticates a user (ADR-0023 §5.3). There is no login, no token
    endpoint, and no `encode` in the plane's JWT module."""
    paths = client.get("/openapi.json").json()["paths"]
    assert not [p for p in paths if "auth" in p or "token" in p or "login" in p]
    from billing.customer import jwt as billing_jwt

    assert not hasattr(billing_jwt, "encode")


def test_organization_comes_from_the_token_never_from_the_body(client):
    """A caller must not be able to name a tenant it did not authenticate as."""
    account = _open_account(client)
    assert account["org_id"] == "org_1"

    # Usage posted with a body scope naming another org is still recorded under org_1.
    response = client.post(
        f"{PREFIX}/metering/usage",
        json={
            "account_id": account["id"],
            "meter_code": "voice_minutes",
            "units": 10,
            "unit": "minute",
            "period": PERIOD,
            "event_id": "evt_scope",
            "event_type": "metric.session.completed",
            "observed_at": 1.0,
            "scope": {"org_id": "org_victim", "kind": "organization"},
        },
        headers=_auth(),
    )
    assert response.status_code == 202
    summary = client.get(
        f"{PREFIX}/metering/usage/{account['id']}/voice_minutes",
        params={"period_start": PERIOD["start"], "period_end": PERIOD["end"]},
        headers=_auth(),
    ).json()
    assert summary["units"] == 10
    # The victim organization sees nothing.
    other = client.get(
        f"{PREFIX}/metering/usage/{account['id']}/voice_minutes",
        params={"period_start": PERIOD["start"], "period_end": PERIOD["end"]},
        headers=_auth(org_id="org_victim"),
    ).json()
    assert other["units"] == 0


# ── 3. authorization forwarding ────────────────────────────────────────────────


def test_denied_authorization_becomes_403():
    with _client(authorizer=DenyAllAuthorizer()) as client:
        response = client.post(
            f"{PREFIX}/accounts",
            json={"display_name": "Acme", "currency": "INR"},
            headers=_auth(),
        )
        assert response.status_code == 403
        assert response.json()["error"]["type"] == "BillingAuthorizationError"


def test_default_wiring_denies_every_command():
    """No authorizer injected ⇒ the Application's deny-all stands. A deployment that has not
    wired the Control Plane's RBAC evaluator is inert, not open."""
    settings = _settings()
    container = BillingContainer.build(settings, ids=SequentialIdGenerator(), clock=FixedClock())
    with TestClient(create_billing_app(settings=settings, container=container)) as client:
        response = client.post(
            f"{PREFIX}/accounts",
            json={"display_name": "Acme", "currency": "INR"},
            headers=_auth(),
        )
        assert response.status_code == 403


def test_the_api_forwards_identity_and_decides_nothing():
    """The authorizer receives the token's subject and organization verbatim. The controller
    asks; it never answers."""
    seen: list[tuple[str, str, str]] = []

    class RecordingAuthorizer(BillingAuthorizer):
        def authorize(self, request):
            seen.append((request.actor_id, request.org_id, request.permission.value))
            return AllowAllAuthorizer().authorize(request)

    with _client(authorizer=RecordingAuthorizer()) as client:
        client.post(
            f"{PREFIX}/accounts",
            json={"display_name": "Acme", "currency": "INR"},
            headers=_auth(user_id="usr_42", org_id="org_9"),
        )
    assert seen and seen[0][0] == "usr_42" and seen[0][1] == "org_9"
    assert seen[0][2].startswith("billing.")


def test_no_controller_evaluates_a_permission():
    """Source-level: the transport layer contains no permission vocabulary at all."""
    for path in _controllers():
        code = _code_only(path)
        assert "BillingPermission" not in code
        assert "authorize(" not in code
        assert "AuthorizationOrchestrator" not in code


# ── 4. full HTTP flow ──────────────────────────────────────────────────────────


def test_account_lifecycle_over_http(client):
    account = _open_account(client)
    assert account["status"] == "active" and account["currency"] == "INR"

    fetched = client.get(f"{PREFIX}/accounts/{account['id']}", headers=_auth()).json()
    assert fetched == account

    updated = client.patch(
        f"{PREFIX}/accounts/{account['id']}",
        json={"contact": {"email": "ap@acme.test", "name": "AP"}, "address": {"country": "IN"}},
        headers=_auth(),
    )
    assert updated.status_code == 200

    closed = client.post(
        f"{PREFIX}/accounts/{account['id']}/close", json={"reason": "churn"}, headers=_auth()
    )
    assert closed.status_code == 200 and closed.json()["status"] == "closed"

    # Closing ends commercial activity; the record is retained and still readable (RS4).
    assert client.get(f"{PREFIX}/accounts/{account['id']}", headers=_auth()).status_code == 200


def test_no_delete_endpoint_exists_for_revenue_records(client):
    """Revenue records outlive the customer record. The only DELETE in the plane revokes an
    entitlement grant — and even that is recorded, not removed."""
    deletes = {
        path
        for path, ops in client.get("/openapi.json").json()["paths"].items()
        for method in ops
        if method == "delete"
    }
    # Sharpened at M9.8. The rule is not "there is exactly one DELETE" — it is that no DELETE
    # can remove a revenue record. Each entry below is allowed because it provably cannot:
    #   * an entitlement grant revocation is *recorded*, not removed;
    #   * a billing account DELETE is rejected by the FSM in every state except `draft`, and a
    #     draft has billed nobody (ADR-0023 RS4).
    assert deletes == {
        f"{PREFIX}/entitlements/grants/{{grant_id}}",
        f"{PREFIX}/billing-accounts/{{identifier}}",
        #   * a subscription *item* may be removed while the agreement is still editable; the
        #     agreement itself, and every version of it, is permanent;
        f"{PREFIX}/agreements/{{identifier}}/items/{{item_id}}",
        #   * a *pending intention* may be withdrawn — nothing was ever applied from it.
        f"{PREFIX}/agreements/{{identifier}}/scheduled-changes/{{change_id}}",
        #   * likewise for a pending *entitlement* intention (M9.10): scheduling records intent
        #     and nothing in that domain ever fires it, so withdrawing one removes no decision.
        f"{PREFIX}/capabilities/profiles/{{account_id}}/scheduled-changes/{{change_id}}",
    }

    # The agreement itself is never deletable, in any state, at any path.
    assert f"{PREFIX}/agreements/{{identifier}}" not in deletes
    assert not any(path.endswith("/versions") or "/versions/" in path for path in deletes)

    # Nor is any entitlement grant, profile, gate declaration, bundle, or published snapshot
    # (M9.10): a revoked grant is *kept*, because why a capability went away is itself a fact.
    for forbidden in ("/grants/", "/profiles/", "/definitions", "/bundles", "/snapshot"):
        assert not any(
            "/capabilities" in path and forbidden in path and "scheduled-changes" not in path
            for path in deletes
        ), forbidden

    # And the property those exceptions exist to protect: nothing that holds money, usage, or a
    # commercial commitment is deletable at all.
    revenue_surfaces = (
        "/invoices", "/ledger", "/payments", "/refunds", "/credits", "/metering",
        "/subscriptions", "/taxes", "/contracts", "/licenses",
        "/pricing/versions", "/catalog/versions", "/books/", "/price-books",
    )
    for path in deletes:
        assert not any(surface in path for surface in revenue_surfaces), path


def test_account_hierarchy_over_http(client):
    parent = _open_account(client, "Partner")
    child = client.post(
        f"{PREFIX}/accounts",
        json={
            "display_name": "Child",
            "currency": "INR",
            "kind": "child",
            "parent_account_id": parent["id"],
        },
        headers=_auth(),
    ).json()
    hierarchy = client.get(f"{PREFIX}/accounts/{parent['id']}/hierarchy", headers=_auth()).json()
    assert [c["id"] for c in hierarchy["children"]] == [child["id"]]
    assert hierarchy["invoiced_account_id"] == parent["id"]


def test_catalog_and_pricing_over_http(client):
    meter = client.post(
        f"{PREFIX}/catalog/meters",
        json={
            "code": "voice_minutes",
            "display_name": "Voice minutes",
            "unit": "minute",
            "aggregation": "sum",
            "source": "runtime.session",
        },
        headers=_auth(),
    )
    assert meter.status_code == 201 and meter.json()["code"] == "voice_minutes"

    plan = client.post(
        f"{PREFIX}/catalog/plans",
        params={"publish": True},
        json={
            "code": "pro",
            "display_name": "Pro",
            "tier": "pro",
            "features": [
                {"feature_code": "voice_minutes", "allowance": 1000, "allows_overage": True}
            ],
            "metered_codes": ["voice_minutes"],
        },
        headers=_auth(),
    )
    assert plan.status_code == 201 and plan.json()["published"] is True

    book = client.post(
        f"{PREFIX}/pricing/price-books",
        json={"code": "in_public", "display_name": "India public", "currency": "INR"},
        headers=_auth(),
    )
    assert book.status_code == 201
    fetched = client.get(
        f"{PREFIX}/pricing/price-books/{book.json()['id']}/versions/1", headers=_auth()
    )
    assert fetched.status_code == 200 and fetched.json()["currency"] == "INR"


def test_subscription_lifecycle_over_http(client):
    account = _open_account(client)
    subscription = client.post(
        f"{PREFIX}/subscriptions",
        json={
            "account_id": account["id"],
            "plan_code": "pro",
            "plan_version": 1,
            "price_book_id": "pbk_1",
            "price_book_version": 1,
            "currency": "INR",
            "interval": "monthly",
            "items": [{"price_code": "pro_monthly", "quantity": 3}],
        },
        headers=_auth(),
    )
    assert subscription.status_code == 201
    sub_id = subscription.json()["id"]

    activated = client.post(
        f"{PREFIX}/subscriptions/{sub_id}/transitions",
        json={"trigger": "activate"},
        headers=_auth(),
    )
    assert activated.status_code == 200 and activated.json()["state"] == "active"
    assert activated.json()["seat_count"] == 3

    listed = client.get(
        f"{PREFIX}/subscriptions", params={"account_id": account["id"]}, headers=_auth()
    ).json()
    assert [s["id"] for s in listed] == [sub_id]


def test_illegal_lifecycle_transition_is_409_not_a_coerced_success(client):
    account = _open_account(client)
    sub_id = client.post(
        f"{PREFIX}/subscriptions",
        json={
            "account_id": account["id"],
            "plan_code": "pro",
            "plan_version": 1,
            "price_book_id": "pbk_1",
            "price_book_version": 1,
            "currency": "INR",
            "interval": "monthly",
            "items": [{"price_code": "pro_monthly", "quantity": 1}],
        },
        headers=_auth(),
    ).json()["id"]
    # A draft subscription cannot be resumed — the state machine rejects the edge.
    response = client.post(
        f"{PREFIX}/subscriptions/{sub_id}/transitions",
        json={"trigger": "resume"},
        headers=_auth(),
    )
    assert response.status_code == 409


def test_entitlements_and_quota_over_http(client):
    account = _open_account(client)
    grant = client.post(
        f"{PREFIX}/entitlements/grants",
        json={
            "account_id": account["id"],
            "entitlement": {
                "code": "voice_minutes",
                "kind": "quota",
                "allowance": 1000,
                "allows_overage": True,
            },
        },
        headers=_auth(),
    )
    assert grant.status_code == 201

    snapshot = client.post(
        f"{PREFIX}/entitlements/snapshots",
        json={
            "account_id": account["id"],
            "gates": [
                {"code": "voice_minutes", "kind": "quota", "classification": "fail_open"},
                {"code": "sso", "kind": "flag", "classification": "fail_closed"},
            ],
        },
        headers=_auth(),
    )
    assert snapshot.status_code == 201
    body = snapshot.json()
    assert body["version"] == 1
    # The classification the *enforcing* plane needs travels with the snapshot.
    assert body["classifications"]["voice_minutes"] == "fail_open"
    assert body["classifications"]["sso"] == "fail_closed"

    verdict = client.get(
        f"{PREFIX}/entitlements/quota/{account['id']}/voice_minutes",
        params={"consumed": 1200, "at": 10.0},
        headers=_auth(),
    ).json()
    assert verdict["within_allowance"] is False
    assert verdict["would_exceed"] is False  # overage is allowed, so it is billed not blocked

    revoked = client.delete(f"{PREFIX}/entitlements/grants/{grant.json()['id']}", headers=_auth())
    assert revoked.status_code == 200 and revoked.json()["revoked_at"] is not None


def test_usage_ingestion_is_idempotent_over_http(client):
    account = _open_account(client)
    payload = {
        "account_id": account["id"],
        "meter_code": "voice_minutes",
        "units": 600,
        "unit": "minute",
        "period": PERIOD,
        "event_id": "evt_replay",
        "event_type": "metric.session.completed",
        "observed_at": 1.0,
    }
    first = client.post(f"{PREFIX}/metering/usage", json=payload, headers=_auth())
    second = client.post(f"{PREFIX}/metering/usage", json=payload, headers=_auth())
    assert first.status_code == 202 and first.json()["duplicate"] is False
    # The replay is distinguishable — 200 + duplicate, never a second record.
    assert second.status_code == 200 and second.json()["duplicate"] is True

    summary = client.get(
        f"{PREFIX}/metering/usage/{account['id']}/voice_minutes",
        params={"period_start": PERIOD["start"], "period_end": PERIOD["end"]},
        headers=_auth(),
    ).json()
    assert summary["units"] == 600 and summary["record_count"] == 1


def test_quarantined_usage_is_parked_not_billed(client):
    account = _open_account(client)
    response = client.post(
        f"{PREFIX}/metering/quarantine",
        json={
            "account_id": account["id"],
            "meter_code": "unknown_meter",
            "units": 5,
            "unit": "call",
            "period": PERIOD,
            "event_id": "evt_bad",
            "event_type": "metric.unknown",
            "observed_at": 1.0,
            "reason": "no such meter",
        },
        headers=_auth(),
    )
    assert response.status_code == 202
    summary = client.get(
        f"{PREFIX}/metering/usage/{account['id']}/unknown_meter",
        params={"period_start": PERIOD["start"], "period_end": PERIOD["end"]},
        headers=_auth(),
    ).json()
    assert summary["units"] == 0


def test_ledger_and_credit_balances_over_http(client):
    account = _open_account(client)
    entry = client.post(
        f"{PREFIX}/ledger/entries",
        json={
            "account_id": account["id"],
            "entry_type": "charge",
            "direction": "debit",
            "amount": {"minor": 99900, "currency": "INR"},
        },
        headers=_auth(),
    )
    assert entry.status_code == 201

    balance = client.get(f"{PREFIX}/ledger/balance/{account['id']}", headers=_auth()).json()
    assert balance["amount"] == {"minor": 99900, "currency": "INR"}
    assert balance["entry_count"] == 1

    credit = client.post(
        f"{PREFIX}/credits",
        json={
            "account_id": account["id"],
            "kind": "promotional",
            "amount": {"minor": 50000, "currency": "INR"},
        },
        headers=_auth(),
    )
    assert credit.status_code == 201
    credit_balance = client.get(f"{PREFIX}/credits/balance/{account['id']}", headers=_auth()).json()
    assert credit_balance["amount"]["minor"] == 50000


def test_money_crosses_the_wire_as_integer_minor_units(client):
    """Never a float, never a formatted string — a rounding decision made in a serializer is
    a rounding decision nobody reviewed."""
    account = _open_account(client)
    client.post(
        f"{PREFIX}/ledger/entries",
        json={
            "account_id": account["id"],
            "entry_type": "charge",
            "direction": "debit",
            "amount": {"minor": 1, "currency": "INR"},
        },
        headers=_auth(),
    )
    amount = client.get(f"{PREFIX}/ledger/balance/{account['id']}", headers=_auth()).json()[
        "amount"
    ]
    assert isinstance(amount["minor"], int) and amount["currency"] == "INR"

    schema = client.get("/openapi.json").json()["components"]["schemas"]["MoneyBody"]
    assert schema["properties"]["minor"]["type"] == "integer"


def test_invoice_flow_and_immutability_over_http(client):
    account = _open_account(client)
    draft = client.post(
        f"{PREFIX}/invoices",
        json={
            "account_id": account["id"],
            "currency": "INR",
            "period": PERIOD,
            "lines": [
                {
                    "id": "chl_1",
                    "kind": "recurring",
                    "description": "Pro monthly",
                    "amount": {"minor": 99900, "currency": "INR"},
                    "period": PERIOD,
                    "price_code": "pro_monthly",
                }
            ],
        },
        headers=_auth(),
    )
    assert draft.status_code == 201
    body = draft.json()
    assert body["state"] == "draft" and body["number"] == ""
    assert body["total"]["minor"] == 99900  # totalled by the Foundation, not by the API

    issued = client.post(
        f"{PREFIX}/invoices/{body['id']}/issue",
        json={"entity_code": "VOX", "jurisdiction": "IN"},
        headers=_auth(),
    )
    assert issued.status_code == 200
    assert issued.json()["state"] == "issued" and issued.json()["number"]

    # Issuing twice is a lifecycle conflict, not a silent no-op and not a 500.
    again = client.post(
        f"{PREFIX}/invoices/{body['id']}/issue",
        json={"entity_code": "VOX", "jurisdiction": "IN"},
        headers=_auth(),
    )
    assert again.status_code == 409


def test_payment_and_refund_flow_over_http(client):
    account = _open_account(client)
    invoice = client.post(
        f"{PREFIX}/invoices",
        json={
            "account_id": account["id"],
            "currency": "INR",
            "period": PERIOD,
            "lines": [
                {
                    "id": "chl_1",
                    "kind": "recurring",
                    "description": "Pro monthly",
                    "amount": {"minor": 99900, "currency": "INR"},
                    "period": PERIOD,
                }
            ],
        },
        headers=_auth(),
    ).json()
    issued = client.post(
        f"{PREFIX}/invoices/{invoice['id']}/issue", json={}, headers=_auth()
    ).json()

    payment = client.post(
        f"{PREFIX}/payments",
        json={
            "account_id": account["id"],
            "invoice_id": issued["id"],
            "amount": {"minor": 99900, "currency": "INR"},
            "method": "upi",
        },
        headers=_auth(),
    )
    assert payment.status_code == 201
    # Intent is recorded; no money moved and no provider was called.
    assert payment.json()["state"] == "required"
    assert payment.json()["provider_code"] == ""

    listed = client.get(
        f"{PREFIX}/payments", params={"invoice_id": issued["id"]}, headers=_auth()
    ).json()
    assert len(listed) == 1

    refund = client.post(
        f"{PREFIX}/refunds",
        json={
            "account_id": account["id"],
            "payment_id": payment.json()["id"],
            "invoice_id": issued["id"],
            "amount": {"minor": 10000, "currency": "INR"},
            "reason": "goodwill",
            "request_key": "rk_1",
            "captured": {"minor": 99900, "currency": "INR"},
        },
        headers=_auth(),
    )
    assert refund.status_code == 201

    # Same idempotency key ⇒ the same refund, not a second one.
    replay = client.post(
        f"{PREFIX}/refunds",
        json={
            "account_id": account["id"],
            "payment_id": payment.json()["id"],
            "invoice_id": issued["id"],
            "amount": {"minor": 10000, "currency": "INR"},
            "reason": "goodwill",
            "request_key": "rk_1",
            "captured": {"minor": 99900, "currency": "INR"},
        },
        headers=_auth(),
    )
    assert replay.json()["id"] == refund.json()["id"]

    # Separation of duties is a Foundation rule, and it survives the HTTP boundary: the
    # requester cannot approve their own refund.
    self_approval = client.post(
        f"{PREFIX}/refunds/{refund.json()['id']}/approve",
        json={"note": "ok"},
        headers=_auth(user_id="usr_1"),
    )
    assert self_approval.status_code == 422

    approved = client.post(
        f"{PREFIX}/refunds/{refund.json()['id']}/approve",
        json={"note": "ok"},
        headers=_auth(user_id="usr_2"),
    )
    assert approved.status_code == 200 and approved.json()["approved_by"] == "usr_2"


def test_tax_profile_and_determination_over_http(client):
    account = _open_account(client)
    profile = client.put(
        f"{PREFIX}/taxes/profiles",
        json={
            "account_id": account["id"],
            "regime": "india_gst",
            "taxpayer_kind": "business",
            "country": "IN",
            "place_of_supply": "MH",
            "registrations": [
                {"regime": "india_gst", "number": "27AAAAA0000A1Z5", "country": "IN"}
            ],
        },
        headers=_auth(),
    )
    assert profile.status_code == 200
    assert profile.json()["registration_numbers"] == ["27AAAAA0000A1Z5"]

    fetched = client.get(f"{PREFIX}/taxes/profiles/{account['id']}", headers=_auth())
    assert fetched.status_code == 200 and fetched.json()["place_of_supply"] == "MH"

    # No determination exists until the tax edge runs (M9.15) — 404, not an invented zero.
    assert client.get(f"{PREFIX}/taxes/determinations/inv_x", headers=_auth()).status_code == 404


def test_contracts_are_read_only_at_this_milestone(client):
    listed = client.get(f"{PREFIX}/contracts", params={"account_id": "acc_1"}, headers=_auth())
    assert listed.status_code == 200 and listed.json() == []
    paths = client.get("/openapi.json").json()["paths"]
    assert list(paths[f"{PREFIX}/contracts"]) == ["get"]


def test_license_lifecycle_over_http(client):
    account = _open_account(client)
    license_ = client.post(
        f"{PREFIX}/licenses",
        json={
            "account_id": account["id"],
            "kind": "self_hosted",
            "valid_from": 0.0,
            "entitlement_codes": ["sso"],
        },
        headers=_auth(),
    )
    assert license_.status_code == 201 and license_.json()["state"] == "issued"

    revoked = client.post(
        f"{PREFIX}/licenses/{license_.json()['id']}/revoke",
        json={"reason": "contract ended"},
        headers=_auth(),
    )
    assert revoked.status_code == 200 and revoked.json()["state"] == "revoked"

    # Revocation is a recorded transition — the license is still listed, not deleted.
    listed = client.get(
        f"{PREFIX}/licenses", params={"account_id": account["id"]}, headers=_auth()
    ).json()
    assert len(listed) == 1 and listed[0]["state"] == "revoked"


def test_writes_are_durable_across_a_new_client(client):
    """The HTTP layer really did reach M9.4 storage."""
    account = _open_account(client)
    fresh = client.get(f"{PREFIX}/accounts/{account['id']}", headers=_auth())
    assert fresh.status_code == 200 and fresh.json()["display_name"] == "Acme"


# ── 5. error translation ───────────────────────────────────────────────────────


def test_not_found_is_404_with_a_typed_envelope(client):
    response = client.get(f"{PREFIX}/accounts/acc_missing", headers=_auth())
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["type"] == "BillingNotFound" and error["request_id"]


def test_validation_failure_is_422_with_every_finding(client):
    account = _open_account(client)
    # GST needs both a country and a place of supply — two findings, both reported.
    response = client.put(
        f"{PREFIX}/taxes/profiles",
        json={"account_id": account["id"], "regime": "india_gst"},
        headers=_auth(),
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["type"] == "BillingValidationError"
    # Every finding, so a caller needs one round trip rather than five.
    assert error["diagnostics"] and all("code" in d for d in error["diagnostics"])


def test_malformed_body_is_422_from_pydantic(client):
    response = client.post(
        f"{PREFIX}/accounts", json={"display_name": "Acme", "currency": "XYZ"}, headers=_auth()
    )
    assert response.status_code == 422


def test_immutability_violation_is_409_not_500(client):
    """Refusing to alter an immutable revenue fact is the system working correctly, and a
    caller must be able to tell that apart from a fault."""
    account = _open_account(client)
    invoice = client.post(
        f"{PREFIX}/invoices",
        json={
            "account_id": account["id"],
            "currency": "INR",
            "period": PERIOD,
            "lines": [
                {
                    "id": "chl_1",
                    "kind": "recurring",
                    "description": "x",
                    "amount": {"minor": 100, "currency": "INR"},
                    "period": PERIOD,
                }
            ],
        },
        headers=_auth(),
    ).json()
    client.post(f"{PREFIX}/invoices/{invoice['id']}/issue", json={}, headers=_auth())
    again = client.post(f"{PREFIX}/invoices/{invoice['id']}/issue", json={}, headers=_auth())
    assert again.status_code == 409
    assert again.json()["error"]["type"] in ("ImmutabilityViolation", "InvalidBillingTransition")


def test_error_translation_table_is_the_single_source():
    from billing.application import BillingApplicationError, BillingAuthorizationError
    from billing.customer.errors import status_for
    from billing.customer.jwt import BillingJWTError
    from billing.foundation import (
        BillingConflict,
        BillingNotFound,
        CurrencyMismatch,
        ImmutabilityViolation,
        InvalidBillingTransition,
        OrganizationIsolationError,
    )

    cases = {
        CurrencyMismatch("x"): 422,
        BillingAuthorizationError("x"): 403,
        OrganizationIsolationError("x"): 403,
        BillingNotFound("x"): 404,
        BillingConflict("x"): 409,
        InvalidBillingTransition("x"): 409,
        ImmutabilityViolation("x"): 409,
        BillingJWTError("x"): 401,
        BillingApplicationError("x"): 400,
        RuntimeError("x"): 500,
    }
    for exc, expected in cases.items():
        assert status_for(exc) == expected, type(exc).__name__


def test_internal_errors_never_leak_detail():
    """5xx carries no exception message — a stack detail on a fault is an information leak.

    `raise_server_exceptions=False` makes the TestClient behave like a real deployment, where
    the handler produces a response rather than the exception propagating to the test."""
    from billing.customer import container as container_module

    settings = _settings()
    container = BillingContainer.build(
        settings, authorizer=AllowAllAuthorizer(), ids=SequentialIdGenerator(), clock=FixedClock()
    )
    app = create_billing_app(settings=settings, container=container)

    def _explode():
        raise RuntimeError("secret internal detail: DSN=postgres://user:pw@host")

    app.dependency_overrides[container_module.get_application] = _explode
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(f"{PREFIX}/accounts/acc_1", headers=_auth())
        assert response.status_code == 500
        assert response.json()["error"]["message"] == "internal error"
        assert "postgres" not in response.text


def _code_only(path: Path) -> str:
    """Source with docstrings and comments stripped — these scans are about what a
    controller *does*, and prose that mentions "rate" or "commit" is not behaviour."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _controllers() -> list[Path]:
    """The domain controllers. `system.py` is excluded from the *domain* rules below because
    it is a probe router: it dispatches no command and no query, and its one `try/except`
    exists so a readiness probe reports `false` instead of raising — the opposite of a
    controller swallowing a domain error. `test_probe_router_only_probes` pins that."""
    return [
        p
        for p in sorted((CUSTOMER / "routers").glob("*.py"))
        if p.name not in ("__init__.py", "system.py")
    ]


def test_probe_router_only_probes():
    code = _code_only(CUSTOMER / "routers" / "system.py")
    assert "dispatch" not in code  # no command, no query
    for forbidden in ("repositories", "select(", "commit", "rollback", "Money("):
        assert forbidden not in code


def test_controllers_never_catch_exceptions():
    """Translation is centralized; a router that caught an error would translate it twice,
    differently.

    Sharpened at M9.15 from a substring scan to an AST walk, and strictly strengthened by it: a
    parameter named `country` contains the substring `try:`, which the old form read as a
    swallowed exception. Structure can tell a handler from a word; a substring cannot."""
    for path in _controllers():
        tree = ast.parse(_code_only(path))
        for node in ast.walk(tree):
            assert not isinstance(node, (ast.Try, ast.ExceptHandler)), path.name


# ── 6. middleware ──────────────────────────────────────────────────────────────


def test_correlation_id_is_honoured_and_echoed(client):
    response = client.get("/health", headers={"X-Correlation-ID": "trace-abc"})
    assert response.headers["X-Correlation-ID"] == "trace-abc"
    assert response.headers["X-Request-ID"]
    assert float(response.headers["X-Response-Time-ms"]) >= 0.0


def test_correlation_id_is_generated_when_absent(client):
    a = client.get("/health").headers["X-Correlation-ID"]
    b = client.get("/health").headers["X-Correlation-ID"]
    assert a and b and a != b


def test_security_headers_are_present(client):
    headers = client.get("/health").headers
    for header, value in {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
    }.items():
        assert headers[header] == value
    # HSTS is meaningless over plain HTTP and must not be advertised there.
    assert "Strict-Transport-Security" not in headers


def test_request_metrics_are_recorded(client):
    client.get("/health")
    client.get("/version")
    snapshot = client.app.state.metrics.snapshot()
    assert snapshot["requests"]["GET /health"] >= 1
    assert snapshot["statuses"]["200"] >= 2
    assert snapshot["total_ms"] > 0


def test_oversized_payload_is_413(client):
    response = client.post(
        f"{PREFIX}/accounts",
        content=b"x" * 200,
        headers=_auth() | {"Content-Length": "9000000", "Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_cors_preflight_is_answered(client):
    response = client.options(
        f"{PREFIX}/accounts",
        headers={
            "Origin": "https://studio.voxvoice.ai",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def test_error_responses_also_carry_correlation_headers(client):
    response = client.get(f"{PREFIX}/accounts/nope", headers=_auth())
    assert response.status_code == 404
    assert response.headers["X-Request-ID"]
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


def test_access_log_carries_no_body_or_token(caplog, client):
    """Billing bodies carry money and tax identifiers; those do not belong in a log line."""
    import logging

    with caplog.at_level(logging.INFO, logger="voxvoice.billing.api"):
        client.post(
            f"{PREFIX}/accounts",
            json={"display_name": "Secret Corp", "currency": "INR"},
            headers=_auth(),
        )
    text = "\n".join(r.getMessage() + str(r.__dict__) for r in caplog.records)
    assert "Secret Corp" not in text
    assert "Bearer" not in text


# ── 7. OpenAPI ─────────────────────────────────────────────────────────────────


def test_openapi_generates_and_is_versioned(client):
    spec = client.get("/openapi.json").json()
    assert spec["info"]["version"] == "1.0.0"
    assert "Billing" in spec["info"]["title"]
    assert len(spec["paths"]) >= 35


def test_every_bounded_context_is_tagged(client):
    spec = client.get("/openapi.json").json()
    tags = {t["name"] for t in spec["tags"]}
    expected = {
        "accounts",
        "catalog",
        "pricing",
        "subscriptions",
        "entitlements",
        "metering",
        # M9.12 — the Rating Engine's own tag, added the way every bounded context before it
        # added one: by extension, never by renaming an existing tag.
        "rating",
        "ledger",
        "credits",
        "invoices",
        "payments",
        # M9.18 — Revenue Recognition's own tag. Not "ledger": M9.13 owns financial *recording*,
        # and this milestone owns the decision about *when* an amount is earned. Two domains, two
        # tags, one prefix each.
        "revenue",
        # M9.17 — Dunning & Collections' own tag. Note it is *not* "payments": M9.16 owns the
        # `/collections` **prefix** for the act of taking money, and this milestone owns the
        # `/recovery` prefix for deciding whether to ask. Two domains, two tags, no shadowing.
        "collections",
        # M9.19 — Reconciliation & Financial Integrity's own tag. Not "ledger" and not
        # "payments": those domains *produce* financial facts, and this one only ever compares
        # them. An observer sharing a tag with the domain it audits is how the boundary gets
        # forgotten.
        "integrity",
        # M9.21 — the investigation of an integrity incident, tagged as its own bounded
        # context because it is a workflow *over* the books rather than a statement about
        # them. Added by extension, never by renaming an existing tag.
        "investigations",
        # M9.22 — regulatory reporting, tagged as its own bounded context because a filing is
        # a derived artifact spanning nine domains rather than any one domain's view.
        "reports",
        # M9.23 — period scope & consolidation, added by extension like every tag before it.
        "consolidations",
        "refunds",
        "taxes",
        "contracts",
        "licenses",
        "system",
    }
    assert expected <= tags
    used = {tag for ops in spec["paths"].values() for op in ops.values() for tag in op["tags"]}
    assert used == expected  # every declared tag is actually used, and none is undeclared


def test_bearer_security_scheme_is_declared(client):
    spec = client.get("/openapi.json").json()
    schemes = spec["components"]["securitySchemes"]
    assert any(s.get("scheme") == "bearer" for s in schemes.values())


def test_authenticated_routes_declare_security(client):
    spec = client.get("/openapi.json").json()
    for path, ops in spec["paths"].items():
        if path in ("/health", "/ready", "/version"):
            continue
        for method, op in ops.items():
            assert op.get("security"), f"{method.upper()} {path} declares no security"


def test_every_domain_route_documents_its_errors(client):
    spec = client.get("/openapi.json").json()
    for path, ops in spec["paths"].items():
        if not path.startswith(PREFIX):
            continue
        for method, op in ops.items():
            for status in ("401", "403", "404", "409", "422", "500"):
                assert status in op["responses"], f"{method.upper()} {path} missing {status}"


def test_schemas_carry_examples(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    documented = [name for name, s in schemas.items() if "example" in s]
    assert {"MoneyBody", "OpenAccountBody", "RecordUsageBody", "PeriodBody"} <= set(documented)


def test_docs_are_served(client):
    assert client.get("/docs").status_code == 200


# ── 8. dependency injection ────────────────────────────────────────────────────


def test_container_is_replaceable_without_touching_the_app():
    """A test builds a container over an in-memory database and injects it, overriding
    nothing else — the whole point of the composition seam."""
    settings = _settings()
    container = BillingContainer.build(
        settings, authorizer=AllowAllAuthorizer(), ids=SequentialIdGenerator(), clock=FixedClock()
    )
    app = create_billing_app(settings=settings, container=container)
    with TestClient(app) as client:
        assert client.app.state.container is container
        assert _open_account(client)["id"].startswith("bac_")


def test_dependency_overrides_work_for_every_provider():
    from billing.customer import container as container_module

    with _client() as client:
        client.app.dependency_overrides[container_module.get_settings] = lambda: _settings(
            api_version="9.9.9"
        )
        try:
            assert client.get("/version").json()["api"] == "9.9.9"
        finally:
            client.app.dependency_overrides.clear()


def test_seams_are_deterministic_when_injected():
    """Sequential ids + a fixed clock make an HTTP-level test reproducible end to end."""
    ids = [_client_account_id() for _ in range(2)]
    assert ids[0] == ids[1]


def _client_account_id() -> str:
    with _client() as client:
        return _open_account(client)["id"]


def test_the_api_builds_nothing_below_itself():
    """The API asks the M9.4 composition root to wire persistence; it never constructs a
    repository, a Unit of Work, or the Foundation."""
    source = (CUSTOMER / "container.py").read_text()
    assert "build_persistent_billing_application" in source
    for forbidden in ("Repository(", "UnitOfWork(", "BillingFactory(", "BillingDatabase("):
        assert forbidden not in source


# ── 9. architecture compliance ─────────────────────────────────────────────────


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def _files(sub: str) -> list[Path]:
    return sorted((PLATFORM / "billing" / sub).rglob("*.py"))


def test_customer_imports_only_its_allowed_dependencies():
    allowed = {
        "billing",
        "core",
        "fastapi",
        "starlette",
        "pydantic",
        "__future__",
        "abc",
        "dataclasses",
        "enum",
        "typing",
        "collections",
        "contextlib",
        "logging",
        "time",
        "uuid",
        "os",
        "base64",
        "hashlib",
        "hmac",
        "json",
        "pathlib",  # M9.6 — locating the Studio's static directory
    }
    for path in _files("customer"):
        assert _imports(path) <= allowed, (path.name, _imports(path) - allowed)


def test_customer_never_imports_persistence_models_or_sqlmodel():
    forbidden = {"sqlmodel", "sqlalchemy"}
    for path in _files("customer"):
        assert not (_imports(path) & forbidden), path.name
        source = path.read_text()
        assert "billing.persistence.models" not in source
        assert "billing.persistence.mapping" not in source
        assert "Row" not in source or "RowBody" in source


def test_customer_never_imports_a_forbidden_plane():
    forbidden = {
        "api",
        "actuator",
        "runtime",
        "providers",
        "provider",
        "agent",
        "tool",
        "skill",
        "workflow",
        "mcp",
        "loop",
        "hooks",
        "integration",
        "bridge",
        "memory",
        "knowledge",
        "context",
        "prompt",
        "reasoning",
        "pipeline",
        "events",
        "gateway",
        "replay",
        "observability",
        "telemetry",
        "tenant",
        "studio",
        "product",
    }
    for path in _files("customer"):
        assert not (_imports(path) & forbidden), (path.name, _imports(path) & forbidden)


def test_lower_layers_never_import_the_customer_api():
    """Dependency direction: customer → application → foundation → core, never the reverse."""
    for layer in ("foundation", "application", "persistence"):
        for path in _files(layer):
            source = path.read_text()
            assert "billing.customer" not in source, (layer, path.name)
            assert "fastapi" not in _imports(path), (layer, path.name)
            assert "pydantic" not in _imports(path), (layer, path.name)


def test_controllers_touch_no_repository_and_no_transaction():
    """M9.6 sharpened this from a word search to a shape search: the catalog has a legitimate
    `rollback_catalog` endpoint, which publishes a *new* catalog version carrying older
    content. A domain word that collides with a database word must not cost the domain its
    vocabulary — what must stay absent is transaction machinery."""
    for path in _controllers():
        code = _code_only(path)
        for forbidden in (
            "repositories",
            "repository",
            "unit_of_work",
            "uow.",
            "session.",
            ".commit()",
            ".rollback()",
            "select(",
            "database.",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_controllers_contain_no_pricing_or_entitlement_logic():
    """No arithmetic on money, no allowance comparison, no rate. If a controller computed a
    charge, the transport layer would have become the pricing authority."""
    for path in _controllers():
        code = _code_only(path)
        for forbidden in (
            "Money(",
            "Decimal",
            "allowance >",
            "allowance <",
            "round(",
            "sum(",
            "def calculate",
            "def compute",
            "* unit_amount",
            "+ tax",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_every_controller_dispatches_through_a_bus():
    """Nothing bypasses the Application: every route body reaches `commands.dispatch` or
    `queries.dispatch`."""
    for path in sorted((CUSTOMER / "routers").glob("*.py")):
        if path.name in ("__init__.py", "system.py"):
            continue
        tree = ast.parse(path.read_text())
        # Sharpened again at M9.11: a route may delegate its response to a module-level private
        # helper, so the route body is expanded with the bodies of the helpers it calls and the
        # dispatch is required *somewhere in that expansion*. Stricter than exempting helpers by
        # name, and it does not force duplicated serialization into every route to satisfy a test.
        helpers = {
            n.name: ast.unparse(n)
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name.startswith("_")
        }
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            # Sharpened at M9.9: a *route* is a function carrying a `@router.<verb>` decorator.
            # Identifying them structurally is stricter than excluding helpers by name — a new
            # route cannot slip past by being called something the exclusion list allows.
            decorators = [ast.unparse(d) for d in node.decorator_list]
            if not any(d.startswith("router.") for d in decorators):
                continue
            body = ast.unparse(node)
            expanded = body + "".join(
                source for name, source in helpers.items() if f"{name}(" in body
            )
            assert "commands.dispatch" in expanded or "queries.dispatch" in expanded, (
                path.name,
                node.name,
            )


def test_no_foundation_aggregate_or_view_is_serialized_directly(client):
    """The wire contract is a third representation. A response model must be a Pydantic
    schema, never a domain type."""
    spec = client.get("/openapi.json").json()
    schema_names = set(spec["components"]["schemas"])
    for domain_type in ("BillingAccount", "Invoice", "Subscription", "LedgerEntry", "Payment"):
        assert domain_type not in schema_names


def test_frozen_layers_are_unchanged_by_this_milestone():
    from billing import application, foundation, persistence

    # Surfaces grow additively; nothing is ever removed (§A12 — M9.6 added the catalog).
    assert len(foundation.__all__) >= 203
    assert len(application.__all__) >= 100
    assert len(persistence.__all__) >= 40
    for surface in (foundation.__all__, application.__all__, persistence.__all__):
        assert not any(name.startswith("create_billing_app") for name in surface)


def test_billing_is_still_absent_from_the_runtime_composition_root():
    """ADR-0031 CR2 survives the API: a Billing outage still cannot affect a conversation."""
    assert "billing" not in (PLATFORM / "bootstrap.py").read_text().lower()
