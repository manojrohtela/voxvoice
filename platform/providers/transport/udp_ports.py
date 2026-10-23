"""Bounded UDP port allocation for the media path (M13.2).

## Why this exists

A deployment on a **shared** host cannot open the operating system's whole ephemeral range to
UDP just so WebRTC media can flow. The Stage-2 runbook's answer was to narrow
`net.ipv4.ip_local_port_range`, but that setting is **host-wide**: on a host running other
services it re-pools every one of their ephemeral ports. So the bound has to come from inside
this process instead.

Neither `aiortc` (1.15.0) nor `aioice` (0.10.2) offers one. `aioice` allocates each media socket
with `loop.create_datagram_endpoint(..., local_addr=(address, 0))` — port `0`, meaning *"operating
system, pick one"* — and exposes no port-range parameter anywhere. This module therefore wraps
that one asyncio call and supplies an explicit port drawn from a configured range.

## What it does and does not promise

It is **not** a reservation system. There is no table of held ports, because the `bind()` itself
is the reservation and the kernel arbitrates it atomically: two concurrent negotiations cannot
both acquire the same `(address, port)`, and `aioice` never asks for `SO_REUSEPORT`. The
consequence is that the allocator holds no state that could leak — though the **sockets** it
returns are owned by `aioice` thereafter, and their cleanup is `aioice`'s (see
`AiortcMediaEndpoint.connect`'s post-condition, and the cancellation limitation recorded there).

## The dependency, stated plainly

This module imports no third-party library. Its one dependency on `aioice` is **behavioural**:
that media sockets are allocated through the running loop's `create_datagram_endpoint` with a
port of `0`. If a future version pre-binds a socket and passes `sock=`, or binds some other way,
this wrapper is simply never consulted — ports would silently return to the host ephemeral range.
Nothing here can detect that; `verify_host_candidate_ports` exists so the **caller** can, and a
regression test pins the invocation count so a dependency upgrade cannot pass quietly.

## A limitation this module cannot fix, and makes sharper

`aioice` registers the sockets it created only **after** its per-address bind loop
(`aioice/ice.py:1009`), and `Connection.close()` closes only what is registered (`:635-637`). So
an exception that is not an `OSError` — `CancelledError` being the realistic one — escapes that
loop and leaves every socket bound so far unregistered and **unclosed**. This module is built so
as not to add such a path: exhaustion raises an `OSError`, which the library catches. It does not
and cannot repair the library's own behaviour, and **no cleanup guarantee is claimed**.

A bounded range makes the consequence worse, not better. Unbounded, an orphan consumes one of
tens of thousands of ephemeral ports. Bounded, it permanently consumes one of a handful until the
process restarts, so repeated cancellations can exhaust the range and refuse every later call.
Size the range with headroom, and treat process restart as the recovery.

## Scope

Installed on the event loop once, and **inert** unless a `bounded_ports` scope is active in the
current context. A `ContextVar` is what makes that precise: `asyncio.gather`, which `aioice`'s
`gather_candidates` uses, copies the current context into each task it creates, so a scope opened
around negotiation reaches the bind site while leaving every unrelated UDP socket in the process
untouched.

**Ranges are half-open — `[lo, hi)` — everywhere: parser, allocator, tests and documentation.**
"""

from __future__ import annotations

import asyncio
import errno
import logging
import random
import re
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("voxvoice.providers.transport.udp_ports")

#: A `typ host` candidate line, captured for its port. Anchored on the serialisation `aiortc`
#: actually emits — `{foundation} {component} {protocol} {priority} {ip} {port} typ {type}` —
#: and deliberately **only** `host`: a `srflx` candidate carries the NAT-mapped port, which
#: differs from the locally bound one behind symmetric NAT, and a `relay` candidate carries the
#: TURN server's. Checking either against a local range would fail a working deployment.
_HOST_CANDIDATE = re.compile(
    r"^a=candidate:\S+\s+\d+\s+\S+\s+\d+\s+\S+\s+(?P<port>\d+)\s+typ\s+host\b",
    re.MULTILINE | re.IGNORECASE,
)


