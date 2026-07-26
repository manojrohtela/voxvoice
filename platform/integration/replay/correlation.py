"""Cross-capability replay correlation (M6.7B-2) — trace_id keyed references."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ReplayArtifact:
    """A reference to one capability's own replay artifact — an *id*, never the
    record itself (the owning capability keeps that). Correlated by `trace_id`."""

    trace_id: str
    capability: str        # "agent" | "workflow" | "loop" | "skill" | "tool"
    kind: str              # e.g. "agent_replay" | "workflow_execution" | "loop_replay"
    ref_id: str            # the owning capability's own id for the artifact


@dataclass(frozen=True, slots=True)
class PlatformReplayQuery:
    """A correlation query — by `trace_id` only (design §13)."""

    trace_id: str


@dataclass(frozen=True, slots=True)
class ReplayValidationRef:
    """The result of delegating validation of one artifact to its owning
    engine. `comparison` is that engine's own `ReplayComparison` — by
    reference, never recomputed here."""

    artifact: ReplayArtifact
    comparison: Any
    validated: bool


class PlatformReplayIndex:
    """An Integration-owned index mapping `trace_id -> capability replay
    artifact references`. Populated by the composition root as it drives an
    execution; holds ids only, never capability state.

    Bounded (M6.7.1 — F2): `max_traces` caps the number of distinct trace_ids
    retained. When a *new* trace would exceed the cap, the least-recently-used
    trace is evicted (deterministic, O(1)). Recording under, or reading, an
    existing trace marks it most-recently-used. Per-trace artifact lists are
    capped at `max_per_trace` to bound a pathological single-trace producer."""

    def __init__(self, max_traces: int = 1024, *, max_per_trace: int = 256) -> None:
        if max_traces < 1 or max_per_trace < 1:
            raise ValueError("PlatformReplayIndex bounds must be >= 1")
        self._by_trace: OrderedDict[str, list[ReplayArtifact]] = OrderedDict()
        self._max_traces = max_traces
        self._max_per_trace = max_per_trace

    def record(self, trace_id: str, capability: str, kind: str, ref_id: str) -> ReplayArtifact:
        artifact = ReplayArtifact(
            trace_id=trace_id, capability=capability, kind=kind, ref_id=ref_id
        )
        entries = self._by_trace.get(trace_id)
        if entries is None:
            if len(self._by_trace) >= self._max_traces:
                self._by_trace.popitem(last=False)  # evict LRU trace
            entries = []
            self._by_trace[trace_id] = entries
        entries.append(artifact)
        if len(entries) > self._max_per_trace:
            del entries[0]  # ring-drop oldest artifact for this trace
        self._by_trace.move_to_end(trace_id)  # mark most-recently-used
        return artifact

    def entries_for(self, trace_id: str) -> tuple[ReplayArtifact, ...]:
        return tuple(self._by_trace.get(trace_id, ()))

    def trace_ids(self) -> tuple[str, ...]:
        return tuple(self._by_trace.keys())


class PlatformReplayCorrelation:
    """Correlates replay artifacts by `trace_id` and, on request, delegates
    validation to each artifact's **owning** engine — never a new replay engine,
    never replaying another capability's state (design §13)."""

    def __init__(self, index: PlatformReplayIndex | None = None) -> None:
        self._index = index or PlatformReplayIndex()

    @property
    def index(self) -> PlatformReplayIndex:
        return self._index

    def correlate(self, query: PlatformReplayQuery) -> tuple[ReplayArtifact, ...]:
        """Every capability's replay artifact sharing this `trace_id` — pure
        reference lookup, zero capability calls."""
        return self._index.entries_for(query.trace_id)

    def validate(
        self,
        query: PlatformReplayQuery,
        *,
        validators: Mapping[str, Callable[[str], Any]],
    ) -> tuple[ReplayValidationRef, ...]:
        """Delegate validation of each correlated artifact to its owning
        capability's own validate-by-id callable (e.g.
        `AgentExecutionRuntime.validate_replay`). Integration performs no replay
        itself; an artifact whose capability has no registered validator is
        skipped (never replayed here)."""
        results: list[ReplayValidationRef] = []
        for artifact in self._index.entries_for(query.trace_id):
            validator = validators.get(artifact.capability)
            if validator is None:
                continue
            comparison = validator(artifact.ref_id)
            results.append(ReplayValidationRef(
                artifact=artifact,
                comparison=comparison,
                validated=bool(getattr(comparison, "matches", False)),
            ))
        return tuple(results)
