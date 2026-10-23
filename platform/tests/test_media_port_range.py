"""Bounded UDP media port range (M13.2).

The deployment problem: a shared host cannot have its whole ephemeral range opened to UDP, and
narrowing `net.ipv4.ip_local_port_range` is **host-wide** — it re-pools the ephemeral ports of
every other service on the machine. So the bound is applied inside this process instead, by
supplying an explicit port to the one `aioice` call that would otherwise pass `0`.

**Ranges are half-open, `[lo, hi)`, here and everywhere else.**

Three layers, deliberately separated:

**A. Specification and detector** — the parser and the post-condition, as pure functions. No
library, no sockets. This is where the three distinct failure modes are pinned, including the one
that must *not* fire: a `srflx` candidate's port is the NAT-mapped one and may legitimately sit
outside the local range.

**B. Allocator mechanics** — the loop wrapper: scoping, signature fidelity, retry semantics,
accounting, and the inherited-context caveat.

**C. Real peers** — two genuine `RTCPeerConnection`s, with the real `AiortcMediaEndpoint`,
proving the advertised candidate ports are the bounded ones, that exhaustion fails closed, and
that ports are released. **Loopback in one process: not NAT, not a browser, not a public host.**

Socket leaks are detected portably by re-binding every port in the range afterwards — if anything
is still held, the probe fails. No `psutil`, no `/proc`.

No `skip`, `skipif` or `xfail`, and nothing sleeps for a duration while waiting on an outcome.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import re

import pytest
import uvloop
from aioice.ice import get_host_addresses

from providers.transport.aiortc_endpoint import AiortcMediaEndpoint
from providers.transport.ice import IcePolicy, IcePolicyError, parse_media_port_range
from providers.transport.media_endpoint import MediaEndpointError
from providers.transport.signalling import PeerSignaller, SdpExchange
from providers.transport.udp_ports import (
    BoundedPortRangeExhausted,
    PortScope,
    bounded_ports,
    host_candidate_ports,
    install,
    verify_host_candidate_ports,
)
from tests.test_webrtc_signalling import LoopbackExchange

#: Where test ranges live. High, and far from anything the suite otherwise binds.
BASE = 54000
CONNECT_DEADLINE_S = 30.0


def _addresses() -> list[str]:
    """The addresses `aioice` will bind, read the same way it reads them.

    Counted at run time rather than assumed: the number differs between machines and CI runners,
    and IPv6 privacy extensions rotate temporary addresses even on one host."""
    return get_host_addresses(use_ipv4=True, use_ipv6=True)


def _width(calls: int = 1) -> int:
    """A range wide enough for `calls` concurrent agents, plus one slot of teardown overlap.

    One socket per address per agent, and each may take a different port — so the budget is
    `addresses x agents`, never `agents`. This is a sizing estimate, not a guarantee."""
    return max(2, len(_addresses()) * (calls + 1))


async def _probe_free(
    lo: int, hi: int, addresses: list[str] | None = None
) -> list[tuple[str, int]]:
    """Every `(address, port)` in the range that is still held. Empty means nothing leaked.

    `addresses` must name the addresses actually used: `get_host_addresses` excludes `127.0.0.1`,
    so a probe that defaulted to it would silently look in the wrong place."""
    loop = asyncio.get_running_loop()
    held: list[tuple[str, int]] = []
    transports = []
    try:
        for address in addresses if addresses is not None else _addresses():
            for port in range(lo, hi):
                try:
                    transport, _ = await loop.create_datagram_endpoint(
                        asyncio.DatagramProtocol, local_addr=(address, port)
                    )
                except OSError as exc:
                    if exc.errno == errno.EADDRINUSE:
                        held.append((address, port))
                    continue  # unusable address is not a leak
                transports.append(transport)
    finally:
        for transport in transports:
            transport.close()
    return held


_HOST_PAIR = re.compile(
    r"^a=candidate:\S+\s+\d+\s+\S+\s+\d+\s+(?P<ip>\S+)\s+(?P<port>\d+)\s+typ\s+host\b",
    re.MULTILINE,
)


def _host_pairs(sdp: str) -> list[tuple[str, int]]:
    """Every `typ host` candidate as `(address, port)` — the pair the kernel actually arbitrates."""
    return [(m.group("ip"), int(m.group("port"))) for m in _HOST_PAIR.finditer(sdp or "")]


def _recording_wrapper(loop) -> list[tuple[str, int]]:
    """Record every address/port this loop binds from now on, delegating to whatever is installed.

    Wrapped **before** the endpoint calls `install`, so the allocator ends up calling this and the
    record shows the explicit port it chose — which is how a test can assert that nothing was
    bound outside the configured range."""
    seen: list[tuple[str, int]] = []
    original = loop.create_datagram_endpoint

    async def recording(protocol_factory, local_addr=None, remote_addr=None, **kw):
        result = await original(protocol_factory, local_addr, remote_addr, **kw)
        sock = result[0].get_extra_info("socket")
        if sock is not None:
            seen.append(sock.getsockname()[:2])
        return result

    loop.create_datagram_endpoint = recording
    return seen


def _endpoint(exchange: SdpExchange, lo: int, hi: int, *, timeout: float = 20.0):
    return AiortcMediaEndpoint(
        PeerSignaller(exchange, negotiation_timeout_s=timeout),
        ice_policy=IcePolicy(media_port_range=(lo, hi)),
    )


# ══════════════════════════════════════════════════════════════════════════════
# A. Specification and detector
# ══════════════════════════════════════════════════════════════════════════════


def test_the_range_is_parsed_half_open():
    assert parse_media_port_range("51000-51032") == (51000, 51032)
    assert parse_media_port_range(" 51000-51032 ") == (51000, 51032)


def test_an_absent_range_means_the_os_ephemeral_range():
    for spec in (None, "", "   "):
        assert parse_media_port_range(spec) is None
    assert IcePolicy().media_port_range is None, "the default must not bound anything"
    assert IcePolicy().bounds_media_ports is False


@pytest.mark.parametrize(
    "spec",
    [
        "51000",  # not a range
        "51000-51010-51020",  # three bounds
        "a-b",  # not integers
        "51032-51000",  # inverted
        "51000-51000",  # empty under half-open semantics
        "80-443",  # privileged
        "65000-70000",  # past the last port
    ],
)
def test_a_malformed_range_is_refused_not_ignored(spec: str):
    with pytest.raises(IcePolicyError):
        parse_media_port_range(spec)


def test_the_policy_describes_the_range_only_when_one_is_set():
    """A feature that is off must not change what the deployment logs about itself.

    `test_webrtc_ice_policy.py` asserts the unbounded description verbatim, so the clause is
    additive and appears only when a range was actually stated."""
    assert IcePolicy().describe() == "no ICE servers (explicit; host candidates only)"
    described = IcePolicy(media_port_range=(51000, 51032)).describe()
    assert "[51000,51032)" in described and "confined" in described


_SDP_HOST = (
    "v=0\r\n"
    "a=candidate:1 1 udp 2130706431 192.0.2.1 51001 typ host\r\n"
    "a=candidate:2 1 udp 2130706431 192.0.2.1 51002 typ host\r\n"
)


def test_only_host_candidates_are_read():
    srflx = "a=candidate:3 1 udp 1 203.0.113.9 9999 typ srflx raddr 192.0.2.1 rport 51001\r\n"
    sdp = _SDP_HOST + srflx
    assert host_candidate_ports(sdp) == (51001, 51002)


def test_a_reflexive_candidate_outside_the_range_is_not_a_violation():
    """The false positive that would break every STUN deployment behind symmetric NAT.

    A `srflx` candidate's port is the address the NAT mapped, not the one bound locally. On a 1:1
    NAT they coincide; behind symmetric NAT they do not, and failing the call for that would be
    wrong."""
    srflx = "a=candidate:3 1 udp 1 203.0.113.9 62000 typ srflx raddr 192.0.2.1 rport 51001\r\n"
    sdp = _SDP_HOST + srflx
    scope = PortScope(
        lo=51000, hi=51032, bound=2,
        placed=(("192.0.2.1", 51001), ("192.0.2.1", 51002)),
    )
    assert verify_host_candidate_ports(scope, sdp) is None


def test_the_detector_reports_exhaustion():
    scope = PortScope(
        lo=51000, hi=51002, bound=1, exhausted=1, exhausted_addresses=("192.0.2.1",)
    )
    problem = verify_host_candidate_ports(scope, _SDP_HOST)
    assert problem is not None and "exhausted" in problem and "192.0.2.1" in problem


def test_the_detector_reports_a_total_bypass():
    """A future `aioice` that pre-binds and passes `sock=` never consults the wrapper.

    `placed` stays empty while the description still advertises host candidates."""
    scope = PortScope(lo=51000, hi=51032, bound=0)
    problem = verify_host_candidate_ports(scope, _SDP_HOST)
    assert problem is not None and "not placed by the allocator" in problem


def test_the_detector_reports_a_partial_bypass():
    """The case a call counter alone would miss: some sockets placed, one not.

    Both advertised ports are inside the range, and the allocator did run — but it never placed
    51002, so that port came from somewhere else."""
    scope = PortScope(
        lo=51000, hi=51032, bound=1, placed=(("192.0.2.1", 51001),)
    )
    problem = verify_host_candidate_ports(scope, _SDP_HOST)
    assert problem is not None and "51002" in problem and "not placed" in problem


def test_the_detector_reports_a_port_outside_the_range():
    scope = PortScope(lo=51000, hi=51002, bound=2, placed=(("192.0.2.1", 51001),))
    problem = verify_host_candidate_ports(scope, _SDP_HOST)
    assert problem is not None and "51002" in problem and "outside" in problem


# ══════════════════════════════════════════════════════════════════════════════
# B. Allocator mechanics
# ══════════════════════════════════════════════════════════════════════════════


async def test_installation_is_idempotent():
    loop = asyncio.get_running_loop()
    install(loop)
    once = loop.create_datagram_endpoint
    install(loop)
    assert loop.create_datagram_endpoint is once, "the loop was wrapped twice"


async def test_outside_a_scope_ports_are_untouched():
    """The scoping boundary: unrelated UDP work must not be captured."""
    loop = asyncio.get_running_loop()
    install(loop)
    lo, hi = BASE, BASE + 2
    transport, _ = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0)
    )
    try:
        port = transport.get_extra_info("socket").getsockname()[1]
        assert not lo <= port < hi, "a bind outside any scope was confined to the range"
    finally:
        transport.close()


async def test_inside_a_scope_a_child_task_inherits_the_bound():
    """The documented caveat, pinned rather than hidden.

    `contextvars` are copied into a task at creation, which is exactly what carries the scope into
    `aioice`'s `asyncio.gather`. The same mechanism means *any* port-0 bind made in a task created
    inside the scope is confined — including, hypothetically, unrelated work. In this process the
    only thing running inside that window is ICE gathering; this test exists so a future
    composition that changes that fails here rather than silently."""
    loop = asyncio.get_running_loop()
    install(loop)
    lo, hi = BASE + 10, BASE + 12

    async def child() -> int:
        transport, _ = await loop.create_datagram_endpoint(
            asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0)
        )
        try:
            return transport.get_extra_info("socket").getsockname()[1]
        finally:
            transport.close()

    async with bounded_ports(lo, hi):
        port = await asyncio.ensure_future(child())
    assert lo <= port < hi, "a child task did not inherit the scope"


async def test_a_preassigned_port_is_left_alone():
    """Only `port == 0` is the allocator's business; an explicit port passes through."""
    loop = asyncio.get_running_loop()
    install(loop)
    async with bounded_ports(BASE + 20, BASE + 22):
        transport, _ = await loop.create_datagram_endpoint(
            asyncio.DatagramProtocol, local_addr=("127.0.0.1", BASE + 40)
        )
        try:
            assert transport.get_extra_info("socket").getsockname()[1] == BASE + 40
        finally:
            transport.close()


