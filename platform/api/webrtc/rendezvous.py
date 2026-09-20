"""A bounded in-process rendezvous between an external WebRTC peer and one VoxVoice session.

## Why a rendezvous at all

`PeerSignaller` makes **the runtime offer**: the endpoint attaches its outbound track and then
asks the deployment's `SdpExchange` to carry that offer to the peer and bring an answer back
(M11.16.3). Over HTTP the external peer is a *client* — nothing can push an offer to it — so the
offer has to wait somewhere the peer can come and fetch it. That somewhere is this module, and it
is the whole of it:

```
   browser                         Control Plane                     Runtime
      │  POST /v1/webrtc/rendezvous      │                              │
      │ ───────────────────────────────► │  reserve()                   │
      │ ◄── rendezvous_id + token ────── │                              │
      │                                  │                              │
      │  POST /v1/sessions               │  bind(session_id)            │
      │ ═══════════════════════════════► │ ───────────────────────────► │  connect()
      │            (in flight)           │  ◄── exchange(offer) ─────── │
      │  GET  …/offer  ◄── the offer ─── │                              │
      │  POST …/answer ───────────────►  │  ─── the answer ───────────► │
      │ ◄══ 201 session_id ════════════  │                              │  media
```

The browser's two signalling calls run **concurrently with** its session-create call. That is the
only thing about the shape that needs explaining, and it is forced: the create call cannot return
until the peer connection is usable, because `MediaEndpoint.connect()` returning means exactly
that (AR-6) and a half-open connection reported as established is the fact that contract exists to
forbid.

## What is bounded, and why each bound exists

Every one of these is here because the peer is outside the process (§6/§7):

* `MAX_OPEN_RENDEZVOUS` — a caller cannot reserve slots without limit.
* `RENDEZVOUS_TTL_S` — a reserved-but-never-used slot expires rather than accumulating.
* `MAX_SIGNALLING_SDP_BYTES` — an answer larger than any real SDP is refused before it is held.
* `DEFAULT_ANSWER_TIMEOUT_S` / the offer long-poll bound — no wait here is unbounded.

## Security posture

A slot is **org-scoped at reservation** and **token-scoped afterwards**, so signalling never rides
the org API key: the browser receives a single-purpose token that can do nothing but exchange one
SDP pair for one session. The token is returned once and stored only as a SHA-256 hash, the same
rule `api.security` applies to API keys, and compared in constant time.

No provider credential is reachable from anything in this module, and full SDP is never logged —
only its byte length, the state, and the rendezvous id.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass, field
from enum import StrEnum

log = logging.getLogger("voxvoice.api.webrtc.rendezvous")

#: The largest SDP the signalling endpoints accept, in bytes.
#:
#: Deliberately the same number as `providers.transport.signalling.MAX_SDP_BYTES`, stated here
#: rather than imported: the Control Plane does not import the provider plane, and a test pins the
#: two together so they cannot drift apart silently.
MAX_SIGNALLING_SDP_BYTES = 256 * 1024

#: How long a reserved rendezvous lives before it is reclaimed. Long enough for a caller to be
#: granted microphone permission and start its session; far too short to accumulate.
RENDEZVOUS_TTL_S = 120.0

#: How many rendezvous slots may be open at once, across all orgs. A reservation past this limit
#: is refused **after** expired slots are reclaimed, so a steady legitimate rate never trips it.
MAX_OPEN_RENDEZVOUS = 64

#: How long `exchange()` waits for the peer's answer. Inside `PeerSignaller`'s own 10 s
#: negotiation bound, so the exchange gives up first and the failure names the peer rather than
#: the negotiation.
DEFAULT_ANSWER_TIMEOUT_S = 8.0

#: How long a single offer long-poll may hold a request open before answering "not yet" (204).
#: Bounded so a client sees a response on a predictable schedule and no request hangs.
DEFAULT_OFFER_POLL_S = 5.0

_TOKEN_BYTES = 32


class RendezvousState(StrEnum):
    """One rendezvous's lifecycle. Signalling only — it says nothing about the conversation."""

    RESERVED = "reserved"
    BOUND = "bound"
    OFFERED = "offered"
    ANSWERED = "answered"
    CLOSED = "closed"
    FAILED = "failed"


