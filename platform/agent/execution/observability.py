"""Execution observability (M6.5B-3) — a pure `AgentGoalResult` projection,
plus a concrete, in-memory default implementation of Bundle 1's
`AgentObservabilitySink`.

Learning from Hook Framework's own M6.3C certification finding (the shipped
`InMemoryHookObservabilitySink` was never actually wired into `HookRuntime`'s
recording path) — and Workflow Runtime's own proactive fix of the same gap
in M6.4B-2 — `AgentExecutionRuntime` (`runtime.py`) calls this sink
directly, in addition to writing the trace to the repository.
"""

from __future__ import annotations

from ..extensions import AgentObservabilitySink
from .domain import AgentExecutionTrace, AgentGoalResult
from .ids import trace_id_for


def trace_for(
    result: AgentGoalResult, *, decision_count: int = 0, now: float = 0.0,
) -> AgentExecutionTrace:
    return AgentExecutionTrace(
        trace_id=trace_id_for(result.execution_id), execution_id=result.execution_id,
        final_state=result.final_state.value, decision_count=decision_count,
        failure_count=1 if result.error else 0, created_at=now,
    )


class InMemoryAgentObservabilitySink(AgentObservabilitySink):
    """The default, in-memory recorder — never a real telemetry backend."""

    def __init__(self) -> None:
        self._records: list[object] = []

    def record(self, event: object) -> None:
        self._records.append(event)

    def all(self) -> tuple[object, ...]:
        return tuple(self._records)

    def count(self) -> int:
        return len(self._records)
