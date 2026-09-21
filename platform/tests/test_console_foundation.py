"""M12.1 — the Developer Platform foundation (ADR-0055).

What is proven here:

* the console app **is** the M11 WebRTC app plus additions — the developer API, `/health` and
  signalling are served unchanged, including their `{"detail": …}` error bodies (DP3/DP7);
* `/v1/console/meta` describes the running process truthfully and leaks no credential (S2);
* the console error envelope is scoped to console errors and maps every type to its status;
* the optional bundle is served as a single-page app and refuses path traversal;
* a misconfigured bundle location is a startup error, never a silent "no console".
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.bootstrap import create_org_with_key
from api.console import ConsoleConfigError, ConsoleSettings, build_console_app
from api.console.errors import (
    Conflict,
    ConsoleError,
    Forbidden,
    InvalidRequest,
    NotFound,
    RateLimited,
    RuntimeUnavailable,
    Unauthenticated,
    register_console_error_handler,
)
from api.console.routers.meta import CAPABILITIES, CONSOLE_API_REVISION
from api.webrtc.config import WebRTCSettings


def _client(
    settings: ConsoleSettings | None = None, **webrtc: object
) -> Iterator[TestClient]:
    app = build_console_app(settings or ConsoleSettings(), webrtc_settings=WebRTCSettings(**webrtc))
    with TestClient(app) as client:  # runs the wrapped M11 lifespan
        yield client


@pytest.fixture
def console() -> Iterator[TestClient]:
    yield from _client()


# ── meta ──────────────────────────────────────────────────────────────────────


def test_meta_describes_the_running_process(console: TestClient) -> None:
    response = console.get("/v1/console/meta")

    assert response.status_code == 200
    assert response.json() == {
        "api_version": "v1",
        "console_api_revision": CONSOLE_API_REVISION,
        "runtime": {
            "transport": "local",
            "webrtc_signalling": False,
            "ice_policy": console.get("/health").json()["ice_policy"],
        },
        "capabilities": list(CAPABILITIES),
        "console_bundle": False,
        "signup": response.json()["signup"],  # depends on accounts; asserted in the M12.2 suite
    }
    assert response.json()["signup"]["mode"] == "first_run"


def test_meta_capabilities_are_only_what_is_implemented() -> None:
    # The console renders anything absent as "not yet available"; claiming more would fake it.
    assert CAPABILITIES == ("runtime.status", "identity", "agents")


def test_meta_reports_signalling_when_the_wrapped_app_mounts_it() -> None:
    for client in _client(signalling=True):
        assert client.get("/v1/console/meta").json()["runtime"]["webrtc_signalling"] is True
        # …and the M11 signalling surface is really there, not merely described.
        assert client.get("/v1/webrtc/client").status_code == 200


def test_meta_never_contains_a_credential_or_vendor(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinels = {
        "VOXVOICE_SECRET_DEEPGRAM_API_KEY": "sentinel-stt-9f2c",
        "VOXVOICE_SECRET_GROQ_API_KEY": "sentinel-llm-4a7e",
        "VOXVOICE_SECRET_CARTESIA_API_KEY": "sentinel-tts-1b8d",
        "VOXVOICE_TTS_VOICE_ID": "sentinel-voice-6c3a",
    }
    for name, value in sentinels.items():
        monkeypatch.setenv(name, value)

    for client in _client():
        body = client.get("/v1/console/meta").text.lower()

    for value in sentinels.values():
        assert value not in body
    for vendor in ("deepgram", "groq", "cartesia", "silero"):
        assert vendor not in body


# ── the wrapped M11 application is unchanged ──────────────────────────────────


def test_health_is_the_m11_health(console: TestClient) -> None:
    body = console.get("/health").json()
    assert body["ok"] is True
    assert body["transport"] == "local"
    assert body["webrtc_signalling"] is False


def test_developer_api_error_bodies_keep_their_m11_shape(console: TestClient) -> None:
    missing = console.get("/v1/agents")
    assert missing.status_code == 401
    assert missing.json() == {"detail": "Missing x-api-key"}

    invalid = console.get("/v1/agents", headers={"x-api-key": "vf_not-a-key"})
    assert invalid.status_code == 401
    assert invalid.json() == {"detail": "Invalid API key"}

    _, key = create_org_with_key("Console M12.1 shape")
    unprocessable = console.post("/v1/agents", headers={"x-api-key": key}, json={})
    assert unprocessable.status_code == 422
    assert "detail" in unprocessable.json()
    assert "error" not in unprocessable.json()


def test_developer_api_agents_work_through_the_console_app(console: TestClient) -> None:
    org, key = create_org_with_key("Console M12.1 agents")
    headers = {"x-api-key": key}

    created = console.post(
        "/v1/agents", headers=headers, json={"name": "Probe", "system_prompt": "Be brief."}
    )
    assert created.status_code == 201
    assert created.json()["org_id"] == org.id

    listed = console.get("/v1/agents", headers=headers)
    assert [a["id"] for a in listed.json()] == [created.json()["id"]]


def test_signalling_is_not_mounted_unless_configured(console: TestClient) -> None:
    assert console.get("/v1/webrtc/client").status_code == 404


# ── the console error envelope ────────────────────────────────────────────────


def test_unknown_console_route_answers_in_the_console_envelope(console: TestClient) -> None:
    response = console.get("/v1/console/does-not-exist")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["type"] == "not_found"
    assert error["request_id"] == response.headers["x-request-id"]


def test_a_safe_inbound_request_id_is_echoed(console: TestClient) -> None:
    response = console.get("/v1/console/nope", headers={"x-request-id": "trace-42.a_b"})
    assert response.headers["x-request-id"] == "trace-42.a_b"
    assert response.json()["error"]["request_id"] == "trace-42.a_b"


@pytest.mark.parametrize("unsafe", ["x" * 65, "has space", "<script>", "a;b"])
def test_an_unsafe_inbound_request_id_is_replaced(console: TestClient, unsafe: str) -> None:
    response = console.get("/v1/console/nope", headers={"x-request-id": unsafe})
    assert response.headers["x-request-id"] != unsafe
    assert unsafe not in response.text


@pytest.mark.parametrize(
    ("error", "status", "type_"),
    [
        (InvalidRequest, 400, "invalid_request"),
        (Unauthenticated, 401, "unauthenticated"),
        (Forbidden, 403, "forbidden"),
        (NotFound, 404, "not_found"),
        (Conflict, 409, "conflict"),
        (RateLimited, 429, "rate_limited"),
        (RuntimeUnavailable, 503, "runtime_unavailable"),
        (ConsoleError, 500, "internal"),
    ],
)
def test_every_console_error_maps_to_its_status_and_type(
    error: type[ConsoleError], status: int, type_: str
) -> None:
    app = FastAPI()
    register_console_error_handler(app)

    @app.get("/boom")
    def boom() -> None:
        raise error("Shown to the user")

    response = TestClient(app).get("/boom")
    assert response.status_code == status
    assert response.json()["error"]["type"] == type_
    assert response.json()["error"]["message"] == "Shown to the user"


# ── the console bundle ────────────────────────────────────────────────────────


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>console-shell</title>")
    (root / "assets" / "app.js").write_text("console.log('app')")
    (tmp_path / "secret.txt").write_text("outside-the-bundle")
    return root


@pytest.fixture
def served(dist: Path) -> Iterator[TestClient]:
    yield from _client(ConsoleSettings(dist_dir=dist))


def test_bundle_root_redirects_to_the_trailing_slash(served: TestClient) -> None:
    response = served.get("/console", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/console/"


def test_bundle_serves_the_shell_and_deep_links(served: TestClient) -> None:
    for path in ("/console/", "/console/agents", "/console/agents/agt_123/edit"):
        response = served.get(path)
        assert response.status_code == 200, path
        assert "console-shell" in response.text
        assert response.headers["cache-control"] == "no-cache"


def test_bundle_serves_assets(served: TestClient) -> None:
    response = served.get("/console/assets/app.js")
    assert response.status_code == 200
    assert response.text == "console.log('app')"


def test_a_missing_asset_is_a_404_not_the_shell(served: TestClient) -> None:
    response = served.get("/console/assets/missing.js")
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "not_found"


@pytest.mark.parametrize(
    "path",
    [
        "/console/..%2Fsecret.txt",
        "/console/%2e%2e/secret.txt",
        "/console/assets/..%2F..%2Fsecret.txt",
    ],
)
def test_bundle_refuses_path_traversal(served: TestClient, path: str) -> None:
    response = served.get(path)
    assert response.status_code == 404
    assert "outside-the-bundle" not in response.text


def test_meta_reports_the_bundle(served: TestClient) -> None:
    assert served.get("/v1/console/meta").json()["console_bundle"] is True


# ── configuration ─────────────────────────────────────────────────────────────


def test_no_bundle_is_configured_by_default() -> None:
    assert ConsoleSettings.from_env({}).dist_dir is None
    assert ConsoleSettings.from_env({"VOXVOICE_CONSOLE_DIST": "  "}).dist_dir is None


@pytest.mark.parametrize("problem", ["relative", "missing", "no-index"])
def test_an_unusable_bundle_is_refused_at_startup(tmp_path: Path, problem: str) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    location = {
        "relative": Path("console/dist"),
        "missing": tmp_path / "absent",
        "no-index": empty,
    }[problem]

    with pytest.raises(ConsoleConfigError):
        build_console_app(ConsoleSettings(dist_dir=location), webrtc_settings=WebRTCSettings())


def test_an_invalid_runtime_configuration_is_refused_before_the_console_exists() -> None:
    from api.webrtc.config import WebRTCConfigError

    with pytest.raises(WebRTCConfigError):
        build_console_app(ConsoleSettings(), webrtc_settings=WebRTCSettings(transport="webrtc"))
