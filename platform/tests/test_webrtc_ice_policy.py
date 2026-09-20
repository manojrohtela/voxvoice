"""R-71: the ICE policy is stated by the composition root, never inherited (M11.16.6).

M11.16.5 measured ICE gathering on a NAT'd host and found a `srflx` candidate the documentation
said could not exist. `aiortc` substitutes `stun:stun.l.google.com:19302` whenever `iceServers` is
`None`, and `AiortcMediaEndpoint` constructed `RTCPeerConnection()` with no configuration — so
every call setup contacted a third party that appeared in no ADR, no configuration surface and no
deployment document.

These tests pin the resolution from three directions:

1. **The library's behaviour**, so the thing that surprised us is now something that would fail
   loudly if it changed underneath us.
2. **Our suppression of it**, measured through real gathering rather than asserted.
3. **The configuration path**, so a deployment's stated policy is what actually reaches the peer
   connection.

Nothing here needs the internet: the empty policy performs no network I/O by construction, and the
configured-STUN cases are asserted at the configuration boundary rather than by contacting a
server. There is no `skip`, `skipif` or `xfail` (`E11-1`), no sleep and no spin.
"""

from __future__ import annotations

import asyncio
import re

import pytest

from api.webrtc.app import build_webrtc_app, resolve_ice_policy
from api.webrtc.config import WebRTCConfigError, WebRTCSettings
from providers.transport.ice import NO_ICE_SERVERS, IcePolicy, IcePolicyError

STUN_A = "stun:stun.example.org:3478"
STUN_B = "stuns:secure.example.org:5349"


# ══════════════════════════════════════════════════════════════════════════════
# 1. The library behaviour this milestone exists to stop inheriting
# ══════════════════════════════════════════════════════════════════════════════


def test_aiortc_still_substitutes_a_public_stun_server_when_given_none():
    """The R-71 mechanism, pinned. If aiortc ever stops doing this, we want to know from a test
    rather than from a behaviour change in production."""
    from aiortc.rtcicetransport import RTCIceGatherer, connection_kwargs

    defaults = RTCIceGatherer.getDefaultIceServers()
    assert defaults, "aiortc no longer supplies a default ICE server; revisit ADR-0054"
    assert "stun:" in str(defaults[0].urls)
    # …and the empty list is genuinely empty, which is what makes suppression possible at all.
    assert connection_kwargs([]) == {}
    assert "stun_server" in connection_kwargs(defaults)


# ══════════════════════════════════════════════════════════════════════════════
# 2. The policy value object
# ══════════════════════════════════════════════════════════════════════════════


def test_the_default_policy_is_explicitly_no_ice_servers():
    """Not 'unset'. The distinction is the whole of R-71."""
    assert NO_ICE_SERVERS.stun_urls == ()
    assert NO_ICE_SERVERS.uses_stun is False
    assert "explicit" in NO_ICE_SERVERS.describe()


def test_stun_urls_are_accepted_and_deduplicated_in_order():
    policy = IcePolicy.from_urls([STUN_A, " ", STUN_B, STUN_A])
    assert policy.stun_urls == (STUN_A, STUN_B)
    assert policy.uses_stun is True


def test_a_turn_url_is_refused_rather_than_ignored():
    """Accepting it would let a deployment believe it had configured relaying that does not
    exist — the failure mode is silence at 3 a.m., not a startup error."""
    for url in ("turn:relay.example.org:3478", "TURNS:relay.example.org:5349"):
        with pytest.raises(IcePolicyError, match="TURN"):
            IcePolicy.from_urls([url])


def test_a_non_stun_url_is_refused():
    for url in ("https://stun.example.org", "stun.example.org:3478", "wss://x"):
        with pytest.raises(IcePolicyError):
            IcePolicy.from_urls([url])


