"""First-party WebRTC signalling for `AiortcMediaEndpoint` (M11.16.3).

M11.5 shipped the endpoint with an **injected** signaller and deliberately no implementation
(R-34): *how* an offer/answer exchange reaches a peer is a deployment concern, and inventing a
signalling server inside the transport milestone would have been scope creep. M11.16.2 then
established that the absence is what makes `E11-15` unreachable — there is no way to obtain a real
peer at all.

This module supplies the missing piece, and nothing more.

```
PeerSignaller.negotiate(session_id, pc)
        │  1. create offer from the pc (the endpoint already added its outbound track)
        │  2. set local description
        ▼
   SdpExchange.exchange(session_id, offer_sdp) ──► the deployment's own channel (HTTP, WS, …)
        │                                              ◄── the remote answer
        │  3. set remote description
        │  4. wait for the inbound audio track
        ▼
    the inbound MediaStreamTrack
```

## Two seams, deliberately separated

`SdpExchange` is **the deployment's**: it moves one SDP string out and brings one back, over
whatever channel the deployment already has. `PeerSignaller` is **first-party and complete**: it
owns the aiortc negotiation sequence and the state machine around it, and it is what makes the
exchange the only thing a deployment must write.

Splitting them is what keeps this small. A signalling *server* — rooms, membership, auth, TURN,
an SFU — is explicitly not built here (§6): none of it is required to obtain one peer connection
for one session, and every piece of it would be architecture this milestone has no authority to
add.

## What this module must never own

STT, VAD, endpointing, the LLM, TTS, playback, conversation state, session lifecycle or billing.
It establishes a peer connection and returns a track. A test asserts the import surface stays that
narrow.

## Security posture (§7)

Signalling is control-plane infrastructure and carries **no provider credential** — not in the
SDP, not in a message, not in a log line. Inputs are validated before they reach the library:
type, size and state are all checked, because an SDP string arrives from outside the process and
`setRemoteDescription` is not the place to discover it was a dict. Full SDP is never logged; it
carries host candidates and network topology that nothing here needs to emit.
"""

from __future__ import annotations

import asyncio
import logging
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from .media_endpoint import MediaEndpointError

log = logging.getLogger("voxvoice.providers.transport.signalling")

#: The largest SDP this will accept, in bytes. An offer/answer for one audio session is a few
#: kilobytes; 256 KB is far above any legitimate value and far below anything that could exhaust
#: memory. A bound is required because the payload originates outside the process (§7).
MAX_SDP_BYTES = 256 * 1024

#: How long the whole negotiation may take: local description, remote exchange, and the inbound
#: track arriving. Chosen to match `ProviderGuard`'s `DEFAULT_CALL_TIMEOUT_S` (10 s) — the
#: repository's existing convention for "one external call", which this is — rather than inventing
#: a new number (§17).
DEFAULT_NEGOTIATION_TIMEOUT_S = 10.0


class SignallingState(StrEnum):
    """Transport-negotiation lifecycle only.

    Deliberately **not** the conversation state machine and not the transport's AR-6 facts: this
    describes one offer/answer exchange and ends where the media path begins (§18)."""

    NEW = "new"
    NEGOTIATING = "negotiating"
    CONNECTED = "connected"
    CLOSED = "closed"
    FAILED = "failed"


#: The transitions this machine admits. Anything else is rejected rather than tolerated, so a
#: double-negotiate or a post-close negotiate is a deterministic error instead of a race.
_ALLOWED: dict[SignallingState, frozenset[SignallingState]] = {
    SignallingState.NEW: frozenset({SignallingState.NEGOTIATING, SignallingState.CLOSED}),
    SignallingState.NEGOTIATING: frozenset(
        {SignallingState.CONNECTED, SignallingState.FAILED, SignallingState.CLOSED}
    ),
    SignallingState.CONNECTED: frozenset({SignallingState.CLOSED, SignallingState.FAILED}),
    SignallingState.FAILED: frozenset({SignallingState.CLOSED}),
    SignallingState.CLOSED: frozenset(),
}


@runtime_checkable
class SdpExchange(Protocol):
    """The deployment's channel for one offer/answer round trip.

    One method, because one method is all a peer connection needs. An HTTP handler, a WebSocket
    client or a test double all satisfy it, and none of them has to know aiortc exists."""

    async def exchange(self, session_id: str, offer_sdp: str) -> str:
        """Deliver the local offer to the remote peer and return its answer SDP."""


@runtime_checkable
class Signaller(Protocol):
    """What `AiortcMediaEndpoint` requires. Stated so the endpoint's injected dependency has a
    name rather than being `Any`."""

    async def negotiate(self, session_id: str, pc: Any) -> Any:
        """Negotiate `pc` and return the inbound audio track, or `None` if the peer sent none."""


