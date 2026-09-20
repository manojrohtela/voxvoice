"""The seam that connects a running conversation to the frozen Epic 10 spine (M11.12).

Epic 10 built a durable event log, a metric producer and a metering buffer, and then **stopped**:
nothing in the runtime ever supplied them. M11.12 supplies them, and this module is the whole of
the connection — one value, carried on the worker configuration, that the Orchestrator consults
when it builds a session's Event Bus.

```
ConversationOrchestrator
        │  builds one InProcessEventBus per session
        ▼
  ObservabilityPlane ─┬─► durable: event log              (the Epic 10 persistence plane)
                      ├─► metrics: MetricStreamSink       (the platform metric buffer)
                      └─► telemetry: the metric producer  (the runtime observability plane)
```

## Why a value object rather than a manager

Because Epic 10 already owns every mechanism here, and a class that *did* something would be the
second event bus, the second telemetry system or the second metering path the milestone forbids.
This carries references and answers one question — *"is this plane wired?"* — and the Orchestrator
does the wiring with Epic 10's own constructors.

## Why it is optional in every part

A deployment may want durable events without metering, or telemetry without either. Each field
defaults to `None`, absent means *not wired*, and an unwired plane is exactly the behaviour every
milestone up to M11.11 had. `MetricStreamPort`'s own docstring sets the rule this follows: *"An
unwired port means no ingestion, not invented usage."*

## Ownership

`runtime/` may import `core` and its own packages and **no other plane** — so this module names
none of them, not even in prose: the attachments are structural Protocols (an opaque object
satisfying Epic 10's append port; a metric sink as anything with `subscribe`), and the composition
root, which is allowed to see every plane, supplies the concrete objects. That is the same
arrangement `LatencyObserver` already uses for the runtime's metric producer.

Naming a storage class here — even in a comment — is what the frozen boundary guards forbid, and
they are right to: a runtime that knows the name of a table-backed sink is one refactor away from
importing it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class DurableEventSink(Protocol):
    """Epic 10's append-only event log, named structurally.

    Only the one method the bus calls is declared, so the concrete Epic 10 sink satisfies this
    as-is — no adapter, and no second store."""

    def append(self, event: object) -> bool:
        """Append one envelope. Idempotent on `Event.id` (ADR-0032 ED2)."""


@runtime_checkable
class MetricStreamSink(Protocol):
    """The platform-side metric buffer, named structurally.

    It subscribes *itself* to the bus, which is why the only method here is `subscribe` — the
    runtime never decodes a metric, never builds a signal, and never reaches the commercial
    plane."""

    def subscribe(self, bus: object) -> object:
        """Register for `metric.turn` on `bus`, returning the bus's own subscription handle."""


@runtime_checkable
class TelemetryFactory(Protocol):
    """Builds the per-session latency observer from a source-bound publish handle.

    A factory rather than an instance because `TurnLatencyTap` holds per-session turn state: one
    tap per session, created when the session's bus exists, discarded with it."""

    def __call__(self, publisher: object) -> object: ...


@dataclass(frozen=True, slots=True)
class ObservabilityPlane:
    """Which Epic 10 facilities a session is wired to. Absent fields mean *not wired*.

    Frozen and slotted: it is configuration, decided once at the composition root, and a plane
    that could be mutated mid-session would make "which events were durable" unanswerable."""

    #: The append-only event log the session's bus writes through (ADR-0035 DW2).
    durable: DurableEventSink | None = None
    #: Called exactly once per failed persistent write, by the bus, off the producer's stack.
    #: The bus never raises into a producer (AR-31) — this is the operator's only signal.
    on_durable_error: object | None = None
    #: The platform metric buffer the commercial plane later pulls from (ADR-0039 MB1).
    metrics: MetricStreamSink | None = None
    #: Builds the `metric.turn` producer for the session (ADR-0038 TP2).
    telemetry: TelemetryFactory | None = None

    @property
    def is_wired(self) -> bool:
        """Whether anything at all is connected — what a test and an operator both ask first."""
        return any((self.durable, self.metrics, self.telemetry))


#: The default: nothing wired, which is exactly what every milestone before M11.12 had.
UNWIRED = ObservabilityPlane()


__all__ = [
    "UNWIRED",
    "DurableEventSink",
    "MetricStreamSink",
    "ObservabilityPlane",
    "TelemetryFactory",
]
