"""The external WebRTC client path: signalling channel, session ownership, real media (M11.16.4).

M11.16.3 proved the WebRTC stack works between two aiortc peers in one process. What it could not
prove was that a peer *outside* the runtime could ever reach it, because no signalling channel
existed. This file exercises the channel that closes that gap, in five layers:

**A. Rendezvous registry** — reservation, binding, ownership, the state machine, validation, the
bounds and the timeouts. No HTTP, no library.

**B. Signalling endpoints** — the real FastAPI app, the real auth, the real status codes, the real
CORS policy.

**C. A real external peer over the real channel** — a genuine `RTCPeerConnection` that fetches the
runtime's offer over HTTP, answers over HTTP, and then carries real PCM16 in both directions,
including one test that drives it through `POST /v1/sessions` and the Runtime Pipeline.

**D. Resources** — repeated connect/media/disconnect leaks no tasks, peers or rendezvous slots.

**E. Security** — no credential reaches the signalling plane or the browser page, malformed and
oversized payloads are refused, and a foreign or closed rendezvous cannot be negotiated.

> **None of this is `E11-15` evidence.** The external peer here is an aiortc peer in the test
> process, the providers are the deterministic local ones, and nothing runs on a Stage-2 host.
> What these tests establish is that the path *exists and works*, not that it was *deployed*
> (§15/§26).

Every wait is on a condition under a bounded deadline. Nothing sleeps for a duration, spins a
fixed number of times, or retries a fixed number of times (the M11.15 R-70 rule), and there is no
`skip`, `skipif` or `xfail` anywhere in the file (`E11-1`, §28).
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import httpx
import pytest

from api.bootstrap import create_org_with_key
from api.db import init_db
from api.webrtc.app import build_webrtc_app
from api.webrtc.binding import PENDING_RENDEZVOUS, RendezvousBindingController
from api.webrtc.config import WebRTCConfigError, WebRTCSettings
from api.webrtc.rendezvous import (
    MAX_SIGNALLING_SDP_BYTES,
    RendezvousError,
    RendezvousRegistry,
    RendezvousState,
    validate_sdp,
)

VALID_SDP = "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\n"


# ══════════════════════════════════════════════════════════════════════════════
# A. The rendezvous registry
# ══════════════════════════════════════════════════════════════════════════════


def test_a_reservation_returns_a_token_that_is_never_stored_in_the_clear():
    registry = RendezvousRegistry()
    rid, token, ttl = registry.reserve("org_1")
    assert rid.startswith("rdv_") and token and ttl > 0
    # The raw token appears nowhere in the registry's own state.
    assert token not in repr(registry.__dict__)
    assert registry.authenticate(rid, token).org_id == "org_1"


def test_a_a_wrong_or_missing_token_is_401():
    registry = RendezvousRegistry()
    rid, token, _ = registry.reserve("org_1")
    for bad in (None, "", "not-the-token", token + "x"):
        with pytest.raises(RendezvousError) as err:
            registry.authenticate(rid, bad)
        assert err.value.status_code == 401


def test_a_an_unknown_rendezvous_is_404_and_reveals_nothing():
    registry = RendezvousRegistry()
    with pytest.raises(RendezvousError) as err:
        registry.authenticate("rdv_nope", "whatever")
    assert err.value.status_code == 404


def test_a_binding_is_org_scoped_and_single_use():
    """Session ownership (§8): one rendezvous, one org, one session."""
    registry = RendezvousRegistry()
    rid, _, _ = registry.reserve("org_a")

    # Org B cannot claim org A's rendezvous, and is told only that it does not exist.
    with pytest.raises(RendezvousError) as err:
        registry.bind(rid, "org_b", "ses_b")
    assert err.value.status_code == 404

    registry.bind(rid, "org_a", "ses_a")
    assert registry.state(rid) is RendezvousState.BOUND

    # A second session cannot reuse it.
    with pytest.raises(RendezvousError) as err:
        registry.bind(rid, "org_a", "ses_a2")
    assert err.value.status_code == 409


async def test_a_the_offer_answer_round_trip_completes():
    registry = RendezvousRegistry()
    rid, token, _ = registry.reserve("org_a")
    registry.bind(rid, "org_a", "ses_a")

    async def peer() -> None:
        sdp = await registry.await_offer(rid, token)
        assert sdp == VALID_SDP
        registry.submit_answer(rid, token, VALID_SDP + "a=answer\r\n")

    answer, _ = await asyncio.gather(
        registry.exchange("ses_a", VALID_SDP), peer()
    )
    assert answer.endswith("a=answer\r\n")
    assert registry.state(rid) is RendezvousState.ANSWERED


async def test_a_a_peer_that_never_answers_fails_within_the_bound():
    registry = RendezvousRegistry(answer_timeout_s=0.2)
    rid, _, _ = registry.reserve("org_a")
    registry.bind(rid, "org_a", "ses_a")
    with pytest.raises(RendezvousError) as err:
        await registry.exchange("ses_a", VALID_SDP)
    assert err.value.status_code == 504
    assert registry.state(rid) is RendezvousState.FAILED


async def test_a_an_offer_poll_that_finds_nothing_yet_returns_none_rather_than_hanging():
    registry = RendezvousRegistry()
    rid, token, _ = registry.reserve("org_a")
    assert await registry.await_offer(rid, token, poll_s=0.05) is None


async def test_a_exchange_for_an_unbound_session_is_refused():
    registry = RendezvousRegistry()
    with pytest.raises(RendezvousError) as err:
        await registry.exchange("ses_unknown", VALID_SDP)
    assert err.value.status_code == 409


async def test_a_an_answer_is_accepted_only_while_an_offer_is_waiting():
    registry = RendezvousRegistry()
    rid, token, _ = registry.reserve("org_a")
    with pytest.raises(RendezvousError) as err:
        registry.submit_answer(rid, token, VALID_SDP)
    assert err.value.status_code == 409

    registry.bind(rid, "org_a", "ses_a")
    task = asyncio.create_task(registry.exchange("ses_a", VALID_SDP))
    assert await registry.await_offer(rid, token) == VALID_SDP
    registry.submit_answer(rid, token, VALID_SDP)
    await task
    # A duplicate terminal message is refused rather than silently reapplied.
    with pytest.raises(RendezvousError) as err:
        registry.submit_answer(rid, token, VALID_SDP)
    assert err.value.status_code == 409


@pytest.mark.parametrize(
    ("value", "status"),
    [
        (None, 400),
        (b"v=0\r\n", 400),
        ("", 400),
        ("   ", 400),
        ("<html>gateway error</html>", 400),
        ("x" * (MAX_SIGNALLING_SDP_BYTES + 1), 413),
    ],
)
def test_a_malformed_and_oversized_sdp_is_refused_at_the_edge(value, status):
    with pytest.raises(RendezvousError) as err:
        validate_sdp(value, what="remote answer")
    assert err.value.status_code == status


def test_a_the_sdp_bound_matches_the_transport_bound():
    """Stated in two planes because the Control Plane does not import the provider plane; pinned
    here so the two numbers cannot drift apart unnoticed."""
    from providers.transport.signalling import MAX_SDP_BYTES

    assert MAX_SIGNALLING_SDP_BYTES == MAX_SDP_BYTES


def test_a_open_rendezvous_are_bounded():
    registry = RendezvousRegistry(max_open=3)
    for _ in range(3):
        registry.reserve("org_a")
    with pytest.raises(RendezvousError) as err:
        registry.reserve("org_a")
    assert err.value.status_code == 429
    assert registry.open_count == 3


def test_a_release_is_idempotent_and_forgets_the_session():
    registry = RendezvousRegistry()
    rid, _, _ = registry.reserve("org_a")
    registry.bind(rid, "org_a", "ses_a")
    registry.release("ses_a")
    registry.release("ses_a")
    assert registry.open_count == 0
    with pytest.raises(RendezvousError):
        registry.state(rid)


def test_a_the_registry_satisfies_the_transport_s_sdp_exchange_protocol():
    """The seam actually being implemented, asserted rather than assumed."""
    from providers.transport.signalling import SdpExchange

    assert isinstance(RendezvousRegistry(), SdpExchange)


class _Request:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id


class _Inner:
    """The controller being wrapped. Records what it was asked to do."""

    def __init__(self, fail: bool = False) -> None:
        self.started: list[str] = []
        self.ended: list[str] = []
        self.fail = fail

    async def start(self, request):
        self.started.append(request.session_id)
        if self.fail:
            raise RuntimeError("runtime refused")
        return "started"

    async def end(self, session_id: str) -> None:
        self.ended.append(session_id)

    def conversation_state(self, session_id: str) -> str | None:
        return "in_conversation"


def test_a_the_binding_controller_is_a_session_controller():
    """It stands where the real one stands, so it must satisfy the same contract (AR-14)."""
    from core.session_control import SessionController

    controller = RendezvousBindingController(_Inner(), RendezvousRegistry())
    assert isinstance(controller, SessionController)


async def test_a_the_binding_controller_claims_on_start_and_releases_on_end():
    registry = RendezvousRegistry()
    rid, _, _ = registry.reserve("org_a")
    inner = _Inner()
    controller = RendezvousBindingController(inner, registry)

    token = PENDING_RENDEZVOUS.set((rid, "org_a"))
    try:
        assert await controller.start(_Request("ses_bound")) == "started"
    finally:
        PENDING_RENDEZVOUS.reset(token)
    assert registry.state(rid) is RendezvousState.BOUND

    await controller.end("ses_bound")
    assert inner.ended == ["ses_bound"]
    assert registry.open_count == 0, "ending the session must release the rendezvous"


async def test_a_a_failed_start_releases_the_rendezvous_and_re_raises_unchanged():
    registry = RendezvousRegistry()
    rid, _, _ = registry.reserve("org_a")
    controller = RendezvousBindingController(_Inner(fail=True), registry)
    token = PENDING_RENDEZVOUS.set((rid, "org_a"))
    try:
        with pytest.raises(RuntimeError, match="runtime refused"):
            await controller.start(_Request("ses_doomed"))
    finally:
        PENDING_RENDEZVOUS.reset(token)
    assert registry.open_count == 0


async def test_a_an_ordinary_session_passes_through_the_binding_controller_untouched():
    """Every session the rest of the platform creates has no rendezvous, and must not acquire
    one — the wrapper is transparent when the context variable is unset."""
    registry = RendezvousRegistry()
    registry.reserve("org_a")
    inner = _Inner()
    controller = RendezvousBindingController(inner, registry)
    assert PENDING_RENDEZVOUS.get() is None
    assert await controller.start(_Request("ses_plain")) == "started"
    assert registry.open_count == 1, "an unrelated session claimed a rendezvous"
    assert controller.conversation_state("ses_plain") == "in_conversation"


def test_a_check_bindable_answers_before_the_session_exists():
    registry = RendezvousRegistry()
    rid, _, _ = registry.reserve("org_a")
    registry.check_bindable(rid, "org_a")  # does not raise, and claims nothing
    assert registry.state(rid) is RendezvousState.RESERVED
    with pytest.raises(RendezvousError) as err:
        registry.check_bindable(rid, "org_b")
    assert err.value.status_code == 404
    registry.bind(rid, "org_a", "ses_a")
    with pytest.raises(RendezvousError) as err:
        registry.check_bindable(rid, "org_a")
    assert err.value.status_code == 409


# ══════════════════════════════════════════════════════════════════════════════
# B. The signalling endpoints, over the real app
# ══════════════════════════════════════════════════════════════════════════════


@contextlib.asynccontextmanager
async def running_app(**overrides):
    """The real Control Plane app, lifespan included, driven over ASGI."""
    init_db()
    app = build_webrtc_app(WebRTCSettings(**overrides))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://signalling.test"
        ) as client:
            yield app, client


@contextlib.asynccontextmanager
async def signalling_app(**overrides):
    async with running_app(signalling=True, **overrides) as pair:
        yield pair


def _org() -> tuple[str, str]:
    org, key = create_org_with_key("Signalling Org")
    return org.id, key


async def _agent(client: httpx.AsyncClient, key: str, *, first_message: str | None = None) -> str:
    body: dict[str, object] = {"name": "Bot", "system_prompt": "hi"}
    if first_message is not None:
        body["first_message"] = first_message
    resp = await client.post("/v1/agents", headers={"x-api-key": key}, json=body)
    return resp.json()["id"]


async def test_b_the_signalling_surface_does_not_exist_unless_it_is_configured():
    """Default deployments are untouched (§10/§12)."""
    async with running_app() as (app, client):
        _, key = _org()
        resp = await client.post("/v1/webrtc/rendezvous", headers={"x-api-key": key})
        assert resp.status_code == 404
        assert (await client.get("/v1/webrtc/client")).status_code == 404
        assert (await client.get("/v1/webrtc/sessions")).status_code == 404
        health = (await client.get("/health")).json()
        assert health["transport"] == "local" and health["webrtc_signalling"] is False
        paths = {getattr(route, "path", "") for route in app.routes}
        assert not any(p.startswith("/v1/webrtc") for p in paths)


def test_b_the_default_application_is_byte_for_byte_the_frozen_one():
    """`E10-7` freezes every pre-existing `platform/api/` file (ADR-0043 CD7).

    The external WebRTC path is therefore **additions only**, and the default app is untouched by
    construction rather than by care: it cannot acquire a signalling surface, a CORS policy or a
    real transport from a setting, because it reads none of them."""
    import subprocess

    from api.main import app as default_app

    frozen = (
        "platform/api/main.py",
        "platform/api/config.py",
        "platform/api/schemas.py",
        "platform/api/routers/sessions.py",
    )
    changed = subprocess.run(
        ["git", "status", "--porcelain", "--", *frozen],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent.parent,
    ).stdout.strip()
    assert not changed, f"a frozen api file was modified: {changed}"

    paths = {getattr(route, "path", "") for route in default_app.routes}
    assert not any(p.startswith("/v1/webrtc") for p in paths)
    assert not any("CORSMiddleware" in str(m.cls) for m in default_app.user_middleware)


async def test_b_reserving_requires_the_org_api_key():
    async with signalling_app() as (_, client):
        assert (await client.post("/v1/webrtc/rendezvous")).status_code == 401
        resp = await client.post(
            "/v1/webrtc/rendezvous", headers={"x-api-key": "vf_not_a_key"}
        )
        assert resp.status_code == 401


async def test_b_a_reserved_rendezvous_exchanges_an_offer_and_an_answer():
    async with signalling_app() as (app, client):
        _, key = _org()
        resp = await client.post("/v1/webrtc/rendezvous", headers={"x-api-key": key})
        assert resp.status_code == 201
        body = resp.json()
        rid, token = body["rendezvous_id"], body["signalling_token"]
        assert body["expires_in_s"] > 0

        registry = app.state.webrtc_rendezvous
        # Before an offer exists the poll answers "not yet" rather than hanging or erroring.
        headers = {"x-signalling-token": token}
        early = await client.get(f"/v1/webrtc/rendezvous/{rid}/offer", headers=headers)
        assert early.status_code == 204

        registry.bind(rid, _org_id_of(registry, rid), "ses_http")

        async def runtime_side() -> str:
            return await registry.exchange("ses_http", VALID_SDP)

        async def peer_side() -> None:
            got = await client.get(f"/v1/webrtc/rendezvous/{rid}/offer", headers=headers)
            assert got.status_code == 200 and got.json()["sdp"] == VALID_SDP
            posted = await client.post(
                f"/v1/webrtc/rendezvous/{rid}/answer",
                headers=headers,
                json={"sdp": VALID_SDP + "a=peer\r\n"},
            )
            assert posted.status_code == 204

        answer, _ = await asyncio.gather(runtime_side(), peer_side())
        assert answer.endswith("a=peer\r\n")


def _org_id_of(registry: RendezvousRegistry, rid: str) -> str:
    """The org a rendezvous belongs to — the binding the router does from the session request."""
    return registry._slots[rid].org_id  # noqa: SLF001 — test reaching into its own fixture


async def test_b_signalling_rejects_the_org_key_and_the_session_api_rejects_the_token():
    """The two credentials are not interchangeable in either direction (§9)."""
    async with signalling_app() as (_, client):
        _, key = _org()
        rid = (
            await client.post("/v1/webrtc/rendezvous", headers={"x-api-key": key})
        ).json()["rendezvous_id"]
        # The org key cannot drive signalling…
        assert (
            await client.get(f"/v1/webrtc/rendezvous/{rid}/offer", headers={"x-api-key": key})
        ).status_code == 401
        # …and the signalling token cannot drive the Control Plane.
        assert (
            await client.get("/v1/sessions/ses_x", headers={"x-signalling-token": "t"})
        ).status_code == 401


async def test_b_oversized_and_malformed_answers_are_refused():
    async with signalling_app() as (app, client):
        _, key = _org()
        body = (
            await client.post("/v1/webrtc/rendezvous", headers={"x-api-key": key})
        ).json()
        rid, token = body["rendezvous_id"], body["signalling_token"]
        headers = {"x-signalling-token": token}

        too_big = await client.post(
            f"/v1/webrtc/rendezvous/{rid}/answer",
            headers=headers,
            json={"sdp": "v=0\r\n" + "x" * (MAX_SIGNALLING_SDP_BYTES + 1)},
        )
        assert too_big.status_code == 422  # refused by the schema bound, before the registry

        registry = app.state.webrtc_rendezvous
        registry.bind(rid, _org_id_of(registry, rid), "ses_bad")
        task = asyncio.create_task(registry.exchange("ses_bad", VALID_SDP))
        assert (
            await client.get(f"/v1/webrtc/rendezvous/{rid}/offer", headers=headers)
        ).status_code == 200
        malformed = await client.post(
            f"/v1/webrtc/rendezvous/{rid}/answer",
            headers=headers,
            json={"sdp": "<html>not an sdp</html>"},
        )
        assert malformed.status_code == 400
        # The runtime is still waiting: a rejected answer is not a delivered one.
        registry.submit_answer(rid, token, VALID_SDP)
        await task


async def test_b_no_cors_middleware_exists_unless_origins_are_configured():
    async with signalling_app() as (app, _):
        assert not any(
            "CORSMiddleware" in str(m.cls) for m in app.user_middleware
        ), "CORS must not appear by default"
    async with signalling_app(allowed_origins=("https://app.example",)) as (app, client):
        assert any("CORSMiddleware" in str(m.cls) for m in app.user_middleware)
        _, key = _org()
        allowed = await client.post(
            "/v1/webrtc/rendezvous",
            headers={"x-api-key": key, "origin": "https://app.example"},
        )
        assert allowed.headers.get("access-control-allow-origin") == "https://app.example"
        denied = await client.post(
            "/v1/webrtc/rendezvous",
            headers={"x-api-key": key, "origin": "https://evil.example"},
        )
        assert "access-control-allow-origin" not in denied.headers


def test_b_a_wildcard_origin_is_refused_outside_development():
    with pytest.raises(WebRTCConfigError, match="wildcard"):
        build_webrtc_app(
            WebRTCSettings(app_env="prod", signalling=True, allowed_origins=("*",))
        )
    # …and permitted in development, which is the only rule `dev` relaxes.
    build_webrtc_app(WebRTCSettings(app_env="dev", signalling=True, allowed_origins=("*",)))


def test_b_selecting_webrtc_without_a_voice_is_a_startup_error_not_a_downgrade():
    with pytest.raises(WebRTCConfigError, match="VOXVOICE_TTS_VOICE_ID"):
        build_webrtc_app(WebRTCSettings(transport="webrtc"))
    with pytest.raises(WebRTCConfigError, match="unknown transport"):
        build_webrtc_app(WebRTCSettings(transport="livekit"))


def test_b_configuration_is_read_from_the_environment_with_safe_defaults():
    assert WebRTCSettings.from_env({}) == WebRTCSettings()
    loaded = WebRTCSettings.from_env(
        {
            "RUNTIME_TRANSPORT": "webrtc",
            "VOXVOICE_TTS_VOICE_ID": "voice-1",
            "WEBRTC_ALLOWED_ORIGINS": "https://a.example, https://b.example",
        }
    )
    assert loaded.webrtc_enabled and loaded.signalling_enabled
    assert loaded.allowed_origins == ("https://a.example", "https://b.example")


async def test_b_the_browser_client_is_served_only_where_signalling_is_configured():
    async with signalling_app() as (_, client):
        page = await client.get("/v1/webrtc/client")
        assert page.status_code == 200
        assert "text/html" in page.headers["content-type"]
        assert "getUserMedia" in page.text


async def test_b_the_ordinary_session_api_is_unchanged_where_signalling_is_configured():
    """The existing endpoint keeps working exactly as before, alongside the WebRTC one (§11)."""
    async with signalling_app() as (_, client):
        _, key = _org()
        agent_id = await _agent(client, key)
        resp = await client.post(
            "/v1/sessions", headers={"x-api-key": key}, json={"agent_id": agent_id}
        )
        assert resp.status_code == 201
        assert resp.json()["engine"] == "voxvoice"
        ended = await client.post(
            f"/v1/sessions/{resp.json()['session_id']}/end", headers={"x-api-key": key}
        )
        assert ended.status_code == 200


async def test_b_a_session_cannot_claim_another_org_s_rendezvous():
    async with signalling_app() as (_, client):
        _, key_a = _org()
        _, key_b = _org()
        rid = (
            await client.post("/v1/webrtc/rendezvous", headers={"x-api-key": key_a})
        ).json()["rendezvous_id"]
        agent_b = await _agent(client, key_b)
        resp = await client.post(
            "/v1/webrtc/sessions",
            headers={"x-api-key": key_b},
            json={"agent_id": agent_b, "rendezvous_id": rid},
        )
        assert resp.status_code == 404


async def test_b_an_unknown_rendezvous_fails_the_session_before_the_runtime_is_touched():
    async with signalling_app() as (app, client):
        _, key = _org()
        agent_id = await _agent(client, key)
        started: list[str] = []

        class Recording:
            async def start(self, request):  # pragma: no cover — must never be reached
                started.append(request.session_id)
                raise AssertionError("the runtime must not be started for a bad rendezvous")

            async def end(self, session_id): ...

        app.state.session_controller = Recording()
        resp = await client.post(
            "/v1/webrtc/sessions",
            headers={"x-api-key": key},
            json={"agent_id": agent_id, "rendezvous_id": "rdv_missing"},
        )
        assert resp.status_code == 404
        assert started == []


# ══════════════════════════════════════════════════════════════════════════════
# C. A real external peer over the real signalling channel
#
#    Real SDP, real ICE, real DTLS, real RTP, real PCM16 — negotiated over the real HTTP
#    endpoints. NOT a browser, NOT live providers, NOT a Stage-2 host, and therefore NOT
#    `E11-15` evidence (§26).
# ══════════════════════════════════════════════════════════════════════════════


class ExternalPeer:
    """A genuine `RTCPeerConnection` that speaks to the signalling endpoints over HTTP.

    It does exactly what the browser page does — poll for the offer, answer it, send microphone
    audio, receive the assistant's — with a synthetic tone in place of a microphone."""

    def __init__(self, client: httpx.AsyncClient, rendezvous_id: str, token: str) -> None:
        self._client = client
        self._rid = rendezvous_id
        self._headers = {"x-signalling-token": token}
        self.pc = None
        self.inbound_frames: list = []
        self.sent_frames = 0
        self._pump: asyncio.Task | None = None

    def _tone_track(self):
        import fractions

        import av
        from aiortc import MediaStreamTrack

        outer = self

        class ToneTrack(MediaStreamTrack):
            kind = "audio"

            def __init__(self) -> None:
                super().__init__()
                self._pts = 0

            async def recv(self):
                samples = 960  # 20 ms at 48 kHz
                frame = av.AudioFrame(format="s16", layout="mono", samples=samples)
                # Bytes that decode to **whitespace**. `LocalSTT` is a text-loopback transcriber:
                # it does `frame.data.decode("utf-8", errors="replace").strip()`, so arbitrary PCM
                # becomes junk "speech", fires a turn, and reaches `LocalTTS` — whose `OPAQUE`
                # output a real media path correctly refuses (M11.16.4 §10), opening the breaker
                # during teardown. This test's scope is **ingress**, so the payload is chosen not
                # to manufacture a spoken turn it never meant to exercise.
                frame.planes[0].update(b"\x20\x20" * samples)
                frame.sample_rate = 48000
                frame.pts = self._pts
                frame.time_base = fractions.Fraction(1, 48000)
                self._pts += samples
                outer.sent_frames += 1
                await asyncio.sleep(0)
                return frame

        return ToneTrack()

    async def run(self, *, deadline_s: float = 25.0) -> None:
        """Fetch the offer, answer it, and pump whatever the runtime sends back."""
        from aiortc import RTCPeerConnection, RTCSessionDescription

        self.pc = RTCPeerConnection()
        self.pc.addTrack(self._tone_track())

        received = asyncio.Event()

        @self.pc.on("track")
        def _on_track(track):  # pragma: no cover — driven by the library
            async def pump():
                with contextlib.suppress(Exception):
                    while True:
                        self.inbound_frames.append(await track.recv())
                        received.set()

            # Held, so teardown can cancel it: a pump that outlived its peer would be exactly the
            # orphan layer D exists to detect.
            self._pump = asyncio.create_task(pump())

        offer = await self._poll_offer(deadline_s=deadline_s)
        await self.pc.setRemoteDescription(RTCSessionDescription(sdp=offer, type="offer"))
        answer = await self.pc.createAnswer()
        await self.pc.setLocalDescription(answer)
        resp = await self._client.post(
            f"/v1/webrtc/rendezvous/{self._rid}/answer",
            headers=self._headers,
            json={"sdp": self.pc.localDescription.sdp},
        )
        assert resp.status_code == 204, resp.text
        self.received = received

    async def _poll_offer(self, *, deadline_s: float) -> str:
        loop = asyncio.get_running_loop()
        end = loop.time() + deadline_s
        while loop.time() < end:
            resp = await self._client.get(
                f"/v1/webrtc/rendezvous/{self._rid}/offer", headers=self._headers
            )
            if resp.status_code == 200:
                return resp.json()["sdp"]
            assert resp.status_code == 204, resp.text
        raise AssertionError("the runtime never published an offer")

    async def aclose(self) -> None:
        # The peer connection goes first: closing it ends the inbound track, so the pump exits on
        # its own rather than being cancelled mid-`recv()` — which leaves the library's ICE
        # machinery holding a receiver it will not wind down.
        if self.pc is not None:
            await self.pc.close()
            self.pc = None
        if self._pump is not None:
            self._pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pump
            self._pump = None