class RendezvousError(Exception):
    """A signalling request could not be honoured. Carries an HTTP status for the router.

    The message is written to be safe to return to an external caller: it names states and limits,
    never contents."""

    def __init__(self, detail: str, *, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass
class _Slot:
    rendezvous_id: str
    org_id: str
    token_hash: str
    created_at: float
    state: RendezvousState = RendezvousState.RESERVED
    session_id: str | None = None
    offer_sdp: str | None = None
    answer_sdp: str | None = None
    offer_ready: asyncio.Event = field(default_factory=asyncio.Event)
    answer_ready: asyncio.Event = field(default_factory=asyncio.Event)

    def expired(self, now: float) -> bool:
        return now - self.created_at > RENDEZVOUS_TTL_S


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_sdp(value: object, *, what: str) -> str:
    """Refuse anything that is not a plausible SDP before it is stored or forwarded.

    The same three questions `providers.transport.signalling._validate_sdp` asks, asked one layer
    earlier: a malformed payload should be rejected at the edge that received it, not discovered
    by `setRemoteDescription` two planes away."""
    if not isinstance(value, str):
        raise RendezvousError(f"{what} is {type(value).__name__}, expected an SDP string")
    if not value.strip():
        raise RendezvousError(f"{what} is empty")
    if len(value.encode("utf-8")) > MAX_SIGNALLING_SDP_BYTES:
        raise RendezvousError(
            f"{what} exceeds {MAX_SIGNALLING_SDP_BYTES} bytes", status_code=413
        )
    if not value.lstrip().startswith("v="):
        raise RendezvousError(f"{what} does not begin with an SDP version line")
    return value


class RendezvousRegistry:
    """Holds the open rendezvous slots and implements `SdpExchange` for the runtime.

    Structurally a `providers.transport.signalling.SdpExchange` — it has the one method the
    Protocol requires — without importing it, so the Control Plane keeps no static dependency on
    the provider plane. A test asserts the Protocol is satisfied.
    """

    def __init__(
        self,
        *,
        answer_timeout_s: float = DEFAULT_ANSWER_TIMEOUT_S,
        max_open: int = MAX_OPEN_RENDEZVOUS,
    ) -> None:
        if answer_timeout_s <= 0:
            raise ValueError(f"answer_timeout_s must be positive, got {answer_timeout_s}")
        if max_open <= 0:
            raise ValueError(f"max_open must be positive, got {max_open}")
        self._answer_timeout_s = answer_timeout_s
        self._max_open = max_open
        self._slots: dict[str, _Slot] = {}
        self._by_session: dict[str, str] = {}

    # ── reservation ──────────────────────────────────────────────────────────

    def reserve(self, org_id: str) -> tuple[str, str, float]:
        """Open a slot for `org_id`. Returns `(rendezvous_id, token, ttl_s)`.

        The token is returned **once** and never stored in the clear."""
        self._reclaim()
        if len(self._slots) >= self._max_open:
            raise RendezvousError(
                f"too many open signalling rendezvous ({self._max_open})", status_code=429
            )
        rendezvous_id = f"rdv_{secrets.token_urlsafe(16)}"
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        self._slots[rendezvous_id] = _Slot(
            rendezvous_id=rendezvous_id,
            org_id=org_id,
            token_hash=_hash_token(token),
            created_at=time.monotonic(),
        )
        log.info("rendezvous %s: reserved", rendezvous_id)
        return rendezvous_id, token, RENDEZVOUS_TTL_S

    def _reclaim(self) -> None:
        now = time.monotonic()
        for slot in [s for s in self._slots.values() if s.expired(now)]:
            log.info("rendezvous %s: expired in state %s", slot.rendezvous_id, slot.state.value)
            self._discard(slot)

    def _discard(self, slot: _Slot) -> None:
        self._slots.pop(slot.rendezvous_id, None)
        if slot.session_id is not None:
            self._by_session.pop(slot.session_id, None)
        # Wake anything parked on this slot so no waiter outlives it.
        slot.offer_ready.set()
        slot.answer_ready.set()

    # ── lookup and authentication ────────────────────────────────────────────

    def _slot(self, rendezvous_id: str) -> _Slot:
        self._reclaim()
        slot = self._slots.get(rendezvous_id)
        if slot is None:
            # 404 for both "never existed" and "expired": an external caller learns nothing about
            # other callers' identifiers from the difference.
            raise RendezvousError("no such signalling rendezvous", status_code=404)
        return slot

    def authenticate(self, rendezvous_id: str, token: str | None) -> _Slot:
        """Resolve a rendezvous from its single-purpose token, in constant time."""
        slot = self._slot(rendezvous_id)
        if not token or not secrets.compare_digest(_hash_token(token), slot.token_hash):
            raise RendezvousError("invalid signalling token", status_code=401)
        return slot

    def state(self, rendezvous_id: str) -> RendezvousState:
        return self._slot(rendezvous_id).state

    # ── binding a session (Control Plane, org-scoped) ────────────────────────

    def check_bindable(self, rendezvous_id: str, org_id: str) -> None:
        """Whether `bind` would succeed, asked **before** the session is created.

        The binding itself happens inside the session-start path, where any error would be mapped
        to a generic 502 by the existing handler. Asking first is what lets an unknown, foreign or
        already-claimed rendezvous answer with the status it actually deserves."""
        slot = self._slot(rendezvous_id)
        if slot.org_id != org_id:
            raise RendezvousError("no such signalling rendezvous", status_code=404)
        if slot.state is not RendezvousState.RESERVED:
            raise RendezvousError(
                f"rendezvous is {slot.state.value}, not reserved", status_code=409
            )

    def bind(self, rendezvous_id: str, org_id: str, session_id: str) -> None:
        """Claim a reserved rendezvous for exactly one session of exactly one org (§8)."""
        slot = self._slot(rendezvous_id)
        if slot.org_id != org_id:
            # Deliberately the same answer as "unknown": org B must not be able to probe for
            # org A's rendezvous identifiers.
            raise RendezvousError("no such signalling rendezvous", status_code=404)
        if slot.state is not RendezvousState.RESERVED:
            raise RendezvousError(
                f"rendezvous is {slot.state.value}, not reserved", status_code=409
            )
        slot.session_id = session_id
        slot.state = RendezvousState.BOUND
        self._by_session[session_id] = rendezvous_id
        log.info("rendezvous %s: bound to session %s", rendezvous_id, session_id)

    # ── the SdpExchange the runtime drives ───────────────────────────────────

    async def exchange(self, session_id: str, offer_sdp: str) -> str:
        """Publish the runtime's offer for the waiting peer and return the peer's answer.

        This is the `SdpExchange` method `PeerSignaller` calls. It never blocks forever: a peer
        that reserved a rendezvous and then walked away fails the session start within
        `answer_timeout_s` (§29)."""
        rendezvous_id = self._by_session.get(session_id)
        if rendezvous_id is None:
            raise RendezvousError(
                f"session {session_id} has no signalling rendezvous", status_code=409
            )
        slot = self._slot(rendezvous_id)
        if slot.state is not RendezvousState.BOUND:
            raise RendezvousError(
                f"rendezvous is {slot.state.value}, not bound", status_code=409
            )
        slot.offer_sdp = validate_sdp(offer_sdp, what="local offer")
        slot.state = RendezvousState.OFFERED
        slot.offer_ready.set()
        log.info(
            "rendezvous %s: offer published (%d bytes)",
            rendezvous_id,
            len(slot.offer_sdp.encode("utf-8")),
        )
        try:
            await asyncio.wait_for(slot.answer_ready.wait(), self._answer_timeout_s)
        except TimeoutError as exc:
            slot.state = RendezvousState.FAILED
            raise RendezvousError(
                f"the peer did not answer within {self._answer_timeout_s}s", status_code=504
            ) from exc
        if slot.answer_sdp is None:
            # The slot was discarded underneath us (expiry or close); the waiter was woken so it
            # would not outlive the slot, not because an answer arrived.
            slot.state = RendezvousState.FAILED
            raise RendezvousError("the signalling rendezvous was closed", status_code=409)
        log.info("rendezvous %s: answer delivered to the runtime", rendezvous_id)
        return slot.answer_sdp

    def release(self, session_id: str) -> None:
        """Forget a session's rendezvous. Idempotent; called when the session ends."""
        rendezvous_id = self._by_session.pop(session_id, None)
        if rendezvous_id is None:
            return
        slot = self._slots.get(rendezvous_id)
        if slot is not None:
            slot.state = RendezvousState.CLOSED
            self._discard(slot)

    # ── the two peer-facing operations ───────────────────────────────────────

    async def await_offer(self, rendezvous_id: str, token: str | None, *,
                          poll_s: float = DEFAULT_OFFER_POLL_S) -> str | None:
        """Wait, bounded, for the runtime's offer. `None` means "not yet — ask again"."""
        slot = self.authenticate(rendezvous_id, token)
        if slot.state in (RendezvousState.CLOSED, RendezvousState.FAILED):
            raise RendezvousError(
                f"rendezvous is {slot.state.value}", status_code=409
            )
        if slot.offer_sdp is None:
            try:
                await asyncio.wait_for(slot.offer_ready.wait(), poll_s)
            except TimeoutError:
                return None
        # Re-read through the registry: the slot may have been discarded while we waited, and the
        # waiter is woken on discard precisely so it does not hold a dead slot.
        slot = self.authenticate(rendezvous_id, token)
        return slot.offer_sdp

    def submit_answer(self, rendezvous_id: str, token: str | None, answer_sdp: object) -> None:
        """Accept the peer's answer once, for a rendezvous that is waiting for one."""
        slot = self.authenticate(rendezvous_id, token)
        if slot.state is not RendezvousState.OFFERED:
            raise RendezvousError(
                f"rendezvous is {slot.state.value}; no offer is awaiting an answer",
                status_code=409,
            )
        sdp = validate_sdp(answer_sdp, what="remote answer")
        slot.answer_sdp = sdp
        slot.state = RendezvousState.ANSWERED
        slot.answer_ready.set()
        log.info(
            "rendezvous %s: answer accepted (%d bytes)",
            rendezvous_id,
            len(sdp.encode("utf-8")),
        )

    # ── introspection (tests and diagnostics; no contents) ───────────────────

    @property
    def open_count(self) -> int:
        self._reclaim()
        return len(self._slots)


__all__ = [
    "DEFAULT_ANSWER_TIMEOUT_S",
    "DEFAULT_OFFER_POLL_S",
    "MAX_OPEN_RENDEZVOUS",
    "MAX_SIGNALLING_SDP_BYTES",
    "RENDEZVOUS_TTL_S",
    "RendezvousError",
    "RendezvousRegistry",
    "RendezvousState",
    "validate_sdp",
]
