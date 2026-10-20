"""An optional cap on how many calls **one process** runs at once (M13.1).

## Why this exists, and why it is not `MAX_OPEN_RENDEZVOUS`

`MAX_OPEN_RENDEZVOUS` bounds *signalling slots*, not calls: it is 64, it counts reservations
rather than conversations, and `RENDEZVOUS_TTL_S` is measured from reservation so a slot is
reclaimed 120 s after it was opened **even while its call is still running**. None of that is a
concurrency control, and a deployment on a small shared host needs one — not to save memory
(systemd's `MemoryMax` does that, and neither mechanism substitutes for the other) but to stop a
second caller from ever creating the demand.

## Disabled unless asked for

`VOXVOICE_MAX_CONCURRENT_CALLS` unset, empty or `0` means **unlimited**, which is the behaviour
every deployment had before this module existed: `try_acquire` returns immediately, nothing is
recorded, and the controller chain is not even wrapped. An explicit positive value enables the
cap. There is no default limit, because a default limit would be a capacity claim nobody measured.

## Reclamation is state-gated, never timed

A holder is released when it *ends*, not when it gets old. The signal is
`SessionController.conversation_state(session_id)`, which returns `None` once the runtime has
forgotten the session and a **terminal** state (`ended`, `failed`) once the conversation has
finished — including the finish that a caller hangup produces, because the transport's ended
inbound stream drives the orchestrator to `end(reason="completed", source="transport")`.

So an active-but-silent call is distinguishable from a finished one, and a long call is never
mistaken for a dead one. `acquired_at` is recorded for logs and is **deliberately never read as a
reclaim condition**: any elapsed-time rule would eventually evict a legitimate conversation, and a
guard that drops a live call to admit a new one is worse than the contention it prevents.

## Fail closed

Probing is reached **only when the slot is full**, so a broken probe can never block the first
call. When the slot *is* full and the probe raises, the holder is treated as **live** and the new
call is refused: staleness was not proven, and inferring "free" from "unknown" is how a capacity
limit silently stops being one. The refusal is logged with the reason so `probe_failed` is
distinguishable from ordinary contention in operation; the caller sees the same 409 either way,
and learns nothing about the holder.

## What it does not do

It is **per process**. The state below is one interpreter's memory; `--workers 2` would mean two
independent slots and two concurrent calls, which is the same constraint already recorded as
`R-E12-5` for the rendezvous registry. `process_lock.py` enforces the single-process precondition
this relies on; neither file provides cross-process coordination, and this one must never be
described as if it did.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass

from core.states import TERMINAL_SESSION_STATES

from .config import WebRTCConfigError

log = logging.getLogger("voxvoice.api.webrtc.call_slot")

#: The deployment's stated cap. Absent, empty or `0` is *unlimited* — see the module docstring.
CAPACITY_ENV = "VOXVOICE_MAX_CONCURRENT_CALLS"

#: What a refused caller is told. Identical for every refusal reason on purpose: the reason is an
#: operational fact, and an external caller that could tell "busy" from "the probe broke" would be
#: learning about the holder, which is the same reasoning `rendezvous.py` applies to its 404s.
BUSY_DETAIL = "a call is already in progress on this deployment"

#: The conversation states that mean *finished*, as the strings the seam actually carries
#: (`RuntimeSessionService.conversation_state` returns `SessionState.value`, not the enum).
TERMINAL_STATE_VALUES = frozenset(state.value for state in TERMINAL_SESSION_STATES)

#: `session_id -> conversation state`, or `None` when the runtime no longer knows the session.
StateProbe = Callable[[str], str | None]


class CallSlotBusy(Exception):
    """Capacity is held by a call that could not be shown to have ended.

    Carries the status the router should answer with, the same shape as `RendezvousError`, so the
    refusal is expressed once here rather than reinvented at each call site."""

    def __init__(self, *, reason: str) -> None:
        super().__init__(BUSY_DETAIL)
        self.detail = BUSY_DETAIL
        self.status_code = 409
        #: `holder_live` or `probe_failed`. For logs and tests; never returned to a caller.
        self.reason = reason


@dataclass
class _Held:
    """One occupied unit of capacity.

    `key` is the rendezvous id, which exists at the moment capacity is taken; `session_id` is
    attached later by `bind`, because the id is minted inside the frozen session-create handler and
    does not exist when the router must already have decided whether to admit the call."""

    key: str
    session_id: str | None
    acquired_at: float


def capacity_from_env(env: dict[str, str] | None = None) -> int | None:
    """Read the configured cap. `None` means unlimited.

    A malformed value is a **startup error**, not a silent fallback to unlimited: a deployment that
    asked for a limit and got none would be told a lie about its own configuration (§A2.6, AR-25).
    """
    source = os.environ if env is None else env
    raw = (source.get(CAPACITY_ENV) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise WebRTCConfigError(
            f"{CAPACITY_ENV} must be an integer; got {raw!r}"
        ) from exc
    if value < 0:
        raise WebRTCConfigError(f"{CAPACITY_ENV} must not be negative; got {value}")
    return value or None


class CallSlot:
    """How many calls this process will run at once, and which ones are running."""

    def __init__(self, capacity: int | None, *, probe: StateProbe) -> None:
        if capacity is not None and capacity <= 0:
            raise ValueError(
                f"capacity must be positive or None (unlimited), got {capacity!r}"
            )
        self._capacity = capacity
        self._probe = probe
        self._held: dict[str, _Held] = {}
        self._by_session: dict[str, str] = {}

    @property
    def capacity(self) -> int | None:
        return self._capacity

    @property
    def unlimited(self) -> bool:
        return self._capacity is None

    # ── taking and releasing capacity ────────────────────────────────────────

    def try_acquire(self, key: str) -> tuple[str, ...]:
        """Take one unit of capacity for `key`, or raise `CallSlotBusy`.

        Returns the session ids of holders found to have **finished**, for the caller to end. They
        are returned rather than ended here because ending is `async` and this method must contain
        no `await`: the check and the mutation below are one atomic step only because the event
        loop cannot interleave another coroutine between them, and a single `await` inside would
        reopen exactly the window this guard exists to close.
        """
        if self._capacity is None:
            return ()
        # ── no `await` from here to the end of this method ──
        if key in self._held:
            # The same rendezvous asking again: idempotent, and it must not evict anything.
            return ()
        evicted: list[_Held] = []
        probe_failed = False
        if len(self._held) >= self._capacity:
            for held in list(self._held.values()):
                stale, failed = self._finished(held)
                probe_failed = probe_failed or failed
                if stale:
                    evicted.append(held)
            for held in evicted:
                log.info(
                    "call slot: reclaimed finished holder session=%s key=%s",
                    held.session_id,
                    held.key,
                )
                self._forget(held)
        if len(self._held) >= self._capacity:
            reason = "probe_failed" if probe_failed else "holder_live"
            held_now = [
                (h.key, h.session_id, round(time.monotonic() - h.acquired_at, 1))
                for h in self._held.values()
            ]
            log.warning(
                "call slot busy: refusing key=%s reason=%s capacity=%d holders=%s",
                key,
                reason,
                self._capacity,
                held_now,
            )
            raise CallSlotBusy(reason=reason)
        self._held[key] = _Held(key=key, session_id=None, acquired_at=time.monotonic())
        return tuple(h.session_id for h in evicted if h.session_id is not None)

    def bind(self, key: str, session_id: str) -> None:
        """Attach the session id to the capacity already taken for `key`.

        A no-op for an unknown key, which is what an unlimited slot and a session started outside
        the signalling path both look like."""
        held = self._held.get(key)
        if held is None:
            return
        if held.session_id is not None and held.session_id != session_id:
            self._by_session.pop(held.session_id, None)
        held.session_id = session_id
        self._by_session[session_id] = key

    def release(self, *, key: str | None = None, session_id: str | None = None) -> None:
        """Give back capacity. Idempotent, and safe to call with either identifier or both.

        Both are accepted because the two release paths know different things: the router that
        failed before the session existed knows only the rendezvous, and the controller that ends a
        session knows only its id."""
        if session_id is not None:
            mapped = self._by_session.pop(session_id, None)
            if mapped is not None:
                held = self._held.pop(mapped, None)
                if held is not None:
                    log.info(
                        "call slot: released session=%s key=%s", session_id, held.key
                    )
        if key is not None:
            held = self._held.pop(key, None)
            if held is not None:
                if held.session_id is not None:
                    self._by_session.pop(held.session_id, None)
                log.info("call slot: released key=%s session=%s", key, held.session_id)

    # ── introspection (composition, diagnostics and tests; no contents) ──────

    @property
    def in_use(self) -> int:
        return len(self._held)

    def held_keys(self) -> tuple[str, ...]:
        return tuple(self._held)

    def held_sessions(self) -> tuple[str, ...]:
        return tuple(self._by_session)

    # ── internals ────────────────────────────────────────────────────────────

    def _finished(self, held: _Held) -> tuple[bool, bool]:
        """`(this holder has finished, the probe failed)`.

        An **unbound** holder is never reported finished. Its call is still inside the
        session-create request that took the capacity, so there is no session to ask about yet, and
        the request's own failure path is what releases it. Guessing here is how a call in the
        middle of negotiating would lose its slot to the next arrival."""
        if held.session_id is None:
            return False, False
        try:
            state = self._probe(held.session_id)
        except Exception:
            # Fail closed: the holder stays. Logged with the traceback because a probe that raises
            # is an operational defect, not ordinary contention (DL-0013: observability must not
            # change the decision, and here the decision is the safe one).
            log.warning(
                "call slot: probing holder session=%s failed; treating it as live",
                held.session_id,
                exc_info=True,
            )
            return False, True
        return state is None or state in TERMINAL_STATE_VALUES, False

    def _forget(self, held: _Held) -> None:
        self._held.pop(held.key, None)
        if held.session_id is not None:
            self._by_session.pop(held.session_id, None)


__all__ = [
    "BUSY_DETAIL",
    "CAPACITY_ENV",
    "TERMINAL_STATE_VALUES",
    "CallSlot",
    "CallSlotBusy",
    "capacity_from_env",
]