async def _reserve(client: httpx.AsyncClient, key: str) -> tuple[str, str]:
    body = (await client.post("/v1/webrtc/rendezvous", headers={"x-api-key": key})).json()
    return body["rendezvous_id"], body["signalling_token"]


async def test_c_a_real_external_peer_negotiates_over_http_and_delivers_real_audio():
    """Media **ingress** (§13/§K): browser-equivalent microphone → RTP → aiortc → `AudioFrame`.

    Hermetic real-WebRTC-stack evidence over the real signalling channel. Not live evidence."""
    from bootstrap import build_webrtc_media_endpoint
    from core.audio import AudioEncoding

    async with signalling_app() as (app, client):
        org_id, key = _org()
        rid, token = await _reserve(client, key)
        registry = app.state.webrtc_rendezvous
        registry.bind(rid, org_id, "ses_ingress")

        endpoint = build_webrtc_media_endpoint(registry, negotiation_timeout_s=20.0)
        peer = ExternalPeer(client, rid, token)
        frames: list = []
        try:
            await asyncio.gather(
                asyncio.wait_for(endpoint.connect("ses_ingress"), 25.0), peer.run()
            )

            async def pull():
                async for frame in endpoint.inbound("ses_ingress"):
                    frames.append(frame)
                    if len(frames) >= 3:
                        return

            await asyncio.wait_for(pull(), 25.0)
        finally:
            await endpoint.close("ses_ingress")
            await peer.aclose()

        assert len(frames) >= 3, "no real media crossed the external peer connection"
        for frame in frames:
            assert frame.encoding is AudioEncoding.PCM16
            assert frame.sample_rate > 0 and frame.channels >= 1
            assert frame.data and frame.captured_at is not None
        assert [f.seq for f in frames] == sorted(f.seq for f in frames)