async def test_remote_addr_may_be_positional():
    """Signature fidelity: `local_addr` and `remote_addr` are positional-or-keyword."""
    loop = asyncio.get_running_loop()
    install(loop)
    async with bounded_ports(BASE + 30, BASE + 32):
        transport, _ = await loop.create_datagram_endpoint(
            asyncio.DatagramProtocol, ("127.0.0.1", 0), ("127.0.0.1", 9)
        )
        try:
            assert BASE + 30 <= transport.get_extra_info("socket").getsockname()[1] < BASE + 32
        finally:
            transport.close()


async def test_exhaustion_raises_an_oserror_so_aioice_can_clean_up():
    """`BoundedPortRangeExhausted` **must** be an `OSError`.

    `aioice` registers the sockets it has created only after its bind loop
    (`aioice/ice.py:1009`), and catches `OSError` inside it. An exception of any other type would
    escape the loop and orphan every socket bound so far."""
    assert issubclass(BoundedPortRangeExhausted, OSError)
    loop = asyncio.get_running_loop()
    install(loop)
    lo, hi = BASE + 50, BASE + 51
    async with bounded_ports(lo, hi) as scope:
        first, _ = await loop.create_datagram_endpoint(
            asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0)
        )
        try:
            with pytest.raises(BoundedPortRangeExhausted) as caught:
                await loop.create_datagram_endpoint(
                    asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0)
                )
            assert caught.value.errno == errno.EADDRINUSE
            assert scope.bound == 1
            assert scope.exhausted == 1
            assert scope.exhausted_addresses == ("127.0.0.1",)
        finally:
            first.close()


