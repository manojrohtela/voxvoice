"""ToolRuntimeMetrics (M6.1B-3) — in-memory counters only. No Prometheus, no
OpenTelemetry, no exporters (those are additive extension interfaces —
`extensions.py`). Mirrors the basic-counter posture already established for
`tool.observability.legacy.Metric` and `governance.observability.Metric`.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain import ToolResult
from .domain import ReplayComparison


@dataclass(frozen=True, slots=True)
class LatencySample:
    tool: str
    duration_ms: float


class ToolRuntimeMetrics:
    def __init__(self) -> None:
        self._executions = 0
        self._success = 0
        self._failure = 0
        self._cancelled = 0  # reserved — no cancellation path exists yet
        self._replay_count = 0
        self._replay_success = 0
        self._replay_mismatch = 0
        self._approval_count = 0
        self._policy_count = 0
        self._authorization_count = 0
        self._latencies: list[float] = []
        self._tool_usage: dict[str, int] = {}
        self._classification_usage: dict[str, int] = {}

    def record_execution(self, result: ToolResult, *, duration_ms: float = 0.0) -> None:
        self._executions += 1
        if result.success:
            self._success += 1
        else:
            self._failure += 1
        self._latencies.append(duration_ms)
        qualified = f"{result.namespace}.{result.tool}"
        self._tool_usage[qualified] = self._tool_usage.get(qualified, 0) + 1
        classification = result.classification.value
        self._classification_usage[classification] = (
            self._classification_usage.get(classification, 0) + 1
        )

    def record_replay(self, comparison: ReplayComparison) -> None:
        self._replay_count += 1
        if comparison.matches:
            self._replay_success += 1
        else:
            self._replay_mismatch += 1

    def record_governance(
        self, *, policy: bool = False, authorization: bool = False, approval: bool = False
    ) -> None:
        if policy:
            self._policy_count += 1
        if authorization:
            self._authorization_count += 1
        if approval:
            self._approval_count += 1

    @property
    def average_latency_ms(self) -> float:
        return sum(self._latencies) / len(self._latencies) if self._latencies else 0.0

    @property
    def max_latency_ms(self) -> float:
        return max(self._latencies) if self._latencies else 0.0

    def snapshot(self) -> dict[str, object]:
        return {
            "executions": self._executions, "success": self._success, "failure": self._failure,
            "cancelled": self._cancelled, "replay_count": self._replay_count,
            "replay_success": self._replay_success, "replay_mismatch": self._replay_mismatch,
            "approval_count": self._approval_count, "policy_count": self._policy_count,
            "authorization_count": self._authorization_count,
            "average_latency_ms": self.average_latency_ms, "max_latency_ms": self.max_latency_ms,
            "tool_usage": dict(self._tool_usage),
            "classification_usage": dict(self._classification_usage),
        }