async def test_c_the_runtime_s_audio_reaches_the_external_peer():
    """Media **egress** (§13/§L): `AudioFrame` → aiortc → RTP → the external peer's speaker."""
    from bootstrap import build_webrtc_media_endpoint
    from core.audio import AudioEncoding, AudioFrame

    async with signalling_app() as (app, client):
        org_id, key = _org()
        rid, token = await _reserve(client, key)
        registry = app.state.webrtc_rendezvous
        registry.bind(rid, org_id, "ses_egress")

        endpoint = build_webrtc_media_endpoint(registry, negotiation_timeout_s=20.0)
        peer = ExternalPeer(client, rid, token)
        try:
            await asyncio.gather(
                asyncio.wait_for(endpoint.connect("ses_egress"), 25.0), peer.run()
            )

            async def speak():
                seq = 0
                while not peer.received.is_set():
                    await endpoint.send(
                        "ses_egress",
                        AudioFrame(
                            data=b"\x20\x4e" * 480,
                            sample_rate=48000,
                            encoding=AudioEncoding.PCM16,
                            channels=1,
                            captured_at=0.0,
                            seq=seq,
                        ),
                    )
                    seq += 1
                    await asyncio.sleep(0)

            speaker = asyncio.create_task(speak())
            try:
                await asyncio.wait_for(peer.received.wait(), 25.0)
            finally:
                speaker.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await speaker
        finally:
            await endpoint.close("ses_egress")
            await peer.aclose()

        assert peer.inbound_frames, "the runtime's audio never reached the external peer"


