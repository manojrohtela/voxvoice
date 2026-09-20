"""Runtime-Plane contracts and value types (M2A; consolidated E2.1).

Minimal, immutable-where-possible types the SessionWorker and runtime components
share. Deliberately small (KISS). Provider provisioning is the sole
responsibility of the `ProviderRegistry` — the runtime resolves capabilities from
`RuntimeContext.registry` and never constructs a provider (Constitution §A6,
AR-20/21). The former `RuntimeDependencies` direct-injection seam was removed at
E2.1 as duplicated responsibility (DL-0011).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from core.audio import DEFAULT_SAMPLE_RATE

from .observability_plane import UNWIRED, ObservabilityPlane
from .state import WorkerState

if TYPE_CHECKING:
    from .registry import ProviderRegistry
    from .smart_turn import SmartTurnPolicy

# A transcript sink receives (session_id, role, text, is_final). The Control
# Plane provides one; the future Event Bus (M4) subsumes it.
TranscriptSink = Callable[[str, str, str, bool], None]


class GreetingState(StrEnum):
    """What the runtime will do with the agent's `first_message` (M11.4, AR-25).

    `first_message` has been accepted by the Control Plane since M2C.2 and **read by nothing** —
    no greeting has ever been played. AR-25 says accepted configuration must act or the write must
    be rejected, and GOVERNANCE §4 calls silent acceptance a release blocker.

    M11.4 cannot *play* it: **D-11** assigns the greeting to M11.10's Playback Scheduler and its
    §A8 greeting pre-cache, and this milestone is explicitly forbidden from implementing playback.
    So the field is given an explicit, readable lifecycle instead of a silence. `PENDING_PLAYBACK`
    is an honest statement — *"configured, validated, understood, not yet spoken"* — and it is
    deliberately **not** a claim that audio was produced. M11.10 replaces it with a played state;
    until then a caller can ask and get a true answer."""

    #: No greeting configured.
    ABSENT = "absent"
    #: A greeting is configured and validated; playback lands with the Playback Scheduler (M11.10).
    PENDING_PLAYBACK = "pending_playback"


@runtime_checkable
class LatencyObserver(Protocol):
    """The per-turn latency observation seam (M10.9, ADR-0038 TP2).

    Declared here, beside `TranscriptSink` and `LifecycleObserver`, for the reason those exist:
    the pipeline must be observable **without knowing who observes it**. `platform/observability/`
    satisfies this **structurally** — so `runtime/` imports no `observability`, `observability/`
    imports no `runtime`, and the two meet only at a composition root.

    Notify-only and best-effort. An implementation **must never raise into the pipeline, never
    block, and never alter conversation behaviour** (ADR-0033 MS8, ADR-0038 TP9); the pipeline
    isolates it anyway, on the `_tap_voice_activity` / `_emit` precedent (DL-0013).
    """

    def record(
        self, session_id: str, turn_index: int, boundary: str, *, backdate_ms: float = 0.0
    ) -> None:
        """One stage boundary was reached, now. `boundary` is one of the six AR-42 names
        (ADR-0038 TP3), the `playback_start` lifecycle marker, or `speech_start` (M11.8).

        `backdate_ms` says how much audio ago the boundary really was, for boundaries recognised
        only after their evidence accrued (endpointing). The observer subtracts it from its own
        clock, so the clock stays in one place. Optional and additive: an observer that ignores it
        behaves exactly as before."""

    def complete(self, session_id: str, turn_index: int, status: str) -> None:
        """The turn reached a terminal outcome — `"completed"`, `"cancelled"` or
        `"interrupted"`. The observer emits at most one metric here and releases its state."""


@dataclass(frozen=True, slots=True)
class SessionContext:
    """Identity and consumer-supplied context for the one session a worker owns."""

    session_id: str
    org_id: str
    agent_id: str
    channel: str = "web"
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkerConfiguration:
    """Worker-level lifecycle configuration."""

    worker_id: str
    startup_timeout_s: float = 10.0
    shutdown_timeout_s: float = 10.0
    #: Whether the session speaks through the M11.10 playback path — clause-by-clause synthesis
    #: into the Playback Engine — instead of the legacy whole-utterance `synthesize()` call
    #: (M11.11, ADR-0048 PB1/PB2).
    #:
    #: It lives on the **worker** configuration rather than on `AgentSpec` deliberately. This is a
    #: property of how the runtime is composed, not of the agent a customer configured: the same
    #: agent must behave identically whichever deployment runs it, and adding an agent-visible
    #: field would put a runtime-composition detail on the public Control Plane contract.
    #:
    #: Default **False**, so every existing caller keeps the M11.9 response path exactly.
    streaming_playback: bool = False
    #: Which Epic 10 facilities this session's events, telemetry and metering are wired to
    #: (M11.12). Defaults to `UNWIRED` — nothing connected, exactly as every milestone before
    #: M11.12. Like `streaming_playback` this is a **composition** choice, not an agent one.
    observability: ObservabilityPlane = UNWIRED


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """The agent's runtime-relevant configuration, passed from the Control Plane
    into the Runtime (built from the persisted Agent). Provider *names* are opaque
    capability selectors resolved via the registry — never vendor branches."""

    system_prompt: str
    first_message: str | None = None
    language: str = "en-IN"
    stt_provider: str = "default"
    tts_provider: str = "default"
    tts_voice: str | None = None
    llm_model: str | None = None
    # The session's audio sample rate in Hz (M11.2, ADR-0044 AD3), carried from
    # `StartSessionRequest`. The Orchestrator passes it to `RuntimePipeline`, which
    # requires it — so no runtime stage assumes a rate (AR-41).
    sample_rate: int = DEFAULT_SAMPLE_RATE
    #: What the runtime intends to do with `first_message` (M11.4). AR-25 forbids leaving an
    #: accepted field silently ignored, and D-11 assigns the *playback* to M11.10's Playback
    #: Scheduler — so the honest M11.4 position is neither "played" nor "ignored" but **declared
    #: pending**, with a state a caller and a test can both read. See `GreetingState`.
    greeting_state: GreetingState = GreetingState.ABSENT
    # Interrupt Intelligence (M3.4): opt-in barge-in. When True AND a Voice Activity
    # provider is registered, the pipeline runs the interruptible playback path;
    # otherwise assistant playback is uninterruptible (exact M3.3 behaviour).
    allow_interrupt: bool = False
    # Audio-driven endpointing (M11.8, ADR-0047 ME1), carried from `StartSessionRequest`. When
    # True the Orchestrator wires an `AudioEndpointDetector` and switches the endpoint policy to
    # audio-driven, so a transcript segment no longer ends the turn.
    audio_endpointing: bool = False
    # Smart Turn Intelligence (M3.5): opt-in adaptive/semantic timing. When True the
    # pipeline routes endpoint/interrupt decisions through the Smart Turn engine
    # (arbitration + adaptive policy + optional registered semantic evaluator).
    # Under the default identity policy behaviour is unchanged. `smart_turn_policy`
    # supplies the adaptive knobs (None → the default identity policy).
    smart_turn: bool = False
    smart_turn_policy: SmartTurnPolicy | None = None


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    """The dependency-injection seam handed to a worker: what session it owns, its
    configuration, and how it resolves providers. Runtime components receive this
    rather than reaching for globals (AR-3/AR-4: no runtime globals).

    `registry` (Constitution §A4.1) is the single mechanism by which the Runtime
    resolves provider capabilities. `agent` carries the agent's runtime config.
    `on_transcript` is the Control Plane's transcript sink. `registry`/`agent` are
    optional: without them a worker runs an idle conversation (no pipeline) — used
    by lifecycle-only tests."""

    session: SessionContext
    config: WorkerConfiguration
    registry: ProviderRegistry | None = None
    agent: AgentSpec | None = None
    on_transcript: TranscriptSink | None = None


@dataclass(frozen=True, slots=True)
class RuntimeMetadata:
    """An immutable snapshot of a worker's runtime state, safe to expose."""

    worker_id: str
    session_id: str
    state: WorkerState
    created_at: float
    started_at: float | None
    stopped_at: float | None


@dataclass(frozen=True, slots=True)
class LifecycleSignal:
    """One lifecycle observability signal (M2A). Not an Event Bus event — the
    Event Bus is M4. A future adapter can bridge these into `core.events`."""

    worker_id: str
    session_id: str
    state: WorkerState
    ts: float
    detail: str | None = None


# A lifecycle observer is a synchronous notification sink (logging, or later an
# Event Bus bridge). It must never raise into the worker.
LifecycleObserver = Callable[[LifecycleSignal], None]
