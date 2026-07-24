"""Hook runtime statistics (M6.3B) — pure aggregation over already-computed
`HookRunResult` sequences. No I/O, no side effects.
"""

from __future__ import annotations

from collections.abc import Sequence

from .domain import HookRunResult, HookRuntimeStatistics


def compute_statistics(results: Sequence[HookRunResult]) -> HookRuntimeStatistics:
    per_point: dict[str, int] = {}
    per_scope: dict[str, int] = {}
    per_outcome: dict[str, int] = {}
    failure_count = 0

    for r in results:
        per_point[r.point.value] = per_point.get(r.point.value, 0) + 1
        per_scope[r.scope.value] = per_scope.get(r.scope.value, 0) + 1
        per_outcome[r.aggregated_outcome.value] = per_outcome.get(r.aggregated_outcome.value, 0) + 1
        failure_count += len(r.failures)

    return HookRuntimeStatistics(
        per_point=per_point, per_scope=per_scope, per_outcome=per_outcome,
        run_count=len(results), failure_count=failure_count,
    )
