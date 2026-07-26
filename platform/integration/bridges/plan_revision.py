"""PlanRevisionCoordinator (M6.7B-2) — the Agent↓Loop↓Reasoning revision path,
wired with real runtimes.

Per the M6.7A design §5: when a delegated workflow fails and the revision budget
allows, a plan revision may be routed through Loop Engine (propose→critique→
repair→validate) instead of a single-shot reasoning call. The certified
`AgentExecutionEngine` performs its *own* single-shot `revise_plan` internally
and exposes **no injection point** for Loop Engine (frozen — DL-0076), so this
Loop-mediated revision runs as a composition-root coordinator around the same
inputs §5.1 names (the failing plan's summary, the reasoning port, an
`AgentPlanValidator`). It calls Loop Engine's own `run_loop` library entry point
— never re-implementing iteration/stopping/validation (all owned by Loop Engine
and the injected validator, ADR-0016/ADR-0014).

The reasoning path is the **real** `PipelineRoutedReasoningPort` (no stub):
`run_loop` reads `Decision(FINAL).payload["content"]`, which the pipeline emits
natively — so, unlike agent goal *decomposition* (which needs structured
`steps` the offline provider cannot emit, DL-0072), the Loop revision path runs
fully against real runtimes. This is the "replace the Bundle 1 stub where
architecture allows" case.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from loop import LoopDefinition, LoopResult, LoopStoppingPolicy, LoopValidator
from loop.core.pipeline import run_loop

from .reasoning_port import PipelineRoutedReasoningPort


class PlanRevisionCoordinator:
    def __init__(
        self,
        reasoning_port: PipelineRoutedReasoningPort,
        *,
        max_loop_iterations: int = 5,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._reasoning_port = reasoning_port
        self._max_loop_iterations = max_loop_iterations
        self._clock = clock

    def revise(
        self,
        *,
        loop_id: str,
        task_description: str,
        validator: LoopValidator,
        session_id: str,
        trace_id: str = "",
        org_id: str | None = None,
        expected_workflow_refs: Sequence[str] = (),  # noqa: ARG002 — see note below
    ) -> LoopResult:
        """Run one Loop-mediated revision to completion and return the
        `LoopResult`. Iteration/stopping/validation are entirely Loop Engine's
        and the validator's own concern; this method only composes the inputs.

        `expected_workflow_refs` is accepted for caller symmetry with
        `AgentPlanValidator` construction; the coordinator itself never inspects
        it (the validator already carries it)."""
        definition = LoopDefinition(
            loop_id=loop_id,
            task_description=task_description,
            stopping_policy=LoopStoppingPolicy(max_iterations=self._max_loop_iterations),
        )
        reasoning = (
            self._reasoning_port.with_trace(trace_id) if trace_id else self._reasoning_port
        )
        return run_loop(
            definition,
            reasoning_port=reasoning,
            validator=validator,
            session_id=session_id,
            org_id=org_id,
            clock=self._clock,
        )
