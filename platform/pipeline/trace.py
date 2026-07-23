"""Unified Execution Trace (M5.7) — one ordered trace spanning every stage.

Mirrors the established pattern (`reasoning.ReasoningExecutionTrace`,
`provider.ProviderExecutionTrace`): an ordered, seq-stamped, metadata-only event stream that
**never changes execution**. Each stage contributes exactly one completion event here (and
this module never re-derives or reinterprets a stage's own internal trace — Reasoning's and
Provider's richer traces are carried, unmodified, on `PipelineLineage`; see `lineage.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class PipelineStage(StrEnum):
    MEMORY = "memory"
    KNOWLEDGE = "knowledge"
    CONTEXT = "context"
    PROMPT = "prompt"
    REASONING = "reasoning"
    PROVIDER = "provider"
    COMPLETION = "completion"


@dataclass(frozen=True, slots=True)
class PipelineTraceEvent:
    stage: PipelineStage
    detail: str = ""
    seq: int = 0
    at: float = 0.0


@dataclass(frozen=True, slots=True)
class PipelineExecutionTrace:
    """Immutable; each stage appends exactly one event via `with_event`, in call order."""

    events: tuple[PipelineTraceEvent, ...] = ()

    def with_event(
        self, stage: PipelineStage, *, detail: str = "", at: float = 0.0
    ) -> PipelineExecutionTrace:
        ev = PipelineTraceEvent(stage=stage, detail=detail, seq=len(self.events), at=at)
        return PipelineExecutionTrace(events=(*self.events, ev))


@dataclass(frozen=True, slots=True)
class StageTiming:
    stage: PipelineStage
    duration_s: float = 0.0


@dataclass(frozen=True, slots=True)
class PipelineTimings:
    """Per-stage durations (for the observability snapshot). Immutable; appended in
    call order — never reordered, never recomputed from anything but wall-clock deltas
    the pipeline itself measured."""

    stages: tuple[StageTiming, ...] = field(default_factory=tuple)

    def with_stage(self, stage: PipelineStage, duration_s: float) -> PipelineTimings:
        return PipelineTimings(stages=(*self.stages, StageTiming(stage, duration_s)))

    @property
    def total_s(self) -> float:
        return sum(s.duration_s for s in self.stages)