def _validate_sdp(value: object, *, what: str) -> str:
    """Refuse anything that is not a plausible SDP before the library sees it (§7)."""
    if not isinstance(value, str):
        raise MediaEndpointError(f"{what} is {type(value).__name__}, expected an SDP string")
    if not value.strip():
        raise MediaEndpointError(f"{what} is empty")
    if len(value.encode("utf-8")) > MAX_SDP_BYTES:
        raise MediaEndpointError(f"{what} exceeds {MAX_SDP_BYTES} bytes")
    # An SDP begins with a version line. Checking it costs nothing and turns "the remote sent us
    # an HTML error page" into a clear message instead of a library stack trace.
    if not value.lstrip().startswith("v="):
        raise MediaEndpointError(f"{what} does not begin with an SDP version line")
    return value


class PeerSignaller:
    """Drives one offer/answer negotiation per session, over an injected `SdpExchange`.

    **The local side offers.** The endpoint has already added its outbound track to the peer
    connection by the time `negotiate` runs, so the offer advertises the audio this runtime will
    send and the exchange is a single round trip the caller drives — which is what lets
    `MediaEndpoint.connect()` stay a plain `await` that either yields a usable connection or
    raises (the contract AR-6 depends on).
    """

    __slots__ = ("_exchange", "_timeout_s", "_states")

    def __init__(
        self,
        exchange: SdpExchange,
        *,
        negotiation_timeout_s: float = DEFAULT_NEGOTIATION_TIMEOUT_S,
    ) -> None:
        if negotiation_timeout_s <= 0:
            raise ValueError(
                f"negotiation_timeout_s must be positive, got {negotiation_timeout_s}"
            )
        self._exchange = exchange
        self._timeout_s = negotiation_timeout_s
        self._states: dict[str, SignallingState] = {}

    # ── state ────────────────────────────────────────────────────────────────

    def state(self, session_id: str) -> SignallingState:
        return self._states.get(session_id, SignallingState.NEW)

    def _move(self, session_id: str, target: SignallingState) -> None:
        current = self.state(session_id)
        if target not in _ALLOWED[current]:
            raise MediaEndpointError(
                f"signalling for {session_id}: illegal transition "
                f"{current.value} -> {target.value}"
            )
        self._states[session_id] = target

    def release(self, session_id: str) -> None:
        """Forget a session. Idempotent; called by the endpoint on teardown."""
        current = self._states.get(session_id)
        if current is not None and current is not SignallingState.CLOSED:
            self._states[session_id] = SignallingState.CLOSED
        self._states.pop(session_id, None)

    # ── negotiation ──────────────────────────────────────────────────────────

    async def negotiate(self, session_id: str, pc: Any) -> Any:
        """Offer, exchange, answer, and return the inbound audio track.

        Bounded end to end: a remote that never answers, or answers without sending audio, fails
        within `negotiation_timeout_s` rather than holding a session open forever (§17)."""
        self._move(session_id, SignallingState.NEGOTIATING)
        try:
            track = await asyncio.wait_for(self._negotiate(session_id, pc), self._timeout_s)
        except TimeoutError as exc:
            self._states[session_id] = SignallingState.FAILED
            raise MediaEndpointError(
                f"signalling for {session_id}: negotiation exceeded {self._timeout_s}s"
            ) from exc
        except asyncio.CancelledError:
            # Withdrawal, not failure — the same rule the provider guard applies (ADR-0046).
            self._states[session_id] = SignallingState.CLOSED
            raise
        except MediaEndpointError:
            self._states[session_id] = SignallingState.FAILED
            raise
        except Exception as exc:  # noqa: BLE001 — a library error must not cross this boundary
            self._states[session_id] = SignallingState.FAILED
            raise MediaEndpointError(f"signalling for {session_id}: {exc}") from exc
        self._states[session_id] = SignallingState.CONNECTED
        return track

    async def _negotiate(self, session_id: str, pc: Any) -> Any:
        from aiortc import RTCSessionDescription

        inbound: asyncio.Future = asyncio.get_running_loop().create_future()

        @pc.on("track")
        def _on_track(track: Any) -> None:  # pragma: no cover — driven by the library
            if track.kind == "audio" and not inbound.done():
                inbound.set_result(track)

        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        # `pc.localDescription` rather than `offer`: aiortc fills in ICE candidates during
        # `setLocalDescription`, so the object handed to the remote must be read back afterwards.
        local_sdp = _validate_sdp(pc.localDescription.sdp, what="local offer")
        log.info(
            "signalling %s: offering (%d bytes)", session_id, len(local_sdp.encode("utf-8"))
        )

        answer_sdp = _validate_sdp(
            await self._exchange.exchange(session_id, local_sdp), what="remote answer"
        )
        await pc.setRemoteDescription(RTCSessionDescription(sdp=answer_sdp, type="answer"))
        log.info("signalling %s: answered; awaiting inbound audio track", session_id)

        # The remote's track arrives through the callback above. Awaiting it here is what makes
        # `connect()` return only once the connection is genuinely usable.
        return await inbound


__all__ = [
    "DEFAULT_NEGOTIATION_TIMEOUT_S",
    "MAX_SDP_BYTES",
    "PeerSignaller",
    "SdpExchange",
    "Signaller",
    "SignallingState",
]
