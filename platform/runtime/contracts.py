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
from typing import TYPE_CHECKING

from .state import WorkerState

if TYPE_CHECKING:
    from .registry import ProviderRegistry

# A transcript sink receives (session_id, role, text, is_final). The Control
# Plane provides one; the future Event Bus (M4) subsumes it.
TranscriptSink = Callable[[str, str, str, bool], None]


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
