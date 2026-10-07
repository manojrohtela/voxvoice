"""Session diagnostics — a **projection** of the durable event log, nothing more (M12.14).

The Playground shows what the runtime did. That truth already exists: Epic 10's durable log holds
every event the runtime published, in the bus's own `seq` order, and M11's transcript sink holds
every finalized line. This module reads those two and renders them for a browser.

```
runtime ─► Event Bus ─► Epic 10 durable log ──┐
                                              ├─► this projection ─► SSE ─► Playground
runtime ─► TranscriptSink ─► transcript rows ─┘
```

**No new event, no new store, no new bus, and nothing is written here.** Live and replay are the
same read: a reader asks for everything after a `seq` it has already seen, so a browser that
reconnects, reloads, or opens the page after the call is over follows exactly the same path. That
is why there is no "live event" versus "durable event" distinction to make — there is one source.

## What cannot be shown, and why

There are no `stt.started` / `llm.started` / `tts.started` events to project. ADR-0038 **TP10**
rules that stage boundaries are *captured* on the hot path and **published once, after the turn is
terminal**, because `InProcessEventBus.publish()` is synchronous run-to-completion and publishing
at six boundaries would run every subscriber inside the voice-to-voice budget six times per turn.
So stage visibility arrives as it is produced: per-stage durations on `metric.turn`, as each turn
completes. A stage the runtime could not measure stays **absent** — never rendered as 0 (E11-10).

## Redaction

`data` is never passed through. Each event type names the fields it may contribute
(`SAFE_FIELDS`), every value is re-checked against `_safe_value`, and anything else is dropped —
so a payload that gains a field later is silently excluded until someone adds it here deliberately.
The provider *name* is dropped as well: `capability` ("stt", "tts") carries the diagnostic value,
and the console deliberately tells the browser nothing about which vendor is configured
(`/v1/console/meta` carries no provider information either).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session as DBSession

from core.events import Event, EventType

from ..conversations import service as conversations

#: Stage order as `metric.turn` reports it (the pipeline's own order).
STAGES = ("endpoint", "stt", "llm", "tts", "egress")

#: The categories a diagnostic can belong to. Named for the runtime subsystem that produced it.
CATEGORIES = (
    "SESSION",
    "TRANSPORT",
    "VAD",
    "ENDPOINT",
    "TURN",
    "SPEECH",
    "INTERRUPT",
    "PROVIDER",
    "LATENCY",
    "RUNTIME",
)

#: category · severity · human message, per event type. An event type absent from this map is not
#: shown: the Playground renders the session's conversation, not the whole platform vocabulary.
_SHOWN: dict[EventType, tuple[str, str, str]] = {
    EventType.RUNTIME_STARTED: ("RUNTIME", "info", "Runtime started"),
    EventType.SESSION_STARTED: ("SESSION", "info", "Session started"),
    EventType.TRANSPORT_CONNECTED: ("TRANSPORT", "info", "Caller connected"),
    EventType.TRANSPORT_MEDIA_READY: ("TRANSPORT", "info", "Audio flowing"),
    EventType.SPEECH_STARTED: ("VAD", "info", "User speech detected"),
    EventType.SPEECH_STOPPED: ("VAD", "info", "User speech stopped"),
    EventType.ENDPOINT_DETECTED: ("ENDPOINT", "info", "End of speech confirmed"),
    EventType.ENDPOINT_CANCELLED: ("ENDPOINT", "info", "End of speech cancelled"),
    EventType.SMART_TURN_DECISION: ("ENDPOINT", "info", "Turn-taking decision"),
    EventType.TURN_STARTED: ("TURN", "info", "Turn started"),
    EventType.ASSISTANT_STARTED_SPEAKING: ("SPEECH", "info", "Agent started speaking"),
    EventType.ASSISTANT_STOPPED_SPEAKING: ("SPEECH", "info", "Agent stopped speaking"),
    EventType.TURN_COMPLETED: ("TURN", "info", "Turn completed"),
    EventType.TURN_CANCELLED: ("TURN", "warn", "Turn cancelled"),
    EventType.INTERRUPT_DETECTED: ("INTERRUPT", "info", "Possible interruption"),
    EventType.INTERRUPT_CONFIRMED: ("INTERRUPT", "info", "Caller interrupted"),
    EventType.INTERRUPT_CANCELLED: ("INTERRUPT", "info", "Interruption withdrawn"),
    EventType.PROVIDER_CONNECTED: ("PROVIDER", "info", "Provider ready"),
    EventType.PROVIDER_DISCONNECTED: ("PROVIDER", "warn", "Provider unavailable"),
    EventType.METRIC_TURN: ("LATENCY", "info", "Turn latency measured"),
    EventType.TRANSPORT_DISCONNECTED: ("TRANSPORT", "warn", "Caller disconnected"),
    EventType.TRANSPORT_FAILED: ("TRANSPORT", "error", "Media failed"),
    EventType.RUNTIME_ERROR: ("RUNTIME", "error", "Runtime error"),
    EventType.SESSION_ENDED: ("SESSION", "info", "Session ended"),
    EventType.RUNTIME_STOPPED: ("RUNTIME", "info", "Runtime stopped"),
}

#: The only payload fields that may reach a browser, per event type. Everything else is dropped.
SAFE_FIELDS: dict[EventType, tuple[str, ...]] = {
    EventType.RUNTIME_ERROR: ("reason",),
    EventType.SESSION_ENDED: ("reason",),
    EventType.TRANSPORT_CONNECTED: ("detail",),
    EventType.TRANSPORT_MEDIA_READY: ("detail",),
    EventType.TRANSPORT_DISCONNECTED: ("detail",),
    EventType.TRANSPORT_FAILED: ("detail",),
    EventType.PROVIDER_CONNECTED: ("capability",),
    EventType.PROVIDER_DISCONNECTED: ("capability", "kind"),
    EventType.ENDPOINT_DETECTED: ("decision",),
    EventType.SMART_TURN_DECISION: ("decision",),
}

#: Values longer than this are truncated: a diagnostic is a label, never a document.
MAX_VALUE = 200

#: A last line of defence, applied to every value that survives the allowlist. Nothing in the
#: runtime's payloads looks like this today, and the test proves such a value never escapes.
_SECRET_SHAPED = re.compile(
    r"(sk-[A-Za-z0-9]{8,}|vf_[A-Za-z0-9]{8,}|Bearer\s+\S{8,}|[A-Za-z0-9_-]{32,})"
)


def _safe_value(value: object) -> str | int | None:
    """One scalar, or nothing. Never a dict, a list, an object or a secret-shaped string."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or _SECRET_SHAPED.search(text):
        return None
    return text[:MAX_VALUE]


