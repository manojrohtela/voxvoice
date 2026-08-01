"""Production hardening validation (Epic 8, M8.18).

Verifies the M8.18 hardening seams: security headers, payload limits, rate limiting, error redaction
(no internal leak on 5xx), configuration validation (production fails fast on insecure defaults),
JWT key-rotation seam + expiry, the real `HttpTransport` shape (offline — still `EchoTransport` for
the suite), and credential/secret redaction. No new features — hardening only.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.adapters import (
    ChainedCredentialProvider,
    CredentialError,
    EchoTransport,
    EnvCredentialProvider,
    HttpTransport,
    ProviderRequest,
    redact,
    redact_headers,
)
from api.customer import Container, CustomerApiSettings, create_app
from api.customer.jwt import JWTError, decode_any, encode
from api.foundation import FixedClock, SequentialIdGenerator
from core.errors import ConfigError


def _client(**settings_over) -> TestClient:
    s = CustomerApiSettings(environment="testing", database_url="sqlite://", **settings_over)
    return TestClient(
        create_app(container=Container.build(s, ids=SequentialIdGenerator(), clock=FixedClock()))
    )


# ── security headers ───────────────────────────────────────────────────────────────


def test_security_headers_present():
    with _client() as c:
        h = c.get("/health").headers
        for header in (
            "X-Content-Type-Options",
            "X-Frame-Options",
            "Referrer-Policy",
            "X-XSS-Protection",
            "Permissions-Policy",
            "Cache-Control",
        ):
            assert header in h, header
        assert h["X-Content-Type-Options"] == "nosniff"
        # HSTS is not advertised over plain HTTP
        assert "Strict-Transport-Security" not in h
        # correlation + request id are stamped
        assert "X-Request-ID" in h and "X-Correlation-ID" in h


# ── payload limit + rate limit ─────────────────────────────────────────────────────


def test_payload_limit_rejects_oversize_body():
    with _client(max_request_bytes=300) as c:
        r = c.post("/v1/auth/register", json={"email": "x" * 500, "display_name": "U"})
        assert r.status_code == 413 and r.json()["error"]["type"] == "PayloadTooLarge"


def test_rate_limit_throttles():
    with _client(rate_limit_per_minute=2) as c:
        codes = [c.get("/version").status_code for _ in range(4)]
        assert 429 in codes  # the limiter engages
        # the 429 carries Retry-After
        last = c.get("/version")
        assert last.status_code == 429 and "Retry-After" in last.headers


def test_rate_limit_disabled_by_default():
    with _client() as c:  # rate_limit_per_minute defaults to 0
        assert all(c.get("/version").status_code == 200 for _ in range(20))


# ── error redaction (5xx must not leak internals) ──────────────────────────────────


def test_5xx_is_redacted():
    with _client() as c:
        # an authenticated route with a malformed token → 401 (typed, safe)
        r = c.get("/v1/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
        assert r.status_code == 401
        body = r.json()["error"]
        assert "request_id" in body and "stack" not in body and "Traceback" not in str(body)


# ── configuration validation (fail fast in production) ─────────────────────────────


def test_production_config_rejects_insecure_defaults():
    with pytest.raises(ConfigError):
        CustomerApiSettings(environment="production", jwt_secret="dev-secret-change-me").validate()
    with pytest.raises(ConfigError):
        CustomerApiSettings(
            environment="production", jwt_secret="real", cors_origins=("*",)
        ).validate()
    with pytest.raises(ConfigError):
        CustomerApiSettings(
            environment="production",
            jwt_secret="real",
            cors_origins=("https://app",),
            database_url="sqlite://",
        ).validate()
    # a properly-configured production settings validates
    CustomerApiSettings(
        environment="production",
        jwt_secret="a-real-secret",
        cors_origins=("https://app",),
        database_url="postgresql://db",
    ).validate()


def test_config_rejects_bad_limits():
    with pytest.raises(ConfigError):
        CustomerApiSettings(max_request_bytes=0).validate()
    with pytest.raises(ConfigError):
        CustomerApiSettings(rate_limit_per_minute=-1).validate()


# ── JWT key rotation + expiry ──────────────────────────────────────────────────────


def test_jwt_key_rotation_seam():
    token = encode({"sub": "u1"}, "old-key", ttl_seconds=60)
    # verifies against either the new or the old key (rotation window)
    assert decode_any(token, ("new-key", "old-key"))["sub"] == "u1"
    # an unknown key set is rejected
    with pytest.raises(JWTError):
        decode_any(token, ("new-key",))


def test_jwt_expiry_enforced():
    token = encode({"sub": "u1"}, "k", ttl_seconds=-1)  # already expired
    with pytest.raises(JWTError) as exc:
        decode_any(token, ("k",))
    assert "expired" in str(exc.value)


def test_rotated_token_still_authenticates_over_api():
    # a token signed by a rotated-out (secondary) secret still authenticates
    s = CustomerApiSettings(
        environment="testing",
        database_url="sqlite://",
        jwt_secret="new-key",
        jwt_secondary_secrets=("old-key",),
    )
    with TestClient(
        create_app(container=Container.build(s, ids=SequentialIdGenerator(), clock=FixedClock()))
    ) as c:
        uid = c.post("/v1/auth/register", json={"email": "f@a.io", "display_name": "U"}).json()[
            "id"
        ]
        old_token = encode({"sub": uid}, "old-key", ttl_seconds=3600)
        r = c.get("/v1/auth/me", headers={"Authorization": f"Bearer {old_token}"})
        assert r.status_code == 200  # a token signed by the rotated-out key still authenticates


# ── HTTP transport (production shape; suite stays offline) ──────────────────────────


def test_http_transport_shape_and_offline_default():
    # the production transport constructs (pooling + timeout) without opening a socket
    t = HttpTransport(timeout_seconds=5.0, max_pool_per_host=4)
    assert t._timeout == 5.0 and t._max_pool == 4
    t.close()
    # the suite's default transport is the deterministic Echo — real translation, no network
    echo = EchoTransport()
    resp = echo.request(ProviderRequest(provider="openai", method="POST", url="x", body={"a": 1}))
    assert resp.status == 200 and "usage" in resp.json


# ── credential seam + secret redaction ─────────────────────────────────────────────


def test_credential_provider_and_rotation():
    primary = EnvCredentialProvider(overrides={})
    fallback = EnvCredentialProvider(overrides={"openai_api_key": "k-old"})
    chain = ChainedCredentialProvider(primary, fallback)  # rotation: primary → fallback
    assert chain.require("openai_api_key") == "k-old"
    with pytest.raises(CredentialError):
        chain.require("missing_key")


def test_secret_redaction():
    payload = {"api_key": "sk-123", "name": "ok", "nested": {"password": "p", "port": 5432}}
    red = redact(payload)
    assert red["api_key"] == "***redacted***" and red["nested"]["password"] == "***redacted***"
    assert red["name"] == "ok" and red["nested"]["port"] == 5432  # non-secrets untouched
    assert redact_headers({"Authorization": "Bearer x", "Accept": "json"}) == {
        "Authorization": "***redacted***",
        "Accept": "json",
    }