def test_the_policy_description_is_log_safe_and_states_the_shape():
    assert IcePolicy.from_urls([STUN_A]).describe() == (
        "1 STUN server(s) (host + server-reflexive candidates)"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 3. Suppression, measured — not asserted
# ══════════════════════════════════════════════════════════════════════════════


async def _gather_candidate_types(endpoint) -> set[str]:
    """Gather on a peer connection built by the endpoint under test and report candidate types."""
    pc = endpoint._new_peer_connection()  # noqa: SLF001 — the construction under test
    try:
        pc.addTransceiver("audio", direction="sendrecv")
        await pc.setLocalDescription(await pc.createOffer())
        types = set()
        for line in re.findall(r"^a=candidate:(.+)$", pc.localDescription.sdp, re.M):
            parts = line.split()
            types.add(parts[parts.index("typ") + 1])
        return types
    finally:
        await pc.close()


async def test_the_default_policy_contacts_no_stun_server_and_gathers_host_only():
    """The measurement that failed before M11.16.6: exactly one candidate type, and it is `host`.

    A `srflx` candidate here would mean a STUN server was reached, which is the defect."""
    from providers.transport.aiortc_endpoint import AiortcMediaEndpoint

    endpoint = AiortcMediaEndpoint(object())
    types = await asyncio.wait_for(_gather_candidate_types(endpoint), 20.0)
    assert types == {"host"}, f"expected host-only gathering, got {sorted(types)}"


async def test_the_endpoint_passes_a_configuration_even_for_the_empty_policy():
    """Passing nothing and passing nothing-to-use are different facts; only the second is a
    decision, and only the second suppresses the library default."""
    from providers.transport.aiortc_endpoint import AiortcMediaEndpoint

    endpoint = AiortcMediaEndpoint(object())
    pc = endpoint._new_peer_connection()  # noqa: SLF001
    try:
        configuration = pc._RTCPeerConnection__configuration  # noqa: SLF001
        assert configuration.iceServers == [], "the empty policy must reach the library as []"
        assert configuration.iceServers is not None
    finally:
        await pc.close()


async def test_a_configured_stun_server_reaches_the_peer_connection():
    """The deployment's stated servers are the ones the library is given — asserted at the
    boundary rather than by contacting a server, so this test needs no network."""
    from providers.transport.aiortc_endpoint import AiortcMediaEndpoint

    endpoint = AiortcMediaEndpoint(object(), ice_policy=IcePolicy.from_urls([STUN_A, STUN_B]))
    pc = endpoint._new_peer_connection()  # noqa: SLF001
    try:
        urls = [str(s.urls) for s in pc._RTCPeerConnection__configuration.iceServers]  # noqa: SLF001
        assert urls == [STUN_A, STUN_B]
    finally:
        await pc.close()


# ══════════════════════════════════════════════════════════════════════════════
# 4. The configuration path
# ══════════════════════════════════════════════════════════════════════════════


def test_the_composition_root_resolves_the_stated_policy():
    assert resolve_ice_policy(WebRTCSettings()).stun_urls == ()
    assert resolve_ice_policy(WebRTCSettings(ice_stun_urls=(STUN_A,))).stun_urls == (STUN_A,)


def test_settings_read_the_ice_policy_from_the_environment():
    loaded = WebRTCSettings.from_env({"VOXVOICE_ICE_STUN_URLS": f"{STUN_A}, {STUN_B}"})
    assert loaded.ice_stun_urls == (STUN_A, STUN_B)
    assert WebRTCSettings.from_env({}).ice_stun_urls == ()


def test_a_turn_url_in_configuration_fails_startup_not_a_call():
    with pytest.raises(WebRTCConfigError, match="TURN"):
        build_webrtc_app(WebRTCSettings(ice_stun_urls=("turn:relay.example.org:3478",)))


def test_a_malformed_ice_url_fails_startup():
    with pytest.raises(WebRTCConfigError):
        build_webrtc_app(WebRTCSettings(ice_stun_urls=("not-a-url",)))


def test_the_health_endpoint_states_the_policy_in_force():
    """An operator should be able to ask a running deployment what it decided, rather than infer
    it from a packet capture."""
    app = build_webrtc_app(WebRTCSettings(signalling=True, ice_stun_urls=(STUN_A,)))
    health = next(r for r in app.routes if getattr(r, "path", "") == "/health")
    assert "ice_policy" in health.endpoint()


# ══════════════════════════════════════════════════════════════════════════════
# 5. Boundaries
# ══════════════════════════════════════════════════════════════════════════════


def test_the_ice_policy_module_imports_no_media_library():
    """AR-12: the policy is a value object. Only `aiortc_endpoint.py` may translate it."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "providers/transport/ice.py").read_text()
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            assert "aiortc" not in stripped, stripped
            assert "av" != stripped.split()[1], stripped


def test_the_control_plane_configuration_does_not_import_the_provider_plane():
    """`api.webrtc.config` carries the URLs as strings; the composition root translates them."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "api/webrtc/config.py").read_text()
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            assert not stripped.startswith(("from providers", "import providers")), stripped
