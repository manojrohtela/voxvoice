"""Binding a signalling rendezvous to the session the existing API creates (M11.16.4).

## The ordering problem, and why this is the answer

A rendezvous must be claimed **before** the runtime starts, because the runtime's first act on the
WebRTC transport is to offer through it. But the session id the claim is keyed by does not exist
until `POST /v1/sessions` has written the row — inside a handler this milestone may not modify,
because `E10-7` freezes every pre-existing `platform/api/` file (ADR-0043 CD7).

So the claim travels the only way it can: a **context variable** set by the WebRTC session
endpoint and read by a controller wrapper, in the same task, between those two moments. The
existing handler is called **as it is**, runs **as it is**, and neither knows this happened.

That is a stronger form of reuse than adding a field would have been. The session-creation path —
agent lookup, org scoping, persistence, the 502 mapping, the `session.started` webhook — is the
one that already exists and is already tested, not a second copy of it.

## What the wrapper adds, and what it does not

It binds on `start` and releases on `end`. It adds no capability, no retry, no state machine and
no error of its own: a failed start releases the rendezvous and re-raises unchanged, so the
existing handler's own failure mapping is what the caller sees.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from core.session_control import SessionController, StartSessionRequest, StartSessionResult

#: The rendezvous a session-start is being made on behalf of, as `(rendezvous_id, org_id)`.
#: Set for the duration of one request by the WebRTC session endpoint and never elsewhere; `None`
#: means an ordinary session, which is every session the rest of the platform creates.
PENDING_RENDEZVOUS: ContextVar[tuple[str, str] | None] = ContextVar(
    "voxvoice_webrtc_pending_rendezvous", default=None
)


class RendezvousBindingController(SessionController):
    """A `SessionController` that claims a rendezvous for the session it is starting.

    Wraps the real controller and delegates everything. It is installed once, at the composition
    root, so that `POST /v1/sessions/{id}/end` releases the rendezvous through the same object —
    without the sessions router knowing a rendezvous exists."""

    def __init__(self, inner: SessionController, registry: Any) -> None:
        self._inner = inner
        self._registry = registry

    async def start(self, request: StartSessionRequest) -> StartSessionResult:
        pending = PENDING_RENDEZVOUS.get()
        if pending is not None:
            rendezvous_id, org_id = pending
            self._registry.bind(rendezvous_id, org_id, request.session_id)
        try:
            return await self._inner.start(request)
        except Exception:
            # The rendezvous dies with the session it was claimed for; a slot that outlived a
            # failed start would be claimable by nothing and expire silently.
            self._registry.release(request.session_id)
            raise

    async def end(self, session_id: str) -> None:
        try:
            await self._inner.end(session_id)
        finally:
            self._registry.release(session_id)

    def conversation_state(self, session_id: str) -> str | None:
        return self._inner.conversation_state(session_id)


__all__ = ["PENDING_RENDEZVOUS", "RendezvousBindingController"]
