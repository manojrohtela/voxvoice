"""ReplayEngine + ReplayController (M4.3).

The `ReplayEngine` assembles the read-only views of one `ReplaySession` (timeline,
index, correlator, metrics, inspector). The `ReplayController` drives deterministic
playback over the timeline cursor: play / pause / resume / stop, step forward /
backward, jump to timestamp / event, and playback speed.

**Replay is visualization only.** No Runtime logic runs; playback just advances a
cursor over recorded events. Real-time playback is driven by the caller feeding
elapsed wall-time into `advance(elapsed_s)` — the engine scales it by the speed into
*virtual* recorded time and moves the cursor — so replay stays fully deterministic
and testable (no background timers, no hidden clock).
"""

from __future__ import annotations

from enum import StrEnum

from core.events import Event

from .correlator import EventCorrelator
from .index import EventIndex
from .inspector import RuntimeStateSnapshot, SessionInspector
from .metrics import ConversationMetrics, MetricsCollector
from .recorder import ReplaySession
from .timeline import Timeline, TimelineBuilder, TimelineCursor, TimelineEntry


class ReplayState(StrEnum):
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"


class ReplayController:
    """Deterministic playback over a timeline cursor."""

    def __init__(self, timeline: Timeline, *, speed: float = 1.0) -> None:
        self._cursor = TimelineCursor(timeline)
        self._timeline = timeline
        self._state = ReplayState.STOPPED
        self._speed = max(0.0, speed)
        # Virtual playback clock, in recorded-time seconds since the timeline start.
        self._virtual_ts = timeline.start_ts or 0.0

    # ── read-only state ─────────────────────────────────────────────────────
    @property
    def state(self) -> ReplayState:
        return self._state

    @property
    def speed(self) -> float:
        return self._speed

    @property
    def cursor(self) -> TimelineCursor:
        return self._cursor

    @property
    def position(self) -> int:
        return self._cursor.index

    @property
    def current(self) -> TimelineEntry | None:
        return self._cursor.current

    @property
    def current_ts(self) -> float | None:
        return self._cursor.current_ts

    @property
    def progress(self) -> float:
        """0.0..1.0 through the timeline (by index)."""
        n = self._cursor.length
        return (self._cursor.index / n) if n else 1.0

    # ── transport controls ──────────────────────────────────────────────────
    def play(self) -> None:
        self._state = ReplayState.PLAYING

    def pause(self) -> None:
        if self._state is ReplayState.PLAYING:
            self._state = ReplayState.PAUSED

    def resume(self) -> None:
        if self._state is ReplayState.PAUSED:
            self._state = ReplayState.PLAYING

    def stop(self) -> None:
        self._state = ReplayState.STOPPED
        self._cursor.reset()
        self._virtual_ts = self._timeline.start_ts or 0.0

    def set_speed(self, speed: float) -> None:
        self._speed = max(0.0, speed)

    def step_forward(self) -> TimelineEntry:
        self._state = ReplayState.PAUSED
        entry = self._cursor.step_forward()
        self._virtual_ts = entry.ts
        return entry

    def step_backward(self) -> TimelineEntry | None:
        self._state = ReplayState.PAUSED
        entry = self._cursor.step_backward()
        self._virtual_ts = self._cursor.current_ts or (self._timeline.start_ts or 0.0)
        return entry

    def jump_to_timestamp(self, ts: float) -> int:
        i = self._cursor.jump_to_ts(ts)
        self._virtual_ts = ts
        return i

    def jump_to_event(self, event_id: str) -> int:
        i = self._cursor.jump_to_event(event_id)
        self._virtual_ts = self._cursor.current_ts or self._virtual_ts
        return i

    def advance(self, elapsed_s: float) -> list[TimelineEntry]:
        """Advance real-time playback by `elapsed_s` (scaled by speed). Returns the
        entries newly crossed. Deterministic; a no-op unless PLAYING."""
        if self._state is not ReplayState.PLAYING or elapsed_s <= 0:
            return []
        before = self._cursor.index
        self._virtual_ts += elapsed_s * self._speed
        self._cursor.jump_to_ts(self._virtual_ts)
        if self._cursor.at_end:
            self._state = ReplayState.STOPPED
        return list(self._timeline.entries[before : self._cursor.index])


class ReplayEngine:
    """Read-only views over one recorded session (visualization only)."""

    def __init__(self, session: ReplaySession) -> None:
        self._session = session
        self._events: tuple[Event, ...] = session.events
        self._timeline = TimelineBuilder().build(self._events)
        self._index = EventIndex(self._events)
        self._correlator = EventCorrelator()
        self._inspector = SessionInspector()

    @property
    def session(self) -> ReplaySession:
        return self._session

    @property
    def timeline(self) -> Timeline:
        return self._timeline

    @property
    def index(self) -> EventIndex:
        return self._index

    @property
    def correlator(self) -> EventCorrelator:
        return self._correlator

    def controller(self, *, speed: float = 1.0) -> ReplayController:
        return ReplayController(self._timeline, speed=speed)

    def metrics(self) -> ConversationMetrics:
        return MetricsCollector().collect(self._session.session_id, self._events)

    def state_at(self, index: int) -> RuntimeStateSnapshot:
        return self._inspector.state_at(self._events, index)