async def test_a_non_busy_error_is_not_retried():
    """`EACCES` and friends are faults, not "port busy" — retrying would bury the real errno."""
    loop = asyncio.get_running_loop()
    install(loop)
    async with bounded_ports(BASE + 60, BASE + 70) as scope:
        with pytest.raises(OSError) as caught:
            # An address this host does not own: EADDRNOTAVAIL, not EADDRINUSE.
            await loop.create_datagram_endpoint(
                asyncio.DatagramProtocol, local_addr=("203.0.113.254", 0)
            )
        assert caught.value.errno != errno.EADDRINUSE
        assert not isinstance(caught.value, BoundedPortRangeExhausted)
        assert scope.bound == 0 and scope.exhausted == 0


async def test_cancellation_mid_gather_orphans_sockets_in_the_pinned_library():
    """**Documents a pinned-`aioice` limitation. It does not claim cleanup.**

    `aioice.Connection.get_component_candidates` binds one socket per address, and appends the
    protocols it created to `Connection._protocols` only **after** that loop
    (`aioice/ice.py:1009`). `Connection.close()` closes only that list (`:635-637`). So an
    exception which is not an `OSError` — `CancelledError` being the realistic one — escapes the
    loop and leaves every socket bound so far unregistered and unclosed.

    This drives the library's own loop directly, with a duplicated address so exactly two binds
    are attempted regardless of how many addresses the host has, and lets a `CancelledError`
    arrive at the second. It then asserts what actually happens, so a future `aioice` that fixes
    this fails here and the limitation can be retired.

    The allocator is built so as not to *add* such a path: it raises `OSError` and nothing else,
    which `aioice` catches, keeping every successful bind under its normal ownership."""
    from aioice.ice import Connection

    addresses = _addresses()
    assert addresses, "this host exposes no global address for aioice to bind"
    address = addresses[0]
    lo, hi = BASE + 80, BASE + 90

    loop = asyncio.get_running_loop()
    install(loop)
    original = loop.create_datagram_endpoint
    placed: list[object] = []

    async def cancel_on_second(factory, local_addr=None, remote_addr=None, **kw):
        if local_addr is not None and local_addr[1] == 0 and placed:
            raise asyncio.CancelledError
        result = await original(factory, local_addr, remote_addr, **kw)
        if local_addr is not None and local_addr[1] == 0:
            placed.append(result[0])
        return result

    connection = Connection(ice_controlling=True)
    loop.create_datagram_endpoint = cancel_on_second
    try:
        async with bounded_ports(lo, hi):
            with contextlib.suppress(asyncio.CancelledError):
                await connection.get_component_candidates(
                    component=1, addresses=[address, address]
                )
        assert len(placed) == 1, "the first bind should have succeeded before the cancellation"
        # Nothing was registered, so `close()` has nothing to close.
        registered = list(connection._protocols)  # noqa: SLF001
        await connection.close()
        held = await _probe_free(lo, hi, [address])
    finally:
        loop.create_datagram_endpoint = original
        for transport in placed:
            transport.close()

    assert registered == [], (
        "aioice registered its protocols before the cancellation; the orphaning window this "
        "limitation describes may be gone — re-check udp_ports' docstring before relaxing this"
    )
    assert held, (
        "a socket bound before cancellation was released despite never being registered; the "
        "pinned-aioice orphaning limitation may be fixed — re-check before relaxing this"
    )


