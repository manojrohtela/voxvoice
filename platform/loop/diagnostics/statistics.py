"""Loop Engine runtime statistics (M6.6B-2) — pure aggregation over
already-finished `LoopResult` sequences. No I/O, no side effects. Mirrors
`agent.execution.statistics.compute_statistics`'s shape.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..core.domain import LoopResult, LoopStatistics


def compute_statistics(results: Sequence[LoopResult]) -> LoopStatistics:
    per_stop_reason: dict[str, int] = {}
    total_iterations = 0
    converged_count = 0

    for r in results:
        per_stop_reason[r.stop_reason] = per_stop_reason.get(r.stop_reason, 0) + 1
        total_iterations += r.iteration_count
        if r.converged:
            converged_count += 1

    count = len(results)
    return LoopStatistics(
        loop_count=count,
        avg_iteration_count=(total_iterations / count) if count else 0.0,
        per_stop_reason=per_stop_reason,
        convergence_rate=(converged_count / count) if count else 0.0,
    )
