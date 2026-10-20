"""Releasing the call slot wherever a session ends (M13.1).

## Why acquisition and release live in different layers

Capacity is taken in the **router**, before `create_session` is reached, and given back in a
**`SessionController` decorator**. That asymmetry is deliberate, and it is forced by the frozen
handler between them:

`api/routers/sessions.py` writes the `Session` row **before** calling `controller.start`, and wraps
that call in `except Exception` → `502 "Runtime failed to start the session"`. A guard that refused
inside the controller chain would therefore (a) have its `409` laundered into a generic `502`, and
(b) leave a `status="failed"` session row behind for every caller it turned away. So the decision
has to be made above that handler — and `RendezvousRegistry.check_bindable` already establishes the
pattern, for the same reason and in the same place.

Release, conversely, belongs here: `start` and `end` are the two points every teardown path
converges on, exactly as `RendezvousBindingController` already uses them for the rendezvous.

## What it adds

Nothing but the release. No capability, no retry, no state of its own, no error of its own. A
failed start releases the slot and re-raises **unchanged**, so the existing failure mapping is
still what the caller sees.

`except BaseException` rather than `except Exception`, because `asyncio.CancelledError` is a
`BaseException` and a cancelled session start is the one failure path that would otherwise leak the
slot — the frozen handler does not catch it either, so it arrives here on its way out.
"""

from __future__ import annotations

from core.session_control import SessionController, StartSessionRequest, StartSessionResult

from .binding import PENDING_RENDEZVOUS
from .call_slot import CallSlot


class SingleCallController(SessionController):
    """Binds the session to the capacity its rendezvous took, and releases it on every exit."""

    def __init__(self, inner: SessionController, slot: CallSlot) -> None:
        self._inner = inner
        self._slot = slot

    async def start(self, request: StartSessionRequest) -> StartSessionResult:
        pending = PENDING_RENDEZVOUS.get()
        key = pending[0] if pending is not None else None
        if key is not None:
            # The id the router could not know: capacity was taken under the rendezvous id,
            # because the session id is minted inside the frozen handler further down this call.
            self._slot.bind(key, request.session_id)
        try:
            return await self._inner.start(request)
        except BaseException:
            self._slot.release(key=key, session_id=request.session_id)
            raise

    async def end(self, session_id: str) -> None:
        try:
            await self._inner.end(session_id)
        finally:
            self._slot.release(session_id=session_id)

    def conversation_state(self, session_id: str) -> str | None:
        return self._inner.conversation_state(session_id)


__all__ = ["SingleCallController"]
