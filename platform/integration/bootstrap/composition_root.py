"""Platform Composition Root (M6.7B-1).

The platform's second composition root, mirroring `pipeline/`'s own role for
the six Epic 5 runtimes but one layer up — wiring Agent Runtime's delegation
lineage (Agent→Workflow, Agent/Loop→Reasoning) together with the already-
composed `pipeline.RuntimePipeline` (design §1). It owns **composition only**:
no domain model, no lifecycle, no registry (design §1 "It is not a fourteenth
capability").

Three classes:

- `PlatformBuilder` — constructs the certified capabilities in one
  deterministic order (Provider/Pipeline → Tool → Skill → Workflow → Loop →
  Agent → Integration; design §15) and records that order for verification. No
  circular dependency is possible: each stage depends only on already-built
  earlier stages.
- `PlatformCompositionRoot` — the assembled platform: the wired capability
  instances + bridges + config, plus thin *execution wiring* (`execute`) that
  calls Agent Runtime's own `submit_goal`/`poll_goal` in the order the engine
  itself documents — never re-deciding anything (design §16.1).
- `PlatformBootstrap` — startup/shutdown ordering (shutdown is the reverse of
  startup), recording both for verification.

No runtime logic lives here (this bundle's Platform Bootstrap scope).
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable

from agent import AgentFactory
from agent.execution import AgentExecutionConfiguration, AgentExecutionFactory
from context import ContextFactory
from pipeline import RuntimePipelineFactory
from skill import SkillFactory
from tool import ToolFactory
from workflow import WorkflowFactory
from workflow.execution import WorkflowExecutionFactory

from ..bridges import (
    AgentPlanValidator,
    AgentPortBridge,
    PipelineRoutedReasoningPort,
    PlanRevisionCoordinator,
    PlatformCapabilityResolver,
    WorkflowDelegationBridge,
    WorkflowPortBridge,
)
from ..cancellation import IntegrationCancellationToken
from ..config import IntegrationConfiguration, PlatformConfiguration
from ..diagnostics import IntegrationDiagnostics, build_diagnostics
from ..errors.failure import FailureTranslator, IntegrationFailurePolicy
from ..http import HttpErrorResponse, translate_error
from ..observability import (
    LatencyRecorder,
    PlatformCorrelation,
    PlatformMetrics,
    PlatformSpan,
    PlatformTrace,
)
from ..replay import PlatformReplayCorrelation, PlatformReplayIndex, PlatformReplayQuery
from ..request import PlatformExecution, PlatformRequest, PlatformResponse
from ..trace import current_cancellation, current_trace, execution_scope


class PlatformBuilder:
    """Deterministic construction of the certified capabilities. Every stage
    depends only on earlier stages — the acyclic graph of design §15."""

    def __init__(
        self,
        integration_config: IntegrationConfiguration | None = None,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._config = integration_config or IntegrationConfiguration()
        self._config.validate()
        self._clock = clock or time.time
        self.construction_order: list[str] = []

    def build(self) -> PlatformCompositionRoot:
        clock = self._clock

        # 1. Provider + Pipeline (Provider reached exclusively via its bridge,
        #    inside the pipeline — design §9). One composed instance.
        pipeline = RuntimePipelineFactory.default(clock=clock)
        self.construction_order.append("provider_pipeline")

        # 2. Tool Runtime.
        tool_runtime = ToolFactory.default(clock=clock)
        self.construction_order.append("tool_runtime")

        # 3. Skill Runtime.
        skill_runtime = SkillFactory.default(clock=clock)
        self.construction_order.append("skill_runtime")

        # 4. Workflow Runtime (definition/registry).
        workflow_runtime = WorkflowFactory.default(clock=clock)
        self.construction_order.append("workflow_runtime")

        # 5. Workflow Execution Runtime — shares the definition registry so a
        #    workflow registered above is executable here.
        workflow_execution = WorkflowExecutionFactory.default(
            workflow_runtime.registry, clock=clock
        )
        self.construction_order.append("workflow_execution")

        # 6. Loop Engine — a pure library (no runtime instance; design §1). Its
        #    reasoning path is the same pipeline-routed port Agent planning uses
        #    (now trace-threaded + latency-instrumented, Bundle 2); its validator
        #    is composed per plan-revision. Recorded as a construction stage for
        #    order verification, constructs no stateful runtime object.
        # Trace threading is context-local (F1): the reasoning port reads
        # `integration.trace.current_trace` (a contextvar set per request),
        # concurrency-safe by construction. Latency is a bounded ring buffer (F2).
        latency = LatencyRecorder(maxlen=self._config.max_latency_samples)
        reasoning_port = PipelineRoutedReasoningPort(
            pipeline, trace_provider=current_trace, latency=latency, clock=clock,
        )
        revision_coordinator = PlanRevisionCoordinator(
            reasoning_port, max_loop_iterations=self._config.max_loop_iterations, clock=clock,
        )
        self.construction_order.append("loop_engine")

        # 7. Agent Runtime — identity registry + execution engine, wired to the
        #    concrete WorkflowDelegationBridge (the AgentWorkflowPort), the
        #    pipeline-routed ReasoningPort, a real ContextPort for planning, and
        #    (Bundle 2) the concrete AgentCapabilityResolver over the certified
        #    tool/skill/workflow discovery surfaces (design §8).
        agent_runtime = AgentFactory.default(clock=clock)
        context_port = ContextFactory.default(clock=clock)
        # Cancellation is context-local (F3): the bridge chains each delegated
        # workflow's own token to the current request's IntegrationCancellationToken,
        # read from the contextvar — never a shared mutable token.
        workflow_bridge = WorkflowDelegationBridge(
            workflow_execution, workflow_runtime.registry,
            cancellation_provider=current_cancellation,
            max_tracked=self._config.max_correlation_traces,
        )
        capability_resolver = PlatformCapabilityResolver(
            tool_runtime=tool_runtime, skill_runtime=skill_runtime,
            workflow_runtime=workflow_runtime,
        )
        agent_execution = AgentExecutionFactory.create(
            AgentExecutionConfiguration(max_revisions=self._config.max_plan_revisions),
            workflow_port=workflow_bridge,
            capability_resolver=capability_resolver,
            reasoning_port=reasoning_port,
            context_port=context_port,
            clock=clock,
        )
        self.construction_order.append("agent_execution")

        # 8. Integration Layer — the composition root itself, plus the two
        #    conformance bridges closing Deferred Decisions items 1 and 10.
        platform_config = PlatformConfiguration(
            provider_pipeline=pipeline,
            tool_runtime=tool_runtime,
            skill_runtime=skill_runtime,
            workflow_runtime=workflow_runtime,
            workflow_execution=workflow_execution,
            agent_execution=agent_execution,
            agent_runtime=agent_runtime,
            integration=self._config,
        )
        platform_config.validate()
        self.construction_order.append("integration")

        return PlatformCompositionRoot(
            config=platform_config,
            reasoning_port=reasoning_port,
            workflow_bridge=workflow_bridge,
            workflow_port_bridge=WorkflowPortBridge(workflow_execution, workflow_runtime.registry),
            agent_port_bridge=AgentPortBridge(agent_runtime.registry),
            capability_resolver=capability_resolver,
            revision_coordinator=revision_coordinator,
            latency=latency,
            construction_order=tuple(self.construction_order),
            clock=clock,
        )


class PlatformCompositionRoot:
    """The assembled platform. Holds references to the certified capabilities
    and bridges; owns thin execution wiring only (design §16.1)."""

    def __init__(
        self,
        *,
        config: PlatformConfiguration,
        reasoning_port: PipelineRoutedReasoningPort,
        workflow_bridge: WorkflowDelegationBridge,
        workflow_port_bridge: WorkflowPortBridge,
        agent_port_bridge: AgentPortBridge,
        capability_resolver: PlatformCapabilityResolver,
        revision_coordinator: PlanRevisionCoordinator,
        latency: LatencyRecorder,
        construction_order: tuple[str, ...],
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self.reasoning_port = reasoning_port
        self.workflow_bridge = workflow_bridge
        self.workflow_port_bridge = workflow_port_bridge
        self.agent_port_bridge = agent_port_bridge
        self.capability_resolver = capability_resolver
        self.revision_coordinator = revision_coordinator
        self.latency = latency
        self.construction_order = construction_order
        self._clock = clock or time.time
        # Bundle 2 correlation state (Integration-owned; ids only, never
        # capability state — design §13/§14). Bounded per M6.7.1 F2.
        ic = config.integration
        self._max_traces = ic.max_correlation_traces
        self._max_spans_per_trace = ic.max_spans_per_trace
        self.replay_correlation = PlatformReplayCorrelation(PlatformReplayIndex(
            max_traces=ic.max_correlation_traces, max_per_trace=ic.max_spans_per_trace,
        ))
        self.failure_translator = FailureTranslator()
        self.failure_policy = IntegrationFailurePolicy()
        self._spans: OrderedDict[str, list[PlatformSpan]] = OrderedDict()

    # ── execution wiring (successful path, design §16.1) ───────────────────────
    def execute(
        self,
        request: PlatformRequest,
        *,
        cancellation: IntegrationCancellationToken | None = None,
    ) -> PlatformResponse:
        """Drive the successful-execution path by calling Agent Runtime's own
        `submit_goal`/`poll_goal` in the order the engine documents — never
        re-deciding anything the engine owns (design §3 ownership matrix).

        Trace + cancellation are bound context-locally for the extent of the
        call (F1/F3): the one platform `trace_id` reaches the reasoning port and
        the request's `IntegrationCancellationToken` reaches the workflow bridge,
        both via `execution_scope` — no shared mutable holder. A caller may pass
        its own `cancellation` token (to cancel from another thread/task);
        otherwise the request's own token is used."""
        entry = PlatformExecution(self.config.integration)
        pec = entry.begin(request)
        token = cancellation or pec.cancellation
        agent = self.config.agent_execution

        with execution_scope(pec.trace_id, token):
            if token.is_cancelled():
                return self._response(pec, None, execution_id="", state="cancelled")
            execution, result = agent.submit_goal(
                pec.goal, capabilities=pec.capabilities, context_request=pec.context_request,
            )
            polls = 0
            max_polls = self.config.integration.max_poll_iterations
            while (
                result is None
                and execution.state.value == "awaiting_workflow"
                and polls < max_polls
                and not token.is_cancelled()  # cooperative cancellation at the boundary
            ):
                execution, result = agent.poll_goal(execution.execution_id, pec.goal)
                polls += 1

            self._record_correlation(pec.trace_id, execution)
            return self._response(
                pec, result, execution_id=execution.execution_id, state=execution.state.value,
            )

    def _response(self, pec, result, *, execution_id: str, state: str) -> PlatformResponse:
        return PlatformResponse(
            trace_id=pec.trace_id,
            request_id=pec.request_id,
            goal_id=pec.goal.goal_id,
            execution_id=execution_id,
            final_state=state,
            partial_success=bool(result.partial_success) if result is not None else False,
            error=result.error if result is not None else None,
        )

    def _record_correlation(self, trace_id: str, execution) -> None:
        """Record replay-artifact + telemetry references for one execution,
        keyed by the shared trace_id (references only — never capability state).
        The span store is LRU-bounded (F2): both the number of traces and the
        spans per trace are capped."""
        self.replay_correlation.index.record(
            trace_id, "agent", "agent_execution", execution.execution_id
        )
        spans = self._add_span(trace_id, PlatformSpan(
            trace_id=trace_id, capability="agent", name="agent_goal_execution",
            ref=execution, status=execution.state.value,
        ))
        delegated = getattr(execution, "delegated_execution_id", None)
        if delegated:
            self.replay_correlation.index.record(
                trace_id, "workflow", "workflow_execution", delegated
            )
            spans.append(PlatformSpan(
                trace_id=trace_id, capability="workflow", name="delegated_workflow",
                ref=delegated, status=self.workflow_bridge.status(delegated),
            ))
            if len(spans) > self._max_spans_per_trace:
                del spans[0]

    def _add_span(self, trace_id: str, span: PlatformSpan) -> list[PlatformSpan]:
        """Append a span to a bounded, LRU-ordered per-trace list."""
        spans = self._spans.get(trace_id)
        if spans is None:
            if len(self._spans) >= self._max_traces:
                self._spans.popitem(last=False)  # evict LRU trace
            spans = []
            self._spans[trace_id] = spans
        spans.append(span)
        if len(spans) > self._max_spans_per_trace:
            del spans[0]
        self._spans.move_to_end(trace_id)
        return spans

    # ── plan revision (Agent↓Loop↓Reasoning, real wiring; design §5) ────────────
    def revise_plan(
        self,
        *,
        task_description: str,
        session_id: str,
        expected_workflow_refs=(),
        trace_id: str = "",
        loop_id: str = "revision",
    ):
        """Run a Loop-mediated plan revision with the real pipeline-routed
        reasoning port and a real `AgentPlanValidator` over the workflow-
        resolution seam. Returns Loop Engine's own `LoopResult`."""
        validator = AgentPlanValidator(
            self.workflow_bridge, expected_workflow_refs=expected_workflow_refs
        )
        with execution_scope(trace_id):
            return self.revision_coordinator.revise(
                loop_id=loop_id, task_description=task_description, validator=validator,
                session_id=session_id, trace_id=trace_id,
                expected_workflow_refs=expected_workflow_refs,
            )

    # ── observability aggregation (references only, design §14) ─────────────────
    def platform_trace(self, trace_id: str) -> PlatformTrace:
        return PlatformCorrelation.trace_of(self._spans.get(trace_id, ()), trace_id)

    def platform_metrics(self, trace_id: str) -> PlatformMetrics:
        return PlatformMetrics(
            trace_id=trace_id,
            agent_statistics=_first_of(self.config.agent_execution, "diagnostics", "statistics"),
            workflow_statistics=_first_of(
                self.config.workflow_execution, "diagnostics", "statistics"
            ),
            tool_statistics=_first_of(self.config.tool_runtime, "statistics", "diagnostics"),
            skill_statistics=_first_of(self.config.skill_runtime, "statistics", "diagnostics"),
            routing_latency=self.latency.stats(),
        )

    def replay_query(self, trace_id: str) -> PlatformReplayQuery:
        return PlatformReplayQuery(trace_id=trace_id)

    def replay_validators(self):
        """Owning-engine validate-by-id callables for `PlatformReplayCorrelation.
        validate` — each delegates to the capability's *own* replay engine, never
        replaying another's state (design §13)."""
        return {
            "agent": self.config.agent_execution.validate_replay,
            "workflow": self.config.workflow_execution.validate_replay,
        }

    # ── failure + HTTP translation (design §12) ─────────────────────────────────
    def http_error(self, exc: BaseException, *, trace_id: str = "") -> HttpErrorResponse:
        return translate_error(exc, trace_id=trace_id)

    # ── diagnostics (reference aggregation only, design §14) ────────────────────
    def diagnostics(
        self, *, trace_id: str = "", loop_diagnostics=None, healthy: bool = True
    ) -> IntegrationDiagnostics:
        return build_diagnostics(
            trace_id=trace_id,
            agent_execution=self.config.agent_execution,
            workflow_execution=self.config.workflow_execution,
            loop_diagnostics=loop_diagnostics,
            tool_runtime=self.config.tool_runtime,
            skill_runtime=self.config.skill_runtime,
            healthy=healthy,
        )