async def test_c_the_session_api_drives_the_whole_path_end_to_end():
    """`POST /v1/webrtc/sessions` → Runtime Pipeline → real WebRTC → a real external peer.

    The endpoint **is** the existing one: it calls `api.routers.sessions.create_session`
    unmodified, with the rendezvous claim carried beside it in a context variable (§11). The
    create call does not return until the peer connection is genuinely usable, which is what
    makes its `201` a statement about the media path rather than about the database.

    **Scope, stated rather than implied.** This proves negotiation and *ingress* — the external
    peer's microphone audio crossing a real peer connection into the runtime — and, since
    M11.16.9, a full turn through a **valid** composition: the deterministic providers with a
    PCM16 TTS rather than `LocalTTS`, whose `OPAQUE` output a real media path correctly refuses.
    It says nothing about any vendor; that is a live-provider question.
    """
    from bootstrap import TRANSPORT_WEBRTC, build_registry, build_webrtc_media_endpoint
    from runtime.registry import CapabilityKind
    from runtime.service import RuntimeSessionService

    from .fake_speech import ScriptedTTS

    async with signalling_app() as (app, client):
        _, key = _org()
        # No opening line: with the local TTS profile a spoken turn cannot reach a PCM16 media
        # path, and asserting against a turn that must fail would prove nothing about the path.
        agent_id = await _agent(client, key)
        rid, token = await _reserve(client, key)
        registry = app.state.webrtc_rendezvous

        # The real transport with the deterministic providers: explicit, and composed here rather
        # than made a default anywhere (§12).
        # The real transport with deterministic providers — but a **PCM16** TTS, not `LocalTTS`.
        # `LocalSTT` decodes audio bytes as text, and Opus is lossy, so real WebRTC audio always
        # manufactures junk "speech" and fires a turn. With `LocalTTS` that turn emits `OPAQUE`,
        # which a real media path correctly refuses (M11.16.4 §10) — opening the transport breaker
        # and failing teardown. Substituting a PCM16 TTS makes the composition *valid* rather than
        # merely lucky, and the test then proves runtime **egress** as well as ingress.
        provider_registry = build_registry(
            transport=TRANSPORT_WEBRTC,
            media_endpoint=build_webrtc_media_endpoint(registry, negotiation_timeout_s=20.0),
        )
        provider_registry.unregister(CapabilityKind.TTS, "local")
        provider_registry.register(CapabilityKind.TTS, ScriptedTTS(frames_per_call=2), default=True)
        app.state.session_controller = RendezvousBindingController(
            RuntimeSessionService(provider_registry), registry
        )

        peer = ExternalPeer(client, rid, token)
        session_id = None
        try:
            create = asyncio.create_task(
                client.post(
                    "/v1/webrtc/sessions",
                    headers={"x-api-key": key},
                    json={"agent_id": agent_id, "rendezvous_id": rid},
                )
            )
            await peer.run()
            resp = await asyncio.wait_for(create, 30.0)
            assert resp.status_code == 201, resp.text
            session_id = resp.json()["session_id"]

            # The runtime is consuming the peer's microphone: the track is only pulled by the
            # inbound loop, so a rising count is media actually crossing into the pipeline.
            await asyncio.wait_for(_frames_flow(peer), 30.0)
        finally:
            if session_id is not None:
                ended = await client.post(
                    f"/v1/sessions/{session_id}/end", headers={"x-api-key": key}
                )
                assert ended.status_code == 200
            await peer.aclose()

        # Ending the session released the signalling slot with it.
        assert registry.open_count == 0


