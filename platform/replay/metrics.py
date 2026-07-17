"""MetricsCollector + ConversationMetrics (M4.3).

Derives observability metrics from a `ReplaySession`'s recorded events — pure and
deterministic (no Runtime execution). Only metrics the current event stream can
support are computed; metrics that depend on subsystems not yet emitting events
(thinking time, token usage, tool calls, memory access, warnings) are reported as
`0` with `available=False` in `pending_metrics`, so the UI never shows a value the
backend cannot produce (no fake UI).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

from core.events import Event, EventType


def _avg(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


@dataclass(slots=True)
class ConversationMetrics:
    session_id: str = ""
    event_count: int = 0
    duration_s: float = 0.0
    # turns
    turn_count: int = 0
    turns_completed: int = 0
    turns_cancelled: int = 0
    avg_turn_duration_ms: float = 0.0
    total_turn_duration_ms: float = 0.0
    # timing
    avg_response_time_ms: float = 0.0  # turn start → assistant speaking
    speech_time_ms: float = 0.0
    speech_segments: int = 0
    assistant_speaking_ms: float = 0.0
    # decisions
    interrupt_count: int = 0
    endpoint_count: int = 0
    smart_turn_decisions: int = 0
    # providers / reliability
    provider_usage: dict[str, int] = field(default_factory=dict)
    errors: int = 0
    warnings: int = 0
    # not yet derivable from the event stream (upstream subsystems land later)
    thinking_time_ms: float = 0.0
    token_usage: int = 0
    tool_calls: int = 0
    memory_accesses: int = 0
    pending_metrics: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class MetricsCollector:
    # Metrics whose source events do not exist yet (M5 memory/tool/prompt runtime).
    _PENDING = ("thinking_time_ms", "token_usage", "tool_calls", "memory_accesses", "warnings")

    def collect(self, session_id: str, events: Sequence[Event]) -> ConversationMetrics:
        m = ConversationMetrics(session_id=session_id, event_count=len(events))
        if events:
            m.duration_s = events[-1].ts - events[0].ts

        turn_started_ts: dict[int, float] = {}
        speech_open: float | None = None
        assistant_open: float | None = None
        turn_durations: list[float] = []
        response_times: list[float] = []

        for e in events:
            t = e.type
            if t is EventType.TURN_STARTED:
                m.turn_count += 1
                if e.turn is not None:
                    turn_started_ts[e.turn] = e.ts
            elif t is EventType.TURN_COMPLETED:
                m.turns_completed += 1
                if e.turn in turn_started_ts:
                    turn_durations.append((e.ts - turn_started_ts[e.turn]) * 1000.0)
            elif t is EventType.TURN_CANCELLED:
                m.turns_cancelled += 1
                if e.turn in turn_started_ts:
                    turn_durations.append((e.ts - turn_started_ts[e.turn]) * 1000.0)
            elif t is EventType.ASSISTANT_STARTED_SPEAKING:
                assistant_open = e.ts
                if e.turn in turn_started_ts:
                    response_times.append((e.ts - turn_started_ts[e.turn]) * 1000.0)
            elif t is EventType.ASSISTANT_STOPPED_SPEAKING:
                if assistant_open is not None:
                    m.assistant_speaking_ms += (e.ts - assistant_open) * 1000.0
                    assistant_open = None
            elif t is EventType.SPEECH_STARTED:
                speech_open = e.ts
                m.speech_segments += 1
            elif t is EventType.SPEECH_STOPPED:
                if speech_open is not None:
                    m.speech_time_ms += (e.ts - speech_open) * 1000.0
                    speech_open = None
            elif t is EventType.INTERRUPT_CONFIRMED:
                m.interrupt_count += 1
            elif t is EventType.ENDPOINT_DETECTED:
                m.endpoint_count += 1
            elif t is EventType.SMART_TURN_DECISION:
                m.smart_turn_decisions += 1
            elif t is EventType.PROVIDER_CONNECTED:
                name = e.data.get("provider") or e.data.get("capability") or "?"
                m.provider_usage[name] = m.provider_usage.get(name, 0) + 1
            elif t is EventType.RUNTIME_ERROR:
                m.errors += 1

        m.total_turn_duration_ms = sum(turn_durations)
        m.avg_turn_duration_ms = _avg(turn_durations)
        m.avg_response_time_ms = _avg(response_times)
        m.pending_metrics = list(self._PENDING)
        return m
