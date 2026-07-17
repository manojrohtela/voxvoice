"""ConversationRecorder + ReplaySession (M4.3).

The recorder subscribes to the Internal Event Bus **read-only** (via the public
`core.events.EventBus` contract) and records every event into a per-session
`ReplaySession`. It **never modifies Runtime behaviour** — it is a passive
subscriber, fully isolated: a recording failure is caught + counted, never raised
into the bus (the Runtime cannot be affected by recording).

A `ReplaySession` is the immutable-after-close, ordered event log for one
conversation — the single source of truth every replay/observability view is
derived from. Recording captures only `core.events.Event` envelopes; no Runtime
object is retained.
"""

from __future__ import annotations

import logging

from core.events import Event, EventBus, EventType, Subscription

log = logging.getLogger("voxvoice.replay.recorder")


class ReplaySession:
    """The recorded, ordered event log for one conversation."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._events: list[Event] = []
        self.closed = False

    # ── recording (recorder-only) ───────────────────────────────────────────
    def append(self, event: Event) -> None:
        self._events.append(event)
        if event.type is EventType.SESSION_ENDED:
            self.closed = True

    # ── read-only views ──────────────────────────────────────────────────────
    @property
    def events(self) -> tuple[Event, ...]:
        return tuple(self._events)

    @property
    def event_count(self) -> int:
        return len(self._events)

    @property
    def org_id(self) -> str:
        return self._events[0].org_id if self._events else ""

    @property
    def conversation_id(self) -> str | None:
        return self._events[0].conversation_id if self._events else None

    @property
    def started_at(self) -> float | None:
        return self._events[0].ts if self._events else None

    @property
    def ended_at(self) -> float | None:
        return self._events[-1].ts if self._events else None

    @property
    def duration_s(self) -> float:
        if len(self._events) < 2:
            return 0.0
        return self._events[-1].ts - self._events[0].ts


class ConversationRecorder:
    """Subscribes to an Event Bus and records events per session. Read-only and
    fully isolated from the Runtime."""

    def __init__(self) -> None:
        self._sessions: dict[str, ReplaySession] = {}
        self._attachments: list[tuple[EventBus, Subscription]] = []
        self._recorded = 0
        self._failures = 0

    @property
    def recorded_count(self) -> int:
        return self._recorded

    @property
    def failure_count(self) -> int:
        return self._failures

    # ── bus attachment (never initiated by the Runtime) ────────────────────
    def attach(self, bus: EventBus) -> Subscription:
        sub = bus.subscribe(self._record)
        self._attachments.append((bus, sub))
        return sub

    def detach_all(self) -> None:
        for bus, sub in self._attachments:
            try:
                bus.unsubscribe(sub)
            except Exception:  # noqa: BLE001 — best-effort
                pass
        self._attachments.clear()

    def _record(self, event: Event) -> None:
        try:
            session = self._sessions.get(event.session_id)
            if session is None:
                session = ReplaySession(event.session_id)
                self._sessions[event.session_id] = session
            session.append(event)
            self._recorded += 1
        except Exception as exc:  # noqa: BLE001 — never raise into the bus/Runtime
            self._failures += 1
            log.warning("recording failed (isolated): %s", exc)

    # ── read-only access ─────────────────────────────────────────────────────
    def get(self, session_id: str) -> ReplaySession | None:
        return self._sessions.get(session_id)

    def sessions(self) -> tuple[ReplaySession, ...]:
        return tuple(self._sessions.values())

    def __len__(self) -> int:
        return len(self._sessions)
