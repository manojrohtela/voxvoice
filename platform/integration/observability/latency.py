"""Routing-latency instrumentation for the composition root (M6.7B-2).

The one measurement Integration legitimately *owns*: the wall-clock overhead
`PipelineRoutedReasoningPort` adds by routing an Agent/Loop reasoning call
through `pipeline.RuntimePipeline` (design §17 risk 2 — "should be benchmarked
in M6.7B-1, not assumed free"). No capability owns this number, so measuring it
here duplicates no capability metric.

Wall time uses `time.perf_counter` deliberately — instrumentation measures real
elapsed time, independent of the platform's injected deterministic domain clock
(which stays the sole driver of *domain outputs* and ids). Measuring latency
never changes a domain result. `context_assembly_s` is read from the pipeline's
own already-computed per-stage timing (`UnifiedResponse.snapshot`), never
recomputed here.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RoutingLatencySample:
    """One reasoning-routing call's measured overhead."""

    total_s: float                 # wall time of the whole routed reasoning call
    context_assembly_s: float = 0.0  # pipeline's own CONTEXT-stage time (reference)


@dataclass(frozen=True, slots=True)
class RoutingLatencyStats:
    """Aggregate over all recorded samples."""

    count: int = 0
    total_s: float = 0.0
    min_s: float = 0.0
    max_s: float = 0.0
    context_assembly_total_s: float = 0.0

    @property
    def avg_s(self) -> float:
        return self.total_s / self.count if self.count else 0.0

    @property
    def avg_context_assembly_s(self) -> float:
        return self.context_assembly_total_s / self.count if self.count else 0.0

    @property
    def avg_routing_overhead_s(self) -> float:
        """Average time *not* spent in context assembly — the routing/adapter
        overhead plus the reasoning/provider call itself."""
        return (self.total_s - self.context_assembly_total_s) / self.count if self.count else 0.0


class LatencyRecorder:
    """A bounded ring buffer of routing-latency samples (M6.7.1 — F2). Not frozen
    — it is a live sink the reasoning port writes to; it holds no domain state
    and mints no ids. `maxlen` caps memory on a long-lived root; the oldest
    sample is dropped once full (deterministic, O(1) per record). `stats()`
    reports over the retained window; `total_recorded` is the lifetime count."""

    def __init__(self, maxlen: int = 4096) -> None:
        if maxlen < 1:
            raise ValueError("LatencyRecorder maxlen must be >= 1")
        self._samples: deque[RoutingLatencySample] = deque(maxlen=maxlen)
        self._total_recorded = 0

    def record(self, sample: RoutingLatencySample) -> None:
        self._samples.append(sample)
        self._total_recorded += 1

    @property
    def samples(self) -> tuple[RoutingLatencySample, ...]:
        return tuple(self._samples)

    @property
    def maxlen(self) -> int:
        return self._samples.maxlen or 0

    @property
    def total_recorded(self) -> int:
        """Lifetime count of samples recorded (may exceed the retained window)."""
        return self._total_recorded

    def stats(self) -> RoutingLatencyStats:
        if not self._samples:
            return RoutingLatencyStats()
        totals = [s.total_s for s in self._samples]
        return RoutingLatencyStats(
            count=len(totals),
            total_s=sum(totals),
            min_s=min(totals),
            max_s=max(totals),
            context_assembly_total_s=sum(s.context_assembly_s for s in self._samples),
        )
