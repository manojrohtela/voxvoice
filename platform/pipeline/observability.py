"""Pipeline observability (M5.7) — one execution snapshot. No dashboards.

`PipelineExecutionSnapshot` is a pure read-side aggregate produced once per execution: stage
timings, the unified execution trace, a lineage summary, the provider/model actually used,
usage, and streaming statistics — all values already computed by the stages themselves or by
the pipeline's own timers, never recomputed or estimated here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .lineage import PipelineLineage
from .trace import PipelineExecutionTrace, PipelineTimings


class Metric:
    STAGE_COMPLETED = "pipeline.stage.completed"
    STAGE_FAILED = "pipeline.stage.failed"
    CANCELLED = "pipeline.cancelled"
    EXECUTIONS = "pipeline.executions"
    TOTAL_LATENCY = "pipeline.latency"


@dataclass(frozen=True, slots=True)
class PipelineExecutionSnapshot:
    request_id: str
    trace_id: str
    timings: PipelineTimings = field(default_factory=PipelineTimings)
    trace: PipelineExecutionTrace = field(default_factory=PipelineExecutionTrace)
    lineage: PipelineLineage = field(default_factory=PipelineLineage)
    provider: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    streamed: bool = False
    chunk_count: int = 0