# ══════════════════════════════════════════════════════════════════════════════
# C. Real peers — loopback only
# ══════════════════════════════════════════════════════════════════════════════


async def test_real_candidate_ports_are_the_bounded_ones():
    """The headline. **Hermetic loopback evidence — not NAT, browser or public-host evidence.**"""
    lo = BASE + 100
    hi = lo + _width(calls=1)
    exchange = LoopbackExchange()
    endpoint = _endpoint(exchange, lo, hi)
    session = "range-ok"
    try:
        await asyncio.wait_for(endpoint.connect(session), CONNECT_DEADLINE_S)
        local = endpoint._connections[session]  # noqa: SLF001
        ports = host_candidate_ports(local.localDescription.sdp)
        assert ports, "no host candidate was advertised"
        assert all(lo <= p < hi for p in ports), (ports, lo, hi)
    finally:
        await endpoint.close(session)
        await exchange.aclose()
    assert not await _probe_free(lo, hi), "a media socket outlived the session"


async def test_sequential_sessions_release_and_reuse_ports(monkeypatch):
    """Release **and** reuse. The previous version asserted only that three sessions ran.

    The scan start is pinned to `lo` so reuse is deterministic: if a port were not released, the
    next session could not land on it again and the final assertion would fail."""
    monkeypatch.setattr("providers.transport.udp_ports.random.randrange", lambda _n: 0)
    lo = BASE + 150
    hi = lo + _width(calls=1)
    seen: list[tuple[int, ...]] = []
    for index in range(3):
        exchange = LoopbackExchange()
        endpoint = _endpoint(exchange, lo, hi)
        session = f"reuse-{index}"
        try:
            await asyncio.wait_for(endpoint.connect(session), CONNECT_DEADLINE_S)
            local = endpoint._connections[session]  # noqa: SLF001
            ports = host_candidate_ports(local.localDescription.sdp)
            assert all(lo <= p < hi for p in ports), ports
            seen.append(ports)
        finally:
            await endpoint.close(session)
            await exchange.aclose()
    assert len(seen) == 3
    assert seen[0], "no host candidate was advertised"
    assert seen[0] == seen[2], (
        f"ports were not reused across sessions: {seen[0]} then {seen[2]}; a port held by an "
        "earlier session cannot be taken again by a later one"
    )
    assert not await _probe_free(lo, hi), "ports were not released after the last session"


