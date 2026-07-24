"""Expanded Tool Runtime statistics (M6.1B-3) — pure aggregation functions over
already-computed `ToolResult`/`ExecutionTrace` sequences. No I/O, no side effects.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ..domain import ToolResult
from .domain import ExecutionTrace


@dataclass(frozen=True, slots=True)
class LatencyDistribution:
    min_ms: float = 0.0
    max_ms: float = 0.0
    avg_ms: float = 0.0
    p50_ms: float = 0.0
    p95_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class ToolRuntimeStatistics:
    per_tool: dict[str, int] = field(default_factory=dict)
    per_classification: dict[str, int] = field(default_factory=dict)
    per_namespace: dict[str, int] = field(default_factory=dict)
    per_organization: dict[str, int] = field(default_factory=dict)
    replay_ratio: float = 0.0
    failure_ratio: float = 0.0
    latency: LatencyDistribution = field(default_factory=LatencyDistribution)


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    rank = max(0, min(len(sorted_values) - 1, round(pct * (len(sorted_values) - 1))))
    return sorted_values[rank]


def latency_distribution(traces: Sequence[ExecutionTrace]) -> LatencyDistribution:
    durations = sorted(t.duration_ms for t in traces)
    if not durations:
        return LatencyDistribution()
    return LatencyDistribution(
        min_ms=durations[0], max_ms=durations[-1],
        avg_ms=sum(durations) / len(durations),
        p50_ms=_percentile(durations, 0.50), p95_ms=_percentile(durations, 0.95),
    )


def compute_statistics(
    results: Sequence[ToolResult], *, traces: Sequence[ExecutionTrace] = (),
    replay_success: int = 0, replay_count: int = 0,
) -> ToolRuntimeStatistics:
    per_tool: dict[str, int] = {}
    per_classification: dict[str, int] = {}
    per_namespace: dict[str, int] = {}
    per_organization: dict[str, int] = {}
    failures = 0

    for r in results:
        qualified = f"{r.namespace}.{r.tool}"
        per_tool[qualified] = per_tool.get(qualified, 0) + 1
        per_classification[r.classification.value] = (
            per_classification.get(r.classification.value, 0) + 1
        )
        per_namespace[r.namespace] = per_namespace.get(r.namespace, 0) + 1
        org_key = r.org_id or "unknown"
        per_organization[org_key] = per_organization.get(org_key, 0) + 1
        if not r.success:
            failures += 1

    total = len(results)
    return ToolRuntimeStatistics(
        per_tool=per_tool, per_classification=per_classification, per_namespace=per_namespace,
        per_organization=per_organization,
        replay_ratio=(replay_success / replay_count) if replay_count else 0.0,
        failure_ratio=(failures / total) if total else 0.0,
        latency=latency_distribution(traces),
    )
