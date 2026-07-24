"""Event envelope, event-type vocabulary, and the EventBus contract
(Constitution §A5, §D4 / ADR-0004).

Definitions and contract only — **no bus implementation** (that lives in `events/`,
the in-process bus built at M4.1). Every runtime fact is one typed, enveloped,
ordered event, produced once by its owning component and fanned out to consumers
that integrate via events, never direct calls (AR-30).

Established at M1 as scaffolding (one envelope shape); **refined at M4.1** to the
execution-ready shape — additive envelope fields (correlation/source/conversation/
metadata) + a deterministic, synchronous, in-process `EventBus` contract. Unreleased
scaffolding with no prior consumers, so this is a refinement, not a versioned break
(DL-0009 precedent; recorded in DL-0017).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    """The event vocabulary (§A5). One producer per type (AR-2). New types are
    added additively (AR-32); payload schemas are versioned. Values are dotted,
    lowercase, and stable — the member names are the code-facing identifiers."""

    # runtime lifecycle (producer: SessionWorker / Orchestrator)
    RUNTIME_STARTED = "runtime.started"
    RUNTIME_STOPPED = "runtime.stopped"
    RUNTIME_ERROR = "runtime.error"
    # session lifecycle (producer: Conversation Orchestrator)
    SESSION_STARTED = "session.started"
    SESSION_ENDED = "session.ended"
    # turn lifecycle (producer: Turn Engine)
    TURN_STARTED = "turn.started"
    TURN_COMPLETED = "turn.completed"
    TURN_CANCELLED = "turn.cancelled"
    # voice activity (producer: Voice Activity Framework)
    SPEECH_STARTED = "speech.started"
    SPEECH_STOPPED = "speech.stopped"
    # endpoint (producer: Endpoint Intelligence)
    ENDPOINT_DETECTED = "endpoint.detected"
    ENDPOINT_CANCELLED = "endpoint.cancelled"
    # interrupt (producer: Interrupt Intelligence)
    INTERRUPT_DETECTED = "interrupt.detected"
    INTERRUPT_CONFIRMED = "interrupt.confirmed"
    INTERRUPT_CANCELLED = "interrupt.cancelled"
    # assistant playback (producer: Runtime Pipeline)
    ASSISTANT_STARTED_SPEAKING = "assistant.started_speaking"
    ASSISTANT_STOPPED_SPEAKING = "assistant.stopped_speaking"
    # smart turn (producer: Smart Turn Intelligence)
    SMART_TURN_DECISION = "smart_turn.decision"
    # provider (producer: Provider Registry / Runtime)
    PROVIDER_CONNECTED = "provider.connected"
    PROVIDER_DISCONNECTED = "provider.disconnected"

    # ── AI Runtime content vocabulary (M5.0) — DEFINED here, EMITTED by the
    #    Epic-5 modules as they land. Additive (AR-32); provider-neutral. No
    #    producer emits these yet (M5.0 is infrastructure only).
    # content ingress / transcript (producer: Interaction Runtime)
    INPUT_RECEIVED = "input.received"
    INPUT_PROCESSED = "input.processed"
    TRANSCRIPT_STARTED = "transcript.started"
    TRANSCRIPT_UPDATED = "transcript.updated"
    TRANSCRIPT_COMPLETED = "transcript.completed"
    # memory (producer: Memory Runtime)
    MEMORY_STORED = "memory.stored"
    MEMORY_RETRIEVED = "memory.retrieved"
    MEMORY_UPDATED = "memory.updated"
    MEMORY_EXPIRED = "memory.expired"
    MEMORY_EVICTED = "memory.evicted"
    MEMORY_CLEARED = "memory.cleared"
    MEMORY_SUMMARIZED = "memory.summarized"
    # prompt (producer: Prompt Runtime)
    PROMPT_RENDERED = "prompt.rendered"
    PROMPT_TEMPLATE_UPDATED = "prompt.template.updated"
    # knowledge (producer: Knowledge Runtime)
    KNOWLEDGE_INDEXED = "knowledge.indexed"
    KNOWLEDGE_RETRIEVED = "knowledge.retrieved"
    KNOWLEDGE_SOURCE_UPDATED = "knowledge.source.updated"
    # context (producer: Context Runtime)
    CONTEXT_ASSEMBLED = "context.assembled"
    CONTEXT_TRUNCATED = "context.truncated"
    # reasoning + model (producer: Reasoning Runtime)
    REASONING_STARTED = "reasoning.started"
    REASONING_COMPLETED = "reasoning.completed"
    REASONING_FAILED = "reasoning.failed"
    MODEL_TOKEN = "model.token"
    MODEL_USAGE = "model.usage"
    # tools (producer: Tool Runtime / MCP Runtime)
    TOOL_REQUESTED = "tool.requested"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    # tool governance (producer: Tool Runtime Governance, M6.1B-2) — additive
    # vocabulary extension anticipated by the M6.1A design (no existing member
    # changed; AR-32).
    TOOL_POLICY_EVALUATED = "tool.policy.evaluated"
    TOOL_APPROVAL_REQUESTED = "tool.approval.requested"
    TOOL_APPROVAL_GRANTED = "tool.approval.granted"
    TOOL_APPROVAL_REJECTED = "tool.approval.rejected"
    TOOL_APPROVAL_EXPIRED = "tool.approval.expired"
    TOOL_AUTHORIZATION_COMPLETED = "tool.authorization.completed"
    TOOL_RATE_LIMIT_EVALUATED = "tool.rate_limit.evaluated"
    TOOL_SECRET_REQUESTED = "tool.secret.requested"
    TOOL_GOVERNANCE_COMPLETED = "tool.governance.completed"
    # tool observability + replay (producer: Tool Runtime Observability, M6.1B-3)
    # — additive vocabulary extension (no existing member changed; AR-32).
    TOOL_EXECUTION_TRACED = "tool.execution.traced"
    TOOL_REPLAY_RECORDED = "tool.replay.recorded"
    TOOL_REPLAY_VALIDATED = "tool.replay.validated"
    TOOL_REPLAY_MISMATCH = "tool.replay.mismatch"
    TOOL_AUDIT_RECORDED = "tool.audit.recorded"
    TOOL_DIAGNOSTICS_GENERATED = "tool.diagnostics.generated"
    TOOL_METRICS_UPDATED = "tool.metrics.updated"
    TOOL_FAILURE_ANALYZED = "tool.failure.analyzed"
    # tool streaming + cancellation (producer: Tool Streaming Runtime, M6.1B-4)
    # — additive vocabulary extension (no existing member changed; AR-32).
    TOOL_STREAM_STARTED = "tool.stream.started"
    TOOL_STREAM_CHUNK = "tool.stream.chunk"
    TOOL_STREAM_COMPLETED = "tool.stream.completed"
    TOOL_STREAM_CANCELLED = "tool.stream.cancelled"
    TOOL_STREAM_TIMED_OUT = "tool.stream.timed_out"
    # workflow / agent (producers: Workflow Runtime / Agent Runtime)
    WORKFLOW_STARTED = "workflow.started"
    WORKFLOW_STEP = "workflow.step"
    WORKFLOW_COMPLETED = "workflow.completed"
    AGENT_ACTIVATED = "agent.activated"
    AGENT_HANDOFF = "agent.handoff"
    # cost / metering (producer: Metering)
    COST_UPDATED = "cost.updated"


class EventSource(StrEnum):
    """The source-subsystem vocabulary — every event names the component that
    produced it (never who consumes it). Additive (AR-32)."""

    RUNTIME = "runtime"
    SESSION_WORKER = "session_worker"
    ORCHESTRATOR = "orchestrator"
    TURN_ENGINE = "turn_engine"
    VOICE_ACTIVITY = "voice_activity"
    ENDPOINT = "endpoint"
    INTERRUPT = "interrupt"
    SMART_TURN = "smart_turn"
    PIPELINE = "pipeline"
    PROVIDER_REGISTRY = "provider_registry"
    # AI Runtime module sources (M5.0 — additive; producers land in Epic 5)
    INTERACTION = "interaction"
    MEMORY = "memory"
    PROMPT = "prompt"
    KNOWLEDGE = "knowledge"
    CONTEXT = "context"
    REASONING = "reasoning"
    TOOL = "tool"
    MCP = "mcp"
    WORKFLOW = "workflow"
    AGENT = "agent"
    MODEL = "model"
    METERING = "metering"


@dataclass(frozen=True, slots=True)
class EventMetadata:
    """The publisher's binding context, stamped onto every event it emits. Carries
    the cross-cutting identifiers so producers only supply a type + payload. Frozen
    and cheap to copy — one per (subsystem, session)."""

    session_id: str
    source: EventSource
    org_id: str = ""
    conversation_id: str | None = None
    correlation_id: str | None = None


@dataclass(frozen=True, slots=True)
class Event:
    """The one envelope for every event, on the wire and in storage (§A5). Frozen +
    slotted: immutable, cheap, replay-friendly. Additive fields only (AR-32)."""

    id: str  # globally unique (uuid4-hex)
    type: EventType
    ts: float  # runtime clock, seconds
    seq: int  # strict, monotonic dispatch order (assigned by the bus)
    session_id: str
    source: EventSource
    org_id: str = ""
    conversation_id: str | None = None
    correlation_id: str | None = None
    turn: int | None = None  # conversation turn index, when known
    data: dict[str, Any] = field(default_factory=dict)  # versioned payload
    metadata: dict[str, Any] = field(default_factory=dict)  # free-form, non-payload


# The canonical name for the envelope (the M4.x event-platform vocabulary).
EventEnvelope = Event

# A subscriber receives one Event. Synchronous + deterministic (in-process bus).
# It must never raise into the dispatcher (the dispatcher isolates failures anyway).
EventHandler = Callable[[Event], None]

# A type filter: the set of EventTypes a subscriber wants (None → all).
EventTypeFilter = frozenset[EventType] | None


class EventBus(ABC):
    """Interface only. The in-process implementation lives in `events/` behind this
    contract (ADR-0006); a distributed broker may implement it later. Delivery is
    deterministic and ordered; a subscriber never blocks another (isolation)."""

    @abstractmethod
    def publish(self, event: Event) -> Event:
        """Enqueue one event for ordered fan-out to matching subscribers and return
        the delivered envelope (with its bus-assigned `seq`). Must not raise into the
        producer; a subscriber that publishes re-entrantly is queued (never
        recursively dispatched)."""

    @abstractmethod
    def subscribe(
        self, handler: EventHandler, *, types: EventTypeFilter = None
    ) -> Subscription:
        """Register a consumer (optionally filtered by event type). Returns a
        handle used to unsubscribe. Runtime-safe to call at any time."""

    @abstractmethod
    def unsubscribe(self, subscription: Subscription) -> None:
        """Remove a consumer. Runtime-safe; idempotent."""

    @abstractmethod
    def shutdown(self) -> None:
        """Stop accepting/dispatching events and drop all subscribers."""


@dataclass(frozen=True, slots=True)
class Subscription:
    """An opaque handle to one registration, returned by `EventBus.subscribe`."""

    id: int
