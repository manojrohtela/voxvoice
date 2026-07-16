"""Event envelope, event-type vocabulary, and the EventBus interface
(Constitution §A5, §D4 / ADR-0004).

Definitions and contract only — **no bus implementation** (that lives in
`events/`, built at M4). Every runtime fact is one typed, enveloped, ordered
event, produced once by its owning component and fanned out to consumers.

Established at M1 as scaffolding so producers/consumers share one envelope shape.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    """The A5 event vocabulary. One producer per type (ARCHITECTURE_RULES AR-2).
    New types are added additively (AR-32); payload schemas are versioned."""

    # session lifecycle (producer: Conversation Orchestrator)
    SESSION_CREATED = "session.created"
    SESSION_CONNECTED = "session.connected"
    SESSION_SUSPENDED = "session.suspended"
    SESSION_RESUMED = "session.resumed"
    SESSION_ENDED = "session.ended"
    SESSION_FAILED = "session.failed"
    # speech / turn (producer: Turn Intelligence)
    SPEECH_USER_STARTED = "speech.user.started"
    SPEECH_USER_STOPPED = "speech.user.stopped"
    TRANSCRIPT_PARTIAL = "transcript.partial"
    TRANSCRIPT_FINAL = "transcript.final"
    TURN_USER_COMPLETED = "turn.user.completed"
    # agent (producers: AI Runtime / Speech Out / Interrupt engine)
    AGENT_THINKING_STARTED = "agent.thinking.started"
    AGENT_THINKING_FINISHED = "agent.thinking.finished"
    AGENT_SPEAKING_STARTED = "agent.speaking.started"
    AGENT_SPEAKING_FINISHED = "agent.speaking.finished"
    AGENT_INTERRUPTED = "agent.interrupted"
    # tools (producer: Tool Runtime)
    TOOL_CALLED = "tool.called"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    # lifecycle timeouts (producer: Lifecycle Manager)
    TIMEOUT_IDLE = "timeout.idle"
    TIMEOUT_TURN = "timeout.turn"
    TIMEOUT_SESSION = "timeout.session"
    TIMEOUT_TOOL = "timeout.tool"
    # observability / errors
    METRIC_TURN = "metric.turn"
    ERROR_PROVIDER = "error.provider"
    ERROR_RUNTIME = "error.runtime"


@dataclass(frozen=True, slots=True)
class Event:
    """The one envelope for every event, on the wire and in storage (§A5)."""

    id: str  # unique, per-session monotonic (e.g. ULID)
    session_id: str
    org_id: str
    type: EventType
    ts: float  # runtime clock, seconds
    seq: int  # strict per-session ordering
    turn: int | None = None  # conversation turn index
    data: dict[str, Any] = field(default_factory=dict)  # versioned payload


# A subscriber receives one Event.
EventHandler = Callable[[Event], Awaitable[None]]


class EventBus(ABC):
    """Interface only. Implementations (in-process now, distributed broker later)
    live in `events/` behind this contract (ADR-0006). Producers never block on
    consumers (AR-31)."""

    @abstractmethod
    async def publish(self, event: Event) -> None:
        """Emit one event to all subscribers. Must not raise into the producer."""

    @abstractmethod
    def subscribe(self, handler: EventHandler) -> None:
        """Register a consumer. Consumers integrate across layers via events,
        never via direct calls (AR-30)."""
