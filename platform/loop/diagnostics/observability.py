"""Loop Engine observability (M6.6B-2) — a pure `LoopResult` projection.
Unlike Agent/Workflow's own observability (which ship a concrete
`InMemory*ObservabilitySink` wired into a live runtime facade), Loop Engine
has no runtime facade to wire a sink into (design §8.5) — `trace_for` is a
plain builder function a caller invokes over its own already-produced
`LoopResult`, the same "observability as a pure projection" role
`agent.execution.observability.trace_for` plays, minus the sink.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.domain import LoopResult
from ..core.ids import content_hash


@dataclass(frozen=True, slots=True)
class LoopExecutionTrace:
    trace_id: str
    loop_id: str
    iteration_count: int
    stop_reason: str
    converged: bool
    critique_count: int
    repair_count: int
    created_at: float = 0.0


def trace_for(result: LoopResult, *, now: float = 0.0) -> LoopExecutionTrace:
    critique_count = sum(1 for it in result.iterations if it.critique is not None)
    repair_count = sum(1 for it in result.iterations if it.repair is not None)
    return LoopExecutionTrace(
        trace_id="lt_" + content_hash(result.loop_id, now, "trace"), loop_id=result.loop_id,
        iteration_count=result.iteration_count, stop_reason=result.stop_reason,
        converged=result.converged, critique_count=critique_count, repair_count=repair_count,
        created_at=now,
    )