def _is_address_in_use(exc: OSError) -> bool:
    """Whether this bind failure is demonstrably "port already taken".

    Two spellings, because the event loop decides which one the caller sees:

    * the **stdlib** selector loop raises the `OSError` from the socket layer unchanged, so
      `exc.errno` is `EADDRINUSE`;
    * **uvloop** — which `uvicorn[standard]` installs, and therefore what the deployed service
      runs — re-wraps it as `OSError("could not bind to local_addr …")` with `errno` of **`None`**
      and the real error on `__cause__`.

    Checking only `exc.errno` was a defect: on uvloop it is `None`, the predicate was false, and
    the allocator aborted on the first collision instead of advancing — so the range was never
    honoured in the one configuration that matters. Found by auditing against the pinned uvloop.

    The cause is inspected **one level** and only when it is itself an `OSError` carrying
    `EADDRINUSE`. `EACCES`, `EADDRNOTAVAIL`, `EAFNOSUPPORT` and anything else are real faults and
    must not be retried merely because an exception happens to have a cause."""
    if exc.errno == errno.EADDRINUSE:
        return True
    cause = exc.__cause__
    return isinstance(cause, OSError) and cause.errno == errno.EADDRINUSE


class BoundedPortRangeExhausted(OSError):
    """No port in the configured range was free for one local address.

    **An `OSError` on purpose, and the choice matters.** `aioice` wraps its bind in
    `except OSError: ... continue` and only registers the sockets it has already created *after*
    that loop finishes (`aioice/ice.py:1009`). An exception that escapes the loop would therefore
    orphan every socket bound so far — they would never reach `Connection._protocols` and never be
    closed. Raising `OSError` keeps `aioice`'s own per-address handling in charge, so every
    successful bind stays under its normal cleanup ownership, and exhaustion is reported instead
    through `PortScope.exhausted`, which the caller checks once negotiation returns."""


@dataclass
class PortScope:
    """One negotiation's allocation record. Half-open `[lo, hi)`."""

    lo: int
    hi: int
    #: Sockets this allocator placed.
    bound: int = 0
    #: The `(address, port)` pairs it placed them on. The post-condition checks that every
    #: advertised host candidate is **one of these**, which is what makes a bypass detectable:
    #: a socket the allocator never placed cannot appear here, whatever else binds in this scope.
    placed: tuple[tuple[str, int], ...] = ()
    #: Local addresses for which the range had nothing free. Non-zero means the bound was not
    #: honoured, and the caller must refuse the call rather than proceed on a partial candidate set.
    exhausted: int = 0
    #: The addresses that exhausted, for the operator-facing message. Addresses, never ports.
    exhausted_addresses: tuple[str, ...] = ()

    @property
    def width(self) -> int:
        return self.hi - self.lo


_SCOPE: ContextVar[PortScope | None] = ContextVar("voxvoice_media_port_scope", default=None)

#: Marks a loop whose factory is already wrapped, so installation is idempotent and a loop reused
#: across sessions is not wrapped twice.
_INSTALLED = "_voxvoice_bounded_udp"


def install(loop: asyncio.AbstractEventLoop) -> None:
    """Wrap this loop's datagram factory. Idempotent, and a no-op outside a `bounded_ports` scope.

    The wrapper mirrors `create_datagram_endpoint`'s real signature — `local_addr` and
    `remote_addr` are positional-or-keyword, everything else keyword-only — so a caller passing
    them positionally is forwarded faithfully rather than raising `TypeError`."""
    if getattr(loop, _INSTALLED, False):
        return
    original = loop.create_datagram_endpoint

    async def bounded(
        protocol_factory: Any,
        local_addr: Any = None,
        remote_addr: Any = None,
        **kwargs: Any,
    ) -> Any:
        scope = _SCOPE.get()
        if scope is None or local_addr is None or local_addr[1] != 0:
            # Not ours: no scope, no bind, or a port the caller already chose.
            return await original(protocol_factory, local_addr, remote_addr, **kwargs)

        address = local_addr[0]
        # A random start spreads concurrent negotiations across the range instead of making every
        # one of them collide on `lo` first. The bound is unchanged; only the contention is.
        start = random.randrange(scope.width)
        last_in_use: OSError | None = None
        for offset in range(scope.width):
            port = scope.lo + (start + offset) % scope.width
            try:
                result = await original(
                    protocol_factory, (address, port), remote_addr, **kwargs
                )
            except OSError as exc:
                if _is_address_in_use(exc):
                    last_in_use = exc
                    continue  # taken; try the next port
                raise  # EACCES, EADDRNOTAVAIL, … are real faults, not "busy"
            scope.bound += 1
            scope.placed = (*scope.placed, (address, port))
            return result

        scope.exhausted += 1
        scope.exhausted_addresses = (*scope.exhausted_addresses, address)
        log.warning(
            "media UDP range [%d,%d) had no free port for %s; "
            "aioice will skip this address and the call will be refused",
            scope.lo,
            scope.hi,
            address,
        )
        # Chained to the last collision so the traceback still shows what the kernel said,
        # which is the difference between "the range is full" and "something else went wrong".
        raise BoundedPortRangeExhausted(
            errno.EADDRINUSE,
            f"no free UDP port in [{scope.lo},{scope.hi}) for {address}",
        ) from last_in_use

    loop.create_datagram_endpoint = bounded  # type: ignore[method-assign]
    setattr(loop, _INSTALLED, True)


