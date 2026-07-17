"""EventCorrelator (M4.3).

Groups recorded events into correlation chains — by `correlation_id` and by
conversation `turn` — so a view can show "everything that belongs together" (e.g.
all events of turn 3, or all events sharing a correlation id). Pure + deterministic;
ordering is the bus `seq` order preserved from recording.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from core.events import Event


@dataclass(slots=True)
class CorrelationGroup:
    kind: str  # "correlation" | "turn"
    key: str
    events: list[Event] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.events)


class EventCorrelator:
    def by_correlation(self, events: Sequence[Event]) -> list[CorrelationGroup]:
        groups: dict[str, CorrelationGroup] = {}
        for e in events:
            if not e.correlation_id:
                continue
            g = groups.get(e.correlation_id)
            if g is None:
                g = CorrelationGroup("correlation", e.correlation_id)
                groups[e.correlation_id] = g
            g.events.append(e)
        return list(groups.values())

    def by_turn(self, events: Sequence[Event]) -> list[CorrelationGroup]:
        groups: dict[int, CorrelationGroup] = {}
        for e in events:
            if e.turn is None:
                continue
            g = groups.get(e.turn)
            if g is None:
                g = CorrelationGroup("turn", str(e.turn))
                groups[e.turn] = g
            g.events.append(e)
        return [groups[k] for k in sorted(groups)]

    def related(self, events: Sequence[Event], event: Event) -> list[Event]:
        """Events correlated to `event` (same correlation_id or same turn), in order."""
        return [
            e
            for e in events
            if e.id != event.id
            and (
                (event.correlation_id and e.correlation_id == event.correlation_id)
                or (event.turn is not None and e.turn == event.turn)
            )
        ]
