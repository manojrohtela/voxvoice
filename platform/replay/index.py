"""EventIndex + EventFilter (M4.3).

Deterministic filtering + search over a `ReplaySession`'s recorded events. Filter by
session / conversation / turn / agent / org / provider / event-type / time-range;
search by substring across type, source, and payload. Pure and read-only.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from core.events import Event, EventType


@dataclass(frozen=True, slots=True)
class EventFilter:
    """A conjunctive event filter — every set field must match. Unset → any."""

    session_id: str | None = None
    conversation_id: str | None = None
    turn: int | None = None
    agent_id: str | None = None
    org_id: str | None = None
    provider: str | None = None  # matches data.provider or data.capability
    source: str | None = None
    types: frozenset[EventType] | None = None
    start_ts: float | None = None
    end_ts: float | None = None

    def matches(self, e: Event) -> bool:
        if self.session_id is not None and e.session_id != self.session_id:
            return False
        if self.conversation_id is not None and e.conversation_id != self.conversation_id:
            return False
        if self.turn is not None and e.turn != self.turn:
            return False
        if self.org_id is not None and e.org_id != self.org_id:
            return False
        if self.source is not None and e.source.value != self.source:
            return False
        if self.agent_id is not None and str(e.metadata.get("agent_id")) != self.agent_id:
            return False
        if self.provider is not None and self.provider not in (
            e.data.get("provider"), e.data.get("capability")
        ):
            return False
        if self.types is not None and e.type not in self.types:
            return False
        if self.start_ts is not None and e.ts < self.start_ts:
            return False
        if self.end_ts is not None and e.ts > self.end_ts:
            return False
        return True


class EventIndex:
    """An index over recorded events for filtering + search. Builds light type/turn
    indices for readiness; matching is otherwise a deterministic ordered scan."""

    def __init__(self, events: Sequence[Event]) -> None:
        self._events: tuple[Event, ...] = tuple(events)
        self._by_type: dict[EventType, list[Event]] = {}
        self._by_turn: dict[int, list[Event]] = {}
        for e in self._events:
            self._by_type.setdefault(e.type, []).append(e)
            if e.turn is not None:
                self._by_turn.setdefault(e.turn, []).append(e)

    @property
    def events(self) -> tuple[Event, ...]:
        return self._events

    def by_type(self, t: EventType) -> list[Event]:
        return list(self._by_type.get(t, ()))

    def by_turn(self, turn: int) -> list[Event]:
        return list(self._by_turn.get(turn, ()))

    def type_counts(self) -> dict[str, int]:
        return {t.value: len(v) for t, v in self._by_type.items()}

    def filter(self, spec: EventFilter) -> list[Event]:
        return [e for e in self._events if spec.matches(e)]

    def search(self, text: str) -> list[Event]:
        """Case-insensitive substring match over type, source, and payload/metadata."""
        needle = text.lower()
        out: list[Event] = []
        for e in self._events:
            blob = (
                f"{e.type.value} {e.source.value} "
                f"{json.dumps(e.data, default=str)} {json.dumps(e.metadata, default=str)}"
            ).lower()
            if needle in blob:
                out.append(e)
        return out