@asynccontextmanager
async def bounded_ports(lo: int, hi: int):
    """Confine port-0 datagram binds made in this context to `[lo, hi)`.

    Yields the scope so the caller can check what was allocated once negotiation finishes."""
    scope = PortScope(lo=lo, hi=hi)
    token = _SCOPE.set(scope)
    try:
        yield scope
    finally:
        _SCOPE.reset(token)


def host_candidate_ports(sdp: str) -> tuple[int, ...]:
    """The ports of every `typ host` candidate in a local description."""
    return tuple(int(m.group("port")) for m in _HOST_CANDIDATE.finditer(sdp or ""))


def verify_host_candidate_ports(scope: PortScope, sdp: str) -> str | None:
    """Why the range was not honoured, or `None` if it was.

    Four distinct failures, reported distinctly because they mean different things:

    * **exhaustion** — `scope.exhausted` is non-zero, so the range was too small for this host's
      addresses and `aioice` skipped at least one of them;
    * **no host candidate at all** — nothing was advertised, so media cannot flow whatever else
      is true;
    * **an out-of-range port** — a port was advertised that the range does not contain;
    * **a port the allocator did not place** — checked as a **subset**, not a count: every
      advertised host port must appear in `scope.placed`. Equality would assume nothing else
      binds inside the scope, which is false whenever the `SdpExchange` itself opens a socket (an
      in-process peer does exactly that). The subset form also catches a *partial* bypass, which
      a count cannot.

    ## Limitations, stated

    * **A detector, not a preventer.** By the time it runs the sockets exist; what it buys is that
      the call is refused rather than proceeding on ports the deployment never opened.
    * **Ports are matched, addresses are not.** `scope.placed` records `(address, port)` but only
      the port is compared, so a candidate advertising port *P* on address *A* is accepted when
      the allocator placed *P* on address *B*. Address matching is not generally possible:
      `aioice` can replace a candidate's address with an mDNS `.local` name, which no local
      address will equal.
    * **Only the local description is inspected**, and only once negotiation has returned. A
      socket bound and discarded before serialisation is invisible here."""
    if scope.exhausted:
        addresses = ", ".join(scope.exhausted_addresses)
        return (
            f"range [{scope.lo},{scope.hi}) exhausted for {scope.exhausted} address(es)"
            f" ({addresses}); widen VOXVOICE_MEDIA_UDP_PORT_RANGE"
        )
    ports = host_candidate_ports(sdp)
    if not ports:
        return (
            f"the local description advertises no host candidate, yet the allocator placed "
            f"{scope.bound} socket(s); media cannot flow"
        )
    stray = sorted(p for p in ports if not scope.lo <= p < scope.hi)
    if stray:
        return f"host candidate port(s) {stray} fall outside [{scope.lo},{scope.hi})"
    # Subset, not equality. Equality would assume nothing else binds inside this scope, which is
    # false whenever the `SdpExchange` itself opens a socket — an in-process peer does exactly
    # that. A port the allocator never placed is the thing that matters, and it is caught here
    # whether the bypass is total or partial.
    placed = {port for _address, port in scope.placed}
    unplaced = sorted(set(ports) - placed)
    if unplaced:
        return (
            f"host candidate port(s) {unplaced} were not placed by the allocator; the media "
            "library's bind path may have changed, so the port range is not being applied"
        )
    return None


__all__ = [
    "BoundedPortRangeExhausted",
    "PortScope",
    "bounded_ports",
    "host_candidate_ports",
    "install",
    "verify_host_candidate_ports",
]