@dataclass(frozen=True)
class DiagnosticEvent:
    """One row of the diagnostics panel. Everything here is safe to render in a browser."""

    seq: int
    #: Seconds since the session's first event, so a reader needs no clock of its own.
    offset_s: float
    #: Wall clock of the runtime that produced it (UTC seconds), for correlating with server logs.
    ts: float
    type: str
    category: str
    severity: str
    message: str
    turn: int | None
    #: Allowlisted payload fields, flattened to scalars.
    detail: dict[str, Any] = field(default_factory=dict)
    #: Present only on `metric.turn`: measured stage durations. An unmeasured stage is absent.
    stages_ms: dict[str, int] = field(default_factory=dict)
    voice_to_voice_ms: int | None = None

    def as_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "seq": self.seq,
            "offset_s": self.offset_s,
            "ts": self.ts,
            "type": self.type,
            "category": self.category,
            "severity": self.severity,
            "message": self.message,
            "turn": self.turn,
            "detail": self.detail,
        }
        if self.type == EventType.METRIC_TURN.value:
            body["stages_ms"] = self.stages_ms
            body["voice_to_voice_ms"] = self.voice_to_voice_ms
        return body


def _latency(event: Event) -> tuple[dict[str, int], int | None, str]:
    """`metric.turn`'s measured stages. Unmeasured stays absent; a failed turn is a warning."""
    data: dict[str, Any] = event.data or {}
    ms = data.get("ms") or {}
    stages = {s: ms[s] for s in STAGES if isinstance(ms.get(s), int)}
    v2v = data.get("voice_to_voice_ms")
    status = data.get("status")
    severity = "info" if status in (None, "completed") else "warn"
    return stages, v2v if isinstance(v2v, int) else None, severity


