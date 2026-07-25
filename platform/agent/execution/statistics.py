"""Agent execution statistics (M6.5B-3) — pure aggregation over
already-finished `AgentGoalResult` sequences. No I/O, no side effects.
"""

from __future__ import annotations

from collections.abc import Sequence

from .domain import AgentExecutionStatistics, AgentGoalResult


def compute_statistics(results: Sequence[AgentGoalResult]) -> AgentExecutionStatistics:
    per_final_state: dict[str, int] = {}
    failure_count = 0

    for r in results:
        key = r.final_state.value
        per_final_state[key] = per_final_state.get(key, 0) + 1
        if r.error:
            failure_count += 1

    return AgentExecutionStatistics(
        per_final_state=per_final_state, execution_count=len(results), failure_count=failure_count,
    )
