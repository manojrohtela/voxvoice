"""Hook observability (M6.3B) — a pure `HookRunResult` projection, plus a
concrete, in-memory default implementation of `HookObservabilitySink`.
"planning-free": this trace records only that a run happened and its
aggregate shape — it never records or infers a plan, since the Hook
Framework never plans anything (that remains Skill/Agent Runtime's concern).
"""

from __future__ import annotations

from .domain import HookEvent, HookExecutionTrace, HookRunResult
from .extensions import HookObservabilitySink
from .ids import trace_id_for


def trace_for(result: HookRunResult, event: HookEvent, *, now: float = 0.0) -> HookExecutionTrace:
    return HookExecutionTrace(
        trace_id=trace_id_for(event.event_id), event_id=event.event_id,
        point=result.point.value, scope=result.scope.value,
        hook_count=len(result.verdicts), failure_count=len(result.failures),
        aggregated_outcome=result.aggregated_outcome.value, created_at=now,
    )


class InMemoryHookObservabilitySink(HookObservabilitySink):
    """The default, in-memory recorder — never a real telemetry backend."""

    def __init__(self) -> None:
        self._records: list[object] = []

    def record(self, event: object) -> None:
        self._records.append(event)

    def all(self) -> tuple[object, ...]:
        return tuple(self._records)

    def count(self) -> int:
        return len(self._records)