async def test_a_range_too_small_refuses_the_call_and_binds_nothing_outside_it():
    """Fail closed: a bound that cannot be met must refuse, never fall back.

    Every bind the loop performs during the attempt is recorded, so "nothing outside the range"
    is asserted from what actually happened rather than inferred from the failure."""
    lo = BASE + 200
    hi = lo + 1  # a single port: fewer than the sockets this negotiation needs
    bound = _recording_wrapper(asyncio.get_running_loop())
    exchange = LoopbackExchange()
    endpoint = _endpoint(exchange, lo, hi, timeout=8.0)
    session = "too-small"
    try:
        with pytest.raises(MediaEndpointError):
            await asyncio.wait_for(endpoint.connect(session), CONNECT_DEADLINE_S)
    finally:
        with contextlib.suppress(Exception):
            await endpoint.close(session)
        with contextlib.suppress(Exception):
            await exchange.aclose()

    assert session not in endpoint._connections  # noqa: SLF001
    stray = [pair for pair in bound if not lo <= pair[1] < hi]
    assert not stray, f"the call fell back to ports outside [{lo},{hi}): {stray}"


async def test_two_simultaneous_negotiations_share_a_wide_enough_range():
    """Contention: distinct `(address, port)` pairs, which is the real invariant.

    Ports are **not** required to differ across addresses — `aioice` binds each address
    independently, so two agents may legitimately hold the same port number on different
    addresses. What may never happen is two sockets on one `(address, port)`."""
    lo = BASE + 250
    hi = lo + _width(calls=2)
    exchanges = [LoopbackExchange(), LoopbackExchange()]
    endpoints = [_endpoint(exchanges[0], lo, hi), _endpoint(exchanges[1], lo, hi)]
    sessions = ["race-a", "race-b"]
    try:
        await asyncio.wait_for(
            asyncio.gather(
                *(e.connect(s) for e, s in zip(endpoints, sessions, strict=True))
            ),
            CONNECT_DEADLINE_S,
        )
        pairs: list[tuple[str, int]] = []
        for endpoint, session in zip(endpoints, sessions, strict=True):
            local = endpoint._connections[session]  # noqa: SLF001
            advertised = _host_pairs(local.localDescription.sdp)
            assert advertised, (session, "no host candidate was advertised")
            assert all(lo <= port < hi for _ip, port in advertised), (session, advertised)
            pairs.extend(advertised)
        # The invariant is on `(address, port)`, not on ports: aioice binds each address
        # independently, so two agents may legitimately hold the same port number on different
        # addresses. What may never happen is two simultaneously open sockets on one pair.
        assert len(pairs) == len(set(pairs)), f"two sockets shared an (address, port): {pairs}"
    finally:
        for endpoint, session in zip(endpoints, sessions, strict=True):
            await endpoint.close(session)
        for exchange in exchanges:
            await exchange.aclose()
    assert not await _probe_free(lo, hi), "a socket outlived one of the concurrent sessions"


