"""Execution observability (M6.4B-2) — a pure `WorkflowExecutionResult`
projection, plus a concrete, in-memory default implementation of Bundle 1's
`WorkflowObservabilitySink`.

Learning from Hook Framework's own M6.3C certification finding (the shipped
`InMemoryHookObservabilitySink` was never actually wired into `HookRuntime`'s
recording path), `WorkflowExecutionRuntime` (`runtime.py`) calls this sink
directly, in addition to writing the trace to the durable repository --
closing that exact gap proactively rather than repeating it.
"""

from __future__ import annotations

from ..extensions import WorkflowObservabilitySink
from .domain import WorkflowExecutionResult, WorkflowExecutionTrace
from .ids import trace_id_for


def trace_for(result: WorkflowExecutionResult, *, now: float = 0.0) -> WorkflowExecutionTrace:
    return WorkflowExecutionTrace(
        trace_id=trace_id_for(result.execution_id), execution_id=result.execution_id,
        final_state=result.final_state.value, node_count=len(result.completed_nodes),
        failure_count=1 if result.error else 0, duration_ms=result.duration_ms, created_at=now,
    )


class InMemoryWorkflowObservabilitySink(WorkflowObservabilitySink):
    """The default, in-memory recorder — never a real telemetry backend."""

    def __init__(self) -> None:
        self._records: list[object] = []

    def record(self, event: object) -> None:
        self._records.append(event)

    def all(self) -> tuple[object, ...]:
        return tuple(self._records)

    def count(self) -> int:
        return len(self._records)