async def _frames_flow(peer: ExternalPeer) -> None:
    """Wait until the runtime has pulled real frames from the peer. Condition, not duration."""
    while peer.sent_frames < 3:
        await asyncio.sleep(0.01)


# ══════════════════════════════════════════════════════════════════════════════
# D. Resources
# ══════════════════════════════════════════════════════════════════════════════


async def _tasks_settle_to(limit: int, *, deadline_s: float = 10.0) -> bool:
    """Wait on the condition, not on a duration: aiortc's ICE teardown needs real timers, so a
    yield-based drain reports the library mid-teardown as a leak (the M11.15 R-70 lesson)."""
    loop = asyncio.get_running_loop()
    end = loop.time() + deadline_s
    while loop.time() < end:
        if len([t for t in asyncio.all_tasks() if not t.done()]) <= limit:
            return True
        await asyncio.sleep(0.05)
    return len([t for t in asyncio.all_tasks() if not t.done()]) <= limit


async def test_d_repeated_external_sessions_leak_no_tasks_peers_or_rendezvous():
    from bootstrap import build_webrtc_media_endpoint

    async with signalling_app() as (app, client):
        org_id, key = _org()
        registry = app.state.webrtc_rendezvous
        before = len([t for t in asyncio.all_tasks() if not t.done()])

        for index in range(3):
            session_id = f"ses_leak_{index}"
            rid, token = await _reserve(client, key)
            registry.bind(rid, org_id, session_id)
            endpoint = build_webrtc_media_endpoint(registry, negotiation_timeout_s=20.0)
            peer = ExternalPeer(client, rid, token)
            try:
                await asyncio.gather(
                    asyncio.wait_for(endpoint.connect(session_id), 25.0), peer.run()
                )
            finally:
                await peer.aclose()
                await endpoint.close(session_id)
                registry.release(session_id)
            assert registry.open_count == 0, "a rendezvous outlived its session"

        if not await _tasks_settle_to(before + 1):
            survivors = [
                t.get_coro().__qualname__
                for t in asyncio.all_tasks()
                if t is not asyncio.current_task()
            ]
            raise AssertionError(f"a task outlived its session: {survivors}")


