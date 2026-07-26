"""IntegrationDiagnostics (M6.7B-1, extended M6.7B-2) — aggregates *references*
only.

Per the M6.7A design §14: a pure aggregation of *references* to each
capability's own already-certified diagnostics/statistics builder output —
never a recomputation, never a new metric. No new counter or gauge is
introduced anywhere in this layer; every number already exists in some
capability's own `*Statistics`/`*Diagnostics` object (§14 "Avoiding duplicate
telemetry").

Bundle 2 adds Loop to the aggregation. Loop Engine is a pure library with no
runtime instance (ADR-0016), so its diagnostics is a `LoopDiagnostics`
reference the composition root builds from a `LoopResult` (via Loop Engine's own
`loop.build_diagnostics`) and passes in — never recomputed here. Where a
capability exposes only `statistics()` rather than `diagnostics()` (Skill/Tool),
that already-built statistics object is referenced instead — still aggregation,
never recomputation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class IntegrationDiagnostics:
    """A correlation-only view: the shared `trace_id` plus references to each
    capability's own diagnostics/statistics object (design §14). Typed `Any` so
    this shape never couples to a capability's concrete diagnostics class."""

    trace_id: str
    agent_diagnostics: Any = None       # agent.execution AgentExecutionDiagnostics
    workflow_diagnostics: Any = None    # workflow.execution WorkflowExecutionDiagnostics
    loop_diagnostics: Any = None        # loop.LoopDiagnostics (reference; no loop runtime)
    tool_diagnostics: Any = None        # tool ToolStatistics (reference)
    skill_diagnostics: Any = None       # skill SkillStatistics (reference)
    healthy: bool = True


def build_diagnostics(
    *,
    trace_id: str,
    agent_execution: Any = None,
    workflow_execution: Any = None,
    loop_diagnostics: Any = None,
    tool_runtime: Any = None,
    skill_runtime: Any = None,
    healthy: bool = True,
) -> IntegrationDiagnostics:
    """Aggregate references to each capability's own diagnostics/statistics.

    Each capability's own builder is called through its public facade only;
    absent capabilities yield `None` references, never a fabricated value.
    `loop_diagnostics` is a pre-built reference (Loop has no runtime facade)."""
    return IntegrationDiagnostics(
        trace_id=trace_id,
        agent_diagnostics=_first(agent_execution, "diagnostics", "statistics"),
        workflow_diagnostics=_first(workflow_execution, "diagnostics", "statistics"),
        loop_diagnostics=loop_diagnostics,
        tool_diagnostics=_first(tool_runtime, "diagnostics", "statistics"),
        skill_diagnostics=_first(skill_runtime, "diagnostics", "statistics"),
        healthy=healthy,
    )


def _first(runtime: Any, *methods: str) -> Any:
    """Reference the first zero-arg builder a capability actually exposes; never
    recompute or synthesize its contents (design §14). A builder requiring
    arguments (e.g. `statistics(results)`) is skipped in favour of a
    materializable one (e.g. `diagnostics()`)."""
    if runtime is None:
        return None
    for method in methods:
        builder = getattr(runtime, method, None)
        if callable(builder):
            try:
                return builder()
            except TypeError:
                continue
    return None
