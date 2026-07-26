"""Platform telemetry aggregation (M6.7B-2) — references + trace_id correlation.

Aggregates *references* to each capability's own execution trace / statistics
and correlates them by the shared `trace_id`. It never constructs a capability
span nor recomputes a metric (design §14).

**Dependency note (Bundle 2 rules):** `integration` may not import the
standalone top-level `replay/` package or `events/` — so `PlatformCorrelation`
does not call `replay.EventCorrelator`. Cross-capability *raw-event* correlation
remains that separate edge platform's job (design §13); Integration's own
responsibility is only to keep `trace_id` consistent and to correlate its own
aggregated *references* by that id. This is not a second event bus or a
reimplementation of the correlator — different input (integration-owned
references, not `core.events.Event`s), zero new telemetry.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .latency import RoutingLatencyStats


@dataclass(frozen=True, slots=True)
class PlatformSpan:
    """A reference to one capability's own execution trace — never a span this
    layer owns or times. `ref` is the capability's own trace/result object."""

    trace_id: str
    capability: str            # "agent" | "workflow" | "loop" | "tool" | "skill" | ...
    name: str
    ref: Any = None            # the capability's own trace/result object (by reference)
    status: str = ""


@dataclass(frozen=True, slots=True)
class PlatformTrace:
    """One platform execution's worth of capability spans, all sharing one
    `trace_id`. Aggregation only — the spans reference each capability's own
    already-emitted trace."""

    trace_id: str
    spans: tuple[PlatformSpan, ...] = ()

    def with_span(self, span: PlatformSpan) -> PlatformTrace:
        return PlatformTrace(trace_id=self.trace_id, spans=(*self.spans, span))

    @property
    def capabilities(self) -> tuple[str, ...]:
        return tuple(s.capability for s in self.spans)


@dataclass(frozen=True, slots=True)
class PlatformMetrics:
    """References to each capability's own `*Statistics`, plus the one metric
    Integration owns (reasoning-routing latency). No metric is recomputed."""

    trace_id: str
    agent_statistics: Any = None
    workflow_statistics: Any = None
    tool_statistics: Any = None
    skill_statistics: Any = None
    routing_latency: RoutingLatencyStats | None = None
    references: dict[str, Any] = field(default_factory=dict)


class PlatformCorrelation:
    """Correlates Integration-owned telemetry references by `trace_id`. Not a
    second correlator over `core.events` — it groups only the reference objects
    Integration itself aggregated (design §14)."""

    @staticmethod
    def group_spans(spans: Sequence[PlatformSpan]) -> dict[str, tuple[PlatformSpan, ...]]:
        grouped: dict[str, list[PlatformSpan]] = {}
        for span in spans:
            grouped.setdefault(span.trace_id, []).append(span)
        return {tid: tuple(items) for tid, items in grouped.items()}

    @staticmethod
    def trace_of(spans: Sequence[PlatformSpan], trace_id: str) -> PlatformTrace:
        """Build the single `PlatformTrace` for one `trace_id` (the whole
        platform-wide execution, correlated by the one shared id)."""
        return PlatformTrace(
            trace_id=trace_id,
            spans=tuple(s for s in spans if s.trace_id == trace_id),
        )