class PlatformBootstrap:
    """Startup/shutdown ordering (design Platform Bootstrap scope). Startup
    builds the composition root in the deterministic construction order;
    shutdown is the strict reverse. No runtime logic."""

    def __init__(
        self,
        integration_config: IntegrationConfiguration | None = None,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._builder = PlatformBuilder(integration_config, clock=clock)
        self.startup_order: tuple[str, ...] = ()
        self.shutdown_order: tuple[str, ...] = ()
        self._root: PlatformCompositionRoot | None = None

    def startup(self) -> PlatformCompositionRoot:
        self._root = self._builder.build()
        self.startup_order = self._root.construction_order
        return self._root

    def shutdown(self) -> tuple[str, ...]:
        """Reverse-order teardown. The certified capabilities are in-memory and
        hold no external resources in the offline profile, so teardown is
        ordering-only — recorded for verification, never a forced resource
        release this layer does not own."""
        self.shutdown_order = tuple(reversed(self.startup_order))
        self._root = None
        return self.shutdown_order


def _first_of(runtime, *methods: str):
    """Reference the first zero-arg telemetry builder a capability exposes, never
    recompute (design §14). A builder that requires arguments (e.g. one that
    computes over a supplied result set) is skipped — Integration references
    only already-materializable telemetry, never drives a computation."""
    for method in methods:
        builder = getattr(runtime, method, None)
        if callable(builder):
            try:
                return builder()
            except TypeError:
                continue
    return None