async def test_an_unbounded_policy_never_installs_the_wrapper():
    """Default off, asserted on the mechanism rather than on an outcome.

    The previous version of this test ended in `... or True`, a tautology that could not fail. The
    real guarantee is that an unconfigured policy never touches the event loop at all, so this
    asserts the loop is unwrapped before **and** after a full negotiation, and that the
    negotiation still produced candidates."""
    loop = asyncio.get_running_loop()
    marker = "_voxvoice_bounded_udp"
    assert not getattr(loop, marker, False), "this loop was already wrapped before the test"

    exchange = LoopbackExchange()
    endpoint = AiortcMediaEndpoint(PeerSignaller(exchange, negotiation_timeout_s=20.0))
    assert endpoint._ice_policy.bounds_media_ports is False  # noqa: SLF001
    session = "unbounded"
    try:
        await asyncio.wait_for(endpoint.connect(session), CONNECT_DEADLINE_S)
        local = endpoint._connections[session]  # noqa: SLF001
        assert host_candidate_ports(local.localDescription.sdp), "no host candidate advertised"
        assert not getattr(loop, marker, False), (
            "an unconfigured policy installed the bounded allocator"
        )
    finally:
        await endpoint.close(session)
        await exchange.aclose()


# ══════════════════════════════════════════════════════════════════════════════
# D. uvloop — the loop the deployed service actually runs
# ══════════════════════════════════════════════════════════════════════════════
#
# `uvicorn[standard]` installs uvloop (pinned in `uv.lock`), and the Stage-2 runbook's unit runs
# uvicorn — so production's event loop is **not** the one pytest-asyncio provides. uvloop re-wraps
# a bind failure as `OSError("could not bind to local_addr …")` with `errno` of `None` and the
# real error on `__cause__`, which silently defeated a predicate that tested `exc.errno` alone.
# The allocator then aborted on the first collision instead of advancing, so the range was never
# honoured in the only configuration that matters, while every stdlib-loop test still passed.
#
# These tests are synchronous on purpose: they drive their own uvloop loop, so they are isolated
# from pytest-asyncio's and from each other. Each closes its loop in `finally`.


def _run_on_uvloop(coro_factory):
    """Run one coroutine on a dedicated uvloop loop, closing it whether or not the test passes."""
    loop = uvloop.new_event_loop()
    try:
        return loop.run_until_complete(coro_factory())
    finally:
        loop.close()