def project(event: Event, origin: float) -> DiagnosticEvent | None:
    """One durable event → one browser-safe row, or `None` if this type is not shown."""
    shown = _SHOWN.get(event.type)
    if shown is None:
        return None
    category, severity, message = shown

    detail: dict[str, Any] = {}
    for name in SAFE_FIELDS.get(event.type, ()):
        value = _safe_value((event.data or {}).get(name))
        if value is not None:
            detail[name] = value

    stages: dict[str, int] = {}
    v2v: int | None = None
    if event.type is EventType.METRIC_TURN:
        stages, v2v, severity = _latency(event)
        status = (event.data or {}).get("status")
        if isinstance(status, str) and status != "completed":
            detail["status"] = status

    return DiagnosticEvent(
        seq=event.seq,
        offset_s=round(max(0.0, event.ts - origin), 3),
        ts=event.ts,
        type=event.type.value,
        category=category,
        severity=severity,
        message=message,
        turn=event.turn,
        detail=detail,
        stages_ms=stages,
        voice_to_voice_ms=v2v,
    )


def read(
    db: DBSession, org_id: str, session_id: str, *, after_seq: int = 0
) -> list[DiagnosticEvent]:
    """This session's diagnostics after `after_seq`, in the bus's own order.

    Tenant-scoped by the same predicate Conversations uses: another organization's session reads
    as empty, exactly as it reads as missing there (S4)."""
    events = conversations.events(db, org_id, session_id)
    if not events:
        return []
    origin = min(e.ts for e in events)
    projected = (project(e, origin) for e in events if e.seq > after_seq)
    return [row for row in projected if row is not None]


#: The states the Playground shows. Each is the runtime's own vocabulary — `TurnPhase.LISTENING`,
#: `TurnPhase.ASSISTANT_SPEAKING` and the transport/session facts — read off the event stream.
#: This is a derivation, not a second state machine: it holds nothing and decides nothing.
RuntimeState = str

_IDLE = "IDLE"


def runtime_state(events: list[DiagnosticEvent]) -> RuntimeState:
    """The runtime's current state, derived from what it has already told us.

    Later events win, so the answer is simply the most recent event that implies a state. An
    unknown or empty stream is `CONNECTING`, never a guess about the runtime's health."""
    if not events:
        return "CONNECTING"
    for event in reversed(events):
        kind = event.type
        if kind in (EventType.SESSION_ENDED.value, EventType.RUNTIME_STOPPED.value):
            return "ENDED"
        if kind in (EventType.RUNTIME_ERROR.value, EventType.TRANSPORT_FAILED.value):
            return "ERROR"
        if kind == EventType.TRANSPORT_DISCONNECTED.value:
            return "ENDING"
        if kind == EventType.ASSISTANT_STARTED_SPEAKING.value:
            return "ASSISTANT_SPEAKING"
        if kind in (
            EventType.ASSISTANT_STOPPED_SPEAKING.value,
            EventType.TURN_COMPLETED.value,
            EventType.TURN_CANCELLED.value,
            EventType.METRIC_TURN.value,
        ):
            return "LISTENING"
        if kind == EventType.ENDPOINT_DETECTED.value:
            return "PROCESSING"
        if kind == EventType.SPEECH_STARTED.value:
            return "USER_SPEAKING"
        if kind in (EventType.SPEECH_STOPPED.value, EventType.TURN_STARTED.value):
            return "LISTENING"
        if kind == EventType.TRANSPORT_MEDIA_READY.value:
            return "LISTENING"
        if kind == EventType.TRANSPORT_CONNECTED.value:
            return "CONNECTED"
        if kind == EventType.SESSION_STARTED.value:
            return "CONNECTING"
    return "CONNECTING"


__all__ = [
    "CATEGORIES",
    "MAX_VALUE",
    "SAFE_FIELDS",
    "STAGES",
    "DiagnosticEvent",
    "RuntimeState",
    "project",
    "read",
    "runtime_state",
]
