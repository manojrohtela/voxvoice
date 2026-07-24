"""Execution runtime statistics (M6.4B-2) — pure aggregation over
already-computed `WorkflowExecutionResult` sequences. No I/O, no side
effects.
"""

from __future__ import annotations

from collections.abc import Sequence

from .domain import WorkflowExecutionResult, WorkflowExecutionStatistics


def compute_statistics(results: Sequence[WorkflowExecutionResult]) -> WorkflowExecutionStatistics:
    per_final_state: dict[str, int] = {}
    failure_count = 0
    total_duration = 0.0
    total_nodes = 0

    for r in results:
        per_final_state[r.final_state.value] = per_final_state.get(r.final_state.value, 0) + 1
        if r.error:
            failure_count += 1
        total_duration += r.duration_ms
        total_nodes += len(r.completed_nodes)

    count = len(results)
    return WorkflowExecutionStatistics(
        per_final_state=per_final_state, execution_count=count, failure_count=failure_count,
        avg_duration_ms=(total_duration / count) if count else 0.0,
        avg_completed_nodes=(total_nodes / count) if count else 0.0,
    )