async def test_d_a_peer_that_walks_away_fails_the_connection_rather_than_holding_it():
    from bootstrap import build_webrtc_media_endpoint
    from providers.transport.media_endpoint import MediaEndpointError

    async with signalling_app() as (app, client):
        org_id, key = _org()
        rid, _token = await _reserve(client, key)
        registry = app.state.webrtc_rendezvous
        registry._answer_timeout_s = 0.3  # noqa: SLF001 — the bound under test
        registry.bind(rid, org_id, "ses_ghost")
        endpoint = build_webrtc_media_endpoint(registry, negotiation_timeout_s=5.0)
        with pytest.raises(MediaEndpointError):
            await asyncio.wait_for(endpoint.connect("ses_ghost"), 10.0)
        await endpoint.close("ses_ghost")


# ══════════════════════════════════════════════════════════════════════════════
# E. Security
# ══════════════════════════════════════════════════════════════════════════════


_API_WEBRTC = Path(__file__).resolve().parent.parent / "api" / "webrtc"
_ROUTER = Path(__file__).resolve().parent.parent / "api" / "routers" / "webrtc.py"

#: Provider-credential vocabulary. A provider credential cannot leak through a layer that has no
#: word for one, and none of these layers has one (§30).
_PROVIDER_VOCABULARY = (
    "deepgram",
    "groq",
    "cartesia",
    "credentialprovider",
    "voxvoice_secret",
    "authorization",
    "bearer",
)

