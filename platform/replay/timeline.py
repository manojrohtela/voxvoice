"""Timeline model — TimelineBuilder / Timeline / TimelineEntry / TimelineCursor
(M4.3).

Turns a `ReplaySession`'s recorded events into a **visualization-only** timeline: an
ordered list of colour-coded, laned entries (with paired durations for speech / turns
/ assistant-speaking). Pure and deterministic — it executes no Runtime logic; it only
re-shapes recorded events for display. `TimelineCursor` is the deterministic playback
position (index / timestamp) the Replay Controller drives.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from core.events import Event, EventType

from .errors import CursorOutOfRange


class Category(str):
    """Colour-coded category (a lane grouping). Plain str for easy JSON."""


# EventType → category (drives colour + lane).
_CATEGORY: dict[EventType, str] = {
    EventType.SPEECH_STARTED: "speech",
    EventType.SPEECH_STOPPED: "speech",
    EventType.TURN_STARTED: "turn",
    EventType.TURN_COMPLETED: "turn",
    EventType.TURN_CANCELLED: "turn",
    EventType.ASSISTANT_STARTED_SPEAKING: "assistant",
    EventType.ASSISTANT_STOPPED_SPEAKING: "assistant",
    EventType.ENDPOINT_DETECTED: "endpoint",
    EventType.ENDPOINT_CANCELLED: "endpoint",
    EventType.INTERRUPT_DETECTED: "interrupt",
    EventType.INTERRUPT_CONFIRMED: "interrupt",
    EventType.INTERRUPT_CANCELLED: "interrupt",
    EventType.SMART_TURN_DECISION: "smart_turn",
    EventType.PROVIDER_CONNECTED: "provider",
    EventType.PROVIDER_DISCONNECTED: "provider",
    EventType.RUNTIME_STARTED: "lifecycle",
    EventType.RUNTIME_STOPPED: "lifecycle",
    EventType.SESSION_STARTED: "lifecycle",
    EventType.SESSION_ENDED: "lifecycle",
    EventType.RUNTIME_ERROR: "error",
}

# category → UI lane + colour token (the UI maps the token to a hex).
_LANE: dict[str, str] = {
    "speech": "speech",
    "turn": "turns",
    "assistant": "assistant",
    "endpoint": "decisions",
    "interrupt": "decisions",
    "smart_turn": "decisions",
    "provider": "providers",
    "lifecycle": "system",
    "error": "system",
}
CATEGORY_COLORS: dict[str, str] = {
    "speech": "cyan",
    "turn": "violet",
    "assistant": "emerald",
    "endpoint": "amber",
    "interrupt": "rose",
    "smart_turn": "indigo",
    "provider": "sky",
    "lifecycle": "slate",
    "error": "red",
}

# Start → end pairing for duration entries.
_PAIRS: dict[EventType, frozenset[EventType]] = {
    EventType.SPEECH_STARTED: frozenset({EventType.SPEECH_STOPPED}),
    EventType.TURN_STARTED: frozenset({EventType.TURN_COMPLETED, EventType.TURN_CANCELLED}),
    EventType.ASSISTANT_STARTED_SPEAKING: frozenset({EventType.ASSISTANT_STOPPED_SPEAKING}),
}


def category_of(event_type: EventType) -> str:
    return _CATEGORY.get(event_type, "lifecycle")


def _label(event_type: EventType) -> str:
    return event_type.name.replace("_", " ").title()


@dataclass(slots=True)
class TimelineEntry:
    seq: int
    ts: float
    event_id: str
    type: EventType
    source: str
    category: str
    lane: str
    color: str
    label: str
    turn: int | None = None
    duration_ms: float | None = None  # set on a start entry once its end is seen
    end_ts: float | None = None


@dataclass(slots=True)
class Timeline:
    entries: list[TimelineEntry] = field(default_factory=list)
    start_ts: float | None = None
    end_ts: float | None = None

    @property
    def duration_ms(self) -> float:
        if self.start_ts is None or self.end_ts is None:
            return 0.0
        return (self.end_ts - self.start_ts) * 1000.0

    def __len__(self) -> int:
        return len(self.entries)


class TimelineBuilder:
    """Builds a `Timeline` from recorded events (deterministic; display-only)."""

    def build(self, events: Sequence[Event]) -> Timeline:
        entries: list[TimelineEntry] = []
        pending: dict[EventType, int] = {}  # start type → entry index awaiting its end
        end_to_start = {
            end: start for start, ends in _PAIRS.items() for end in ends
        }
        for ev in events:
            cat = category_of(ev.type)
            entry = TimelineEntry(
                seq=ev.seq,
                ts=ev.ts,
                event_id=ev.id,
                type=ev.type,
                source=ev.source.value,
                category=cat,
                lane=_LANE.get(cat, "system"),
                color=CATEGORY_COLORS.get(cat, "slate"),
                label=_label(ev.type),
                turn=ev.turn,
            )
            idx = len(entries)
            entries.append(entry)
            if ev.type in _PAIRS:
                pending[ev.type] = idx
            elif ev.type in end_to_start:
                start_type = end_to_start[ev.type]
                start_idx = pending.pop(start_type, None)
                if start_idx is not None:
                    s = entries[start_idx]
                    s.duration_ms = (ev.ts - s.ts) * 1000.0
                    s.end_ts = ev.ts
        start_ts = events[0].ts if events else None
        end_ts = events[-1].ts if events else None
        return Timeline(entries=entries, start_ts=start_ts, end_ts=end_ts)


class TimelineCursor:
    """A deterministic playback position over a timeline. Index-based; supports
    step, seek, and jump-to-timestamp. Executes no Runtime logic."""

    def __init__(self, timeline: Timeline) -> None:
        self._timeline = timeline
        self._index = 0  # points at the next-to-play entry [0..len]

    @property
    def index(self) -> int:
        return self._index

    @property
    def length(self) -> int:
        return len(self._timeline.entries)

    @property
    def at_start(self) -> bool:
        return self._index <= 0

    @property
    def at_end(self) -> bool:
        return self._index >= self.length

    @property
    def current(self) -> TimelineEntry | None:
        """The most recently played entry (index-1), or None at the start."""
        if self._index <= 0 or self._index > self.length:
            return None
        return self._timeline.entries[self._index - 1]

    @property
    def current_ts(self) -> float | None:
        c = self.current
        return c.ts if c is not None else self._timeline.start_ts

    def played(self) -> Iterable[TimelineEntry]:
        return self._timeline.entries[: self._index]

    def reset(self) -> None:
        self._index = 0

    def step_forward(self) -> TimelineEntry:
        if self._index >= self.length:
            raise CursorOutOfRange("already at end of timeline")
        self._index += 1
        return self._timeline.entries[self._index - 1]

    def step_backward(self) -> TimelineEntry | None:
        if self._index <= 0:
            raise CursorOutOfRange("already at start of timeline")
        self._index -= 1
        return self.current

    def seek(self, index: int) -> None:
        if index < 0 or index > self.length:
            raise CursorOutOfRange(f"index {index} out of [0, {self.length}]")
        self._index = index

    def jump_to_ts(self, ts: float) -> int:
        """Move so every entry with `entry.ts <= ts` is played (deterministic)."""
        i = 0
        for entry in self._timeline.entries:
            if entry.ts <= ts:
                i += 1
            else:
                break
        self._index = i
        return i

    def jump_to_event(self, event_id: str) -> int:
        for i, entry in enumerate(self._timeline.entries):
            if entry.event_id == event_id:
                self._index = i + 1  # play up to and including this event
                return self._index
        raise CursorOutOfRange(f"event {event_id!r} not in timeline")
