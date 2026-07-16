"""Runtime-Plane contracts and value types (M2A).

Minimal, immutable-where-possible types the SessionWorker and future runtime
components share. Deliberately small — no fields are added in anticipation of
later milestones (KISS). Provider slots are typed with the `core.providers`
*interfaces*; the worker receives them injected and never instantiates them
(Constitution §A6, AR-20/21).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from core.providers import (
    AnalyticsProvider,
    LLMProvider,
    StorageProvider,
    STTProvider,
    TransportProvider,
    TTSProvider,
)

from .state import WorkerState


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


@dataclass(slots=True)
class RuntimeDependencies:
    """Injected capability providers (interfaces only) + metrics sink. All
    optional at M2A — no implementations exist yet. The worker stores these and
    hands them to future components; it never constructs a provider itself."""

    stt: STTProvider | None = None
    tts: TTSProvider | None = None
    llm: LLMProvider | None = None
    transport: TransportProvider | None = None
    storage: StorageProvider | None = None
    metrics: AnalyticsProvider | None = None


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    """The dependency-injection seam handed to a worker: what session it owns,
    which providers it may use, and its configuration. Future runtime components
    receive this rather than reaching for globals (AR-3/AR-4: no runtime globals)."""

    session: SessionContext
    dependencies: RuntimeDependencies
    config: WorkerConfiguration


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