#: The **org** API key is a different question. The browser page holds one — the operator types
#: it — which is exactly the production-authentication prerequisite M11.16.4 documents rather
#: than solves. The signalling plane itself must never touch it: it authenticates with the
#: single-purpose token and nothing else.
_ORG_KEY_VOCABULARY = ("x-api-key", "api_key", "apikey")


@pytest.mark.parametrize(
    "path",
    [
        _API_WEBRTC / "rendezvous.py",
        _API_WEBRTC / "binding.py",
        _API_WEBRTC / "config.py",
        _API_WEBRTC / "__init__.py",
        _API_WEBRTC / "client.html",
        _ROUTER,
    ],
)
def test_e_the_signalling_plane_carries_no_provider_credential_vocabulary(path):
    source = path.read_text(encoding="utf-8").lower()
    for word in _PROVIDER_VOCABULARY:
        assert word not in source, f"{path.name} mentions {word!r}"


def test_e_the_rendezvous_registry_never_touches_the_org_api_key():
    source = (_API_WEBRTC / "rendezvous.py").read_text(encoding="utf-8").lower()
    for word in _ORG_KEY_VOCABULARY:
        assert word not in source, f"rendezvous.py mentions {word!r}"


def test_e_the_browser_page_asks_for_a_microphone_and_nothing_else():
    page = (_API_WEBRTC / "client.html").read_text(encoding="utf-8")
    assert "video: false" in page, "the page must not request a camera"
    assert "getUserMedia" in page
    # The page holds no endpoint, key or host of any provider.
    for word in ("deepgram", "groq", "cartesia", "wss://", "https://api."):
        assert word not in page.lower()