def test_uvloop_wraps_address_in_use_with_no_errno():
    """The root cause, pinned as a fact about the dependency rather than an assumption."""

    async def body():
        loop = asyncio.get_running_loop()
        held, _ = await loop.create_datagram_endpoint(
            asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0)
        )
        try:
            port = held.get_extra_info("socket").getsockname()[1]
            with pytest.raises(OSError) as caught:
                await loop.create_datagram_endpoint(
                    asyncio.DatagramProtocol, local_addr=("127.0.0.1", port)
                )
            return caught.value.errno, getattr(caught.value.__cause__, "errno", None)
        finally:
            held.close()

    outer_errno, cause_errno = _run_on_uvloop(body)
    assert outer_errno is None, (
        "uvloop no longer hides the errno; the predicate's cause check may be removable"
    )
    assert cause_errno == errno.EADDRINUSE


def test_uvloop_retries_a_collision_and_succeeds_on_another_port(monkeypatch):
    """A held port inside the range must be stepped over, not fatal.

    The scan start is pinned to `lo` so the collision is guaranteed rather than probabilistic:
    with `lo` held and a width of two, the allocator must hit the collision first and then land
    on `lo + 1`."""
    monkeypatch.setattr("providers.transport.udp_ports.random.randrange", lambda _n: 0)
    lo, hi = BASE + 300, BASE + 302

    async def body():
        loop = asyncio.get_running_loop()
        install(loop)
        held, _ = await loop.create_datagram_endpoint(
            asyncio.DatagramProtocol, local_addr=("127.0.0.1", lo)
        )
        allocated = None
        try:
            async with bounded_ports(lo, hi) as scope:
                allocated, _ = await loop.create_datagram_endpoint(
                    asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0)
                )
                port = allocated.get_extra_info("socket").getsockname()[1]
                return port, scope.bound, scope.exhausted, scope.placed
        finally:
            if allocated is not None:
                allocated.close()
            held.close()

    port, bound, exhausted, placed = _run_on_uvloop(body)
    assert port == lo + 1, f"the collision on {lo} was not retried (got {port})"
    assert bound == 1 and exhausted == 0
    assert placed == (("127.0.0.1", lo + 1),)


def test_uvloop_exhaustion_fails_closed_without_binding_outside_the_range():
    lo, hi = BASE + 310, BASE + 311  # exactly one port, and it is taken

    async def body():
        loop = asyncio.get_running_loop()
        install(loop)
        held, _ = await loop.create_datagram_endpoint(
            asyncio.DatagramProtocol, local_addr=("127.0.0.1", lo)
        )
        try:
            async with bounded_ports(lo, hi) as scope:
                with pytest.raises(BoundedPortRangeExhausted) as caught:
                    await loop.create_datagram_endpoint(
                        asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0)
                    )
                return (
                    caught.value.errno,
                    isinstance(caught.value.__cause__, OSError),
                    scope.bound,
                    scope.exhausted,
                    scope.exhausted_addresses,
                    scope.placed,
                )
        finally:
            held.close()

    exc_errno, chained, bound, exhausted, addresses, placed = _run_on_uvloop(body)
    assert exc_errno == errno.EADDRINUSE
    assert chained, "the last collision was not chained; the kernel's reason is lost"
    assert bound == 0 and placed == (), "a socket was bound despite the range being full"
    assert exhausted == 1 and addresses == ("127.0.0.1",)


def test_uvloop_does_not_retry_a_non_address_in_use_error():
    """`EADDRNOTAVAIL` is a fault, not "busy" — it must propagate, not become exhaustion."""

    async def body():
        loop = asyncio.get_running_loop()
        install(loop)
        async with bounded_ports(BASE + 320, BASE + 330) as scope:
            with pytest.raises(OSError) as caught:
                # An address this host does not own.
                await loop.create_datagram_endpoint(
                    asyncio.DatagramProtocol, local_addr=("203.0.113.254", 0)
                )
            return (
                isinstance(caught.value, BoundedPortRangeExhausted),
                getattr(caught.value.__cause__, "errno", caught.value.errno),
                scope.bound,
                scope.exhausted,
            )

    was_exhaustion, underlying, bound, exhausted = _run_on_uvloop(body)
    assert not was_exhaustion, "a non-EADDRINUSE fault was retried to exhaustion"
    assert underlying != errno.EADDRINUSE
    assert bound == 0 and exhausted == 0