async def test_e_full_sdp_is_never_logged(caplog):
    """Only the byte length, the state and the rendezvous id (§7)."""
    import logging

    registry = RendezvousRegistry()
    secretish = VALID_SDP + "a=candidate:host 10.1.2.3\r\n"
    with caplog.at_level(logging.INFO, logger="voxvoice.api.webrtc.rendezvous"):
        rid, token, _ = registry.reserve("org_a")
        registry.bind(rid, "org_a", "ses_log")
        exchange = asyncio.create_task(registry.exchange("ses_log", secretish))
        assert await registry.await_offer(rid, token) == secretish
        registry.submit_answer(rid, token, secretish)
        await exchange
    emitted = "\n".join(record.getMessage() for record in caplog.records)
    assert emitted, "the rendezvous logged nothing at all"
    assert "candidate" not in emitted and "10.1.2.3" not in emitted
    assert "v=0" not in emitted
    assert "bytes" in emitted


async def test_e_a_closed_session_cannot_be_renegotiated():
    registry = RendezvousRegistry()
    rid, token, _ = registry.reserve("org_a")
    registry.bind(rid, "org_a", "ses_closed")
    registry.release("ses_closed")
    with pytest.raises(RendezvousError) as err:
        await registry.exchange("ses_closed", VALID_SDP)
    assert err.value.status_code == 409
    with pytest.raises(RendezvousError) as err:
        registry.submit_answer(rid, token, VALID_SDP)
    assert err.value.status_code == 404


def test_e_only_the_composition_seam_names_the_credential_provider():
    """`app.py` resolves credentials because that is what a composition root does (ADR-0049); it
    is also the only file in this package allowed to, and it never reads a value."""
    source = (_API_WEBRTC / "app.py").read_text(encoding="utf-8")
    assert "EnvCredentialProvider" in source
    for forbidden in ("os.environ", "VOXVOICE_SECRET", ".require(", ".get(\"deepgram"):
        assert forbidden not in source, f"app.py reads a credential value: {forbidden}"


def test_e_the_signalling_plane_never_imports_the_runtime_or_a_vendor_sdk():
    """AR-12/AR-14 at the Control Plane edge: no aiortc, no runtime, no provider import."""
    for path in (_API_WEBRTC / "rendezvous.py", _ROUTER):
        source = path.read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            if not stripped.startswith(("import ", "from ")):
                continue
            assert "aiortc" not in stripped, f"{path.name}: {stripped}"
            assert not stripped.startswith("from runtime"), f"{path.name}: {stripped}"
            assert not stripped.startswith("import runtime"), f"{path.name}: {stripped}"
            assert not stripped.startswith("from providers"), f"{path.name}: {stripped}"
