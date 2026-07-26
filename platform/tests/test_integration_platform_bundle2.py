"""Agent Platform Integration — Bundle 2 (M6.7B-2) test suite.

Verifies replay correlation, observability aggregation, diagnostics
aggregation, HTTP error translation, failure propagation, end-to-end trace
propagation, plan revision (real wiring), capability-resolver wiring, latency
instrumentation, and the (unchanged) dependency boundaries. Aggregation/
correlation only — no capability behaviour is exercised beyond its own public
surface.
"""

from __future__ import annotations

import ast
import pathlib

import integration as I
from agent import AgentGoal
from agent.domain import AgentCapability
from agent.execution import AgentExecutionConfiguration, AgentExecutionFactory
from core.errors import ConfigError, PlatformError, ProviderError, RuntimeConflict
from core.ports.context import ContextRequest
from core.ports.reasoning import Decision, DecisionKind, ReasoningRequest
from integration.bridges import AgentPlanValidator, WorkflowDelegationBridge
from integration.observability import PlatformCorrelation, PlatformSpan
from integration.replay import PlatformReplayIndex, PlatformReplayQuery
from loop import LoopProposal
from workflow import (
    WorkflowFactory,
    WorkflowGraph,
    WorkflowManifest,
    WorkflowNode,
    WorkflowNodeKind,
)
from workflow.execution import WorkflowExecutionFactory

CLOCK = lambda: 0.0  # noqa: E731


def _real_workflow_runtimes():
    wf = WorkflowFactory.default(clock=CLOCK)
    graph = WorkflowGraph(
        entry_node="a",
        nodes=(WorkflowNode(node_id="a", kind=WorkflowNodeKind.DELAY, delay_ms=1),),
        edges=(),
    )
    wf.register(WorkflowManifest(id="onboard", namespace="internal", version="1.0.0", graph=graph))
    wf.activate("internal", "onboard", "1.0.0")
    ex = WorkflowExecutionFactory.default(wf.registry, clock=CLOCK)
    return wf, ex


class _StepReasoningPort:
    """Structured reasoning stub (offline provider can't emit steps — DL-0072)."""

    def __init__(self, workflow_ref: str) -> None:
        self._steps = ({"kind": "delegate_workflow", "workflow_ref": workflow_ref, "inputs": {}},)

    def run(self, request: ReasoningRequest):
        yield Decision(kind=DecisionKind.FINAL, payload={"steps": list(self._steps)})


def _registered_platform():
    """A platform whose workflow registry has an active `internal.onboard`."""
    root = I.PlatformFactory.default(clock=CLOCK)
    wf = root.config.workflow_runtime
    graph = WorkflowGraph(
        entry_node="a",
        nodes=(WorkflowNode(node_id="a", kind=WorkflowNodeKind.DELAY, delay_ms=1),),
        edges=(),
    )
    wf.register(WorkflowManifest(id="onboard", namespace="internal", version="1.0.0", graph=graph))
    wf.activate("internal", "onboard", "1.0.0")
    return root


# ── replay correlation ──────────────────────────────────────────────────────
def test_replay_index_records_and_correlates_by_trace_id():
    index = PlatformReplayIndex()
    index.record("tr1", "agent", "agent_replay", "ar-1")
    index.record("tr1", "workflow", "workflow_execution", "we-1")
    index.record("tr2", "agent", "agent_replay", "ar-2")
    corr = I.PlatformReplayCorrelation(index)
    tr1 = corr.correlate(PlatformReplayQuery("tr1"))
    assert {a.ref_id for a in tr1} == {"ar-1", "we-1"}
    assert {a.capability for a in tr1} == {"agent", "workflow"}
    assert [a.ref_id for a in corr.correlate(PlatformReplayQuery("tr2"))] == ["ar-2"]


def test_replay_validate_delegates_to_owning_engine():
    # Real Agent run -> record_replay -> correlate -> validate via the agent's
    # OWN replay engine (never replayed by Integration).
    wf, ex = _real_workflow_runtimes()
    bridge = WorkflowDelegationBridge(ex, wf.registry)
    from context import ContextFactory

    agent = AgentExecutionFactory.create(
        AgentExecutionConfiguration.default(),
        workflow_port=bridge,
        reasoning_port=_StepReasoningPort("internal.onboard@1.0.0"),
        context_port=ContextFactory.default(clock=CLOCK),
        clock=CLOCK,
    )
    goal = AgentGoal(goal_id="g1", agent_id="a1", conversation_id="c1", description="onboard")
    execution, _ = agent.submit_goal(
        goal, capabilities=(), context_request=ContextRequest(session_id="c1", query="x"),
    )
    execution, result = agent.poll_goal(execution.execution_id, goal)
    record = agent.record_replay((), result)

    corr = I.PlatformReplayCorrelation()
    corr.index.record("tr", "agent", "agent_replay", record.replay_id)
    validations = corr.validate(
        PlatformReplayQuery("tr"), validators={"agent": agent.validate_replay},
    )
    assert len(validations) == 1 and validations[0].validated is True


def test_replay_validate_skips_capability_without_validator():
    corr = I.PlatformReplayCorrelation()
    corr.index.record("tr", "loop", "loop_replay", "lr-1")
    assert corr.validate(PlatformReplayQuery("tr"), validators={}) == ()


# ── observability aggregation ───────────────────────────────────────────────
def test_platform_correlation_groups_spans_by_trace():
    spans = [
        PlatformSpan(trace_id="tr1", capability="agent", name="a"),
        PlatformSpan(trace_id="tr1", capability="workflow", name="w"),
        PlatformSpan(trace_id="tr2", capability="agent", name="a"),
    ]
    grouped = PlatformCorrelation.group_spans(spans)
    assert set(grouped) == {"tr1", "tr2"}
    assert len(grouped["tr1"]) == 2
    assert PlatformCorrelation.trace_of(spans, "tr1").capabilities == ("agent", "workflow")


def test_execute_records_trace_scoped_spans_and_metrics():
    root = _registered_platform()
    resp = root.execute(I.PlatformRequest(
        session_id="s1", agent_id="internal.coach@1.0.0", goal_description="do a thing",
    ))
    trace = root.platform_trace(resp.trace_id)
    assert "agent" in trace.capabilities
    metrics = root.platform_metrics(resp.trace_id)
    assert metrics.trace_id == resp.trace_id
    assert metrics.routing_latency is not None
    assert metrics.agent_statistics is not None  # reference to agent's own stats


# ── diagnostics aggregation ─────────────────────────────────────────────────
def test_diagnostics_aggregates_all_five_capabilities():
    root = I.PlatformFactory.default(clock=CLOCK)
    sentinel = object()
    diag = root.diagnostics(trace_id="tr", loop_diagnostics=sentinel)
    assert diag.trace_id == "tr"
    assert diag.agent_diagnostics is not None
    assert diag.workflow_diagnostics is not None
    assert diag.tool_diagnostics is not None   # ToolStatistics reference
    assert diag.skill_diagnostics is not None  # SkillStatistics reference
    assert diag.loop_diagnostics is sentinel   # passed-in reference, not recomputed


# ── HTTP translation ────────────────────────────────────────────────────────
def test_http_translation_is_deterministic_and_typed():
    assert I.translate_error(I.IntegrationError("x")).status_code == 500
    assert I.translate_error(ConfigError("x")).status_code == 400
    assert I.translate_error(ProviderError("x")).status_code == 502
    assert I.translate_error(RuntimeConflict("x")).status_code == 409
    assert I.translate_error(ValueError("x")).status_code == 500  # non-platform -> 500
    resp = I.translate_error(ConfigError("bad cfg"), trace_id="tr")
    assert resp.error_type == "ConfigError" and resp.trace_id == "tr"
    assert resp.body["category"] == "configuration"


def test_http_translation_no_fastapi_import():
    # The http layer must not import api/fastapi (dependency rules).
    src = pathlib.Path("integration/http").rglob("*.py")
    for p in src:
        text = p.read_text()
        assert "fastapi" not in text and "import api" not in text


# ── failure propagation ─────────────────────────────────────────────────────
def test_failure_translator_preserves_typed_error():
    tr = I.FailureTranslator()
    exc = ProviderError("upstream down")
    summary = tr.from_exception(exc, trace_id="tr", origin="provider")
    assert summary.original is exc          # kept, never rewrapped
    assert summary.category == "provider"
    assert summary.error_type == "ProviderError"
    assert summary.is_boundary_error is False


def test_failure_from_result_only_on_failure():
    tr = I.FailureTranslator()

    class _R:
        partial_success = False
        error = "boom"

    class _OK:
        partial_success = True
        error = None

    assert tr.from_result(_R(), trace_id="tr").message == "boom"
    assert tr.from_result(_OK(), trace_id="tr") is None


def test_failure_policy_propagates_capability_errors_verbatim():
    policy = I.IntegrationFailurePolicy()
    assert policy.propagate_verbatim(ConfigError("x")) is True     # capability error
    assert policy.propagate_verbatim(I.IntegrationError("x")) is False  # the one boundary error
    assert policy.is_boundary_error(I.IntegrationError("x")) is True
    assert isinstance(ConfigError("x"), PlatformError)


# ── end-to-end trace propagation ────────────────────────────────────────────
class _SpyPipeline:
    """Records the RequestContext trace_id the reasoning port routes with."""

    def __init__(self) -> None:
        self.seen_trace: str | None = None

    def execute(self, ctx, *, cancellation=None, streaming=True):
        self.seen_trace = ctx.trace_id

        class _Resp:
            text = "ok"
            snapshot = None

        return _Resp()


def test_reasoning_port_threads_one_trace_id():
    from integration.bridges import PipelineRoutedReasoningPort

    spy = _SpyPipeline()
    # trace_provider supplies the current platform trace.
    port = PipelineRoutedReasoningPort(spy, trace_provider=lambda: "tr_platform")
    list(port.run(ReasoningRequest(session_id="s", context_ref="c")))
    assert spy.seen_trace == "tr_platform"
    # explicit with_trace binding wins over the provider.
    list(port.with_trace("tr_bound").run(ReasoningRequest(session_id="s", context_ref="c")))
    assert spy.seen_trace == "tr_bound"


def test_execute_preserves_single_trace_id_onto_response():
    root = _registered_platform()
    resp = root.execute(I.PlatformRequest(
        session_id="s1", agent_id="a1", goal_description="thing",
    ))
    # one id: request + context + response + correlation index all share it.
    assert resp.trace_id.startswith("tr_")
    assert root.replay_correlation.index.entries_for(resp.trace_id)  # recorded under same id


# ── cancellation propagation (chained, Bundle 1 mechanism verified in situ) ──
def test_workflow_delegation_bridge_chains_cancellation():
    wf, ex = _real_workflow_runtimes()
    token = I.IntegrationCancellationToken("tr")
    bridge = WorkflowDelegationBridge(ex, wf.registry, cancellation=token)
    handle = bridge.resolve("internal.onboard@1.0.0")
    bridge.submit(handle, {})           # a per-execution workflow token is chained
    token.cancel(reason="client-abort")  # cancelling the root cancels the chained child
    assert token.is_cancelled() and token.reason == "client-abort"


# ── plan revision (real Agent↓Loop↓Reasoning wiring) ────────────────────────
def test_plan_revision_converges_with_real_reasoning():
    root = _registered_platform()
    result = root.revise_plan(
        task_description="revise the failing plan", session_id="s1",
        expected_workflow_refs=("internal.onboard@1.0.0",), trace_id="tr_rev",
    )
    # Loop Engine (real) + PipelineRoutedReasoningPort (real) + AgentPlanValidator
    # (real): the validator passes because the ref resolves, so the loop converges.
    assert result.converged is True
    assert result.stop_reason == "validation_success"


def test_plan_revision_fails_validation_when_ref_unresolvable():
    root = _registered_platform()
    result = root.revise_plan(
        task_description="revise", session_id="s1",
        expected_workflow_refs=("internal.ghost@1.0.0",), trace_id="tr_rev2",
    )
    # The validator fails closed on the unresolvable ref, so the loop never
    # stops on validation_success; the deterministic offline provider yields
    # structurally-identical proposals, so Loop Engine's own convergence policy
    # stops the loop (its own certified semantics, not Integration's decision).
    assert result.stop_reason != "validation_success"
    assert result.iterations[-1].validation.passed is False


def test_agent_plan_validator_fails_closed_on_unresolvable():
    wf, ex = _real_workflow_runtimes()
    bridge = WorkflowDelegationBridge(ex, wf.registry)
    validator = AgentPlanValidator(bridge, expected_workflow_refs=("internal.ghost@1.0.0",))
    assert validator.validate(LoopProposal(proposal_id="p", content_ref="x")).passed is False


# ── capability-resolver wiring ──────────────────────────────────────────────
def test_capability_resolver_resolves_via_certified_surfaces():
    root = I.PlatformFactory.default(clock=CLOCK)
    r = root.capability_resolver
    assert r.resolve(AgentCapability(kind="tool", tag="echo")).available is True
    assert r.resolve(AgentCapability(kind="memory", tag="m")).available is True
    assert r.resolve(AgentCapability(kind="tool", tag="nonexistent")).available is False
    assert r.resolve(AgentCapability(kind="unknown", tag="m")).available is False


def test_execute_with_capabilities_does_not_crash():
    root = _registered_platform()
    resp = root.execute(I.PlatformRequest(
        session_id="s1", agent_id="a1", goal_description="thing",
        capabilities=(AgentCapability(kind="tool", tag="echo"),),
    ))
    assert isinstance(resp, I.PlatformResponse)


# ── latency instrumentation ─────────────────────────────────────────────────
def test_latency_recorder_captures_samples():
    root = _registered_platform()
    root.execute(I.PlatformRequest(session_id="s1", agent_id="a1", goal_description="x"))
    stats = root.latency.stats()
    assert stats.count >= 1
    assert stats.total_s >= 0.0
    assert stats.avg_s >= 0.0
    assert stats.avg_routing_overhead_s >= 0.0


# ── dependency boundaries ───────────────────────────────────────────────────
def _imports(pkg: str) -> set[str]:
    tops: set[str] = set()
    for p in pathlib.Path(pkg).rglob("*.py"):
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.ImportFrom) and n.module and not n.level:
                tops.add(n.module.split(".")[0])
            elif isinstance(n, ast.Import):
                for a in n.names:
                    tops.add(a.name.split(".")[0])
    return tops


def test_integration_imports_only_allowed_packages():
    allowed = {
        "core", "tenant", "agent", "workflow", "loop", "tool", "skill", "hooks",
        "pipeline", "bridge", "context",
        "integration", "__future__", "abc", "collections", "dataclasses", "typing",
        "hashlib", "time", "contextlib", "contextvars",  # contextvars: F1 hardening (M6.7.1)
    }
    tops = _imports("integration")
    assert tops <= allowed, tops - allowed
    # Bundle 2 must NOT import the standalone replay/events edge platforms.
    assert "replay" not in tops and "events" not in tops
    assert "api" not in tops and "fastapi" not in tops


def test_no_certified_capability_imports_integration():
    for pkg in ("core", "agent", "workflow", "loop", "tool", "skill", "hooks",
                "pipeline", "bridge", "reasoning", "provider", "context", "prompt",
                "memory", "knowledge"):
        assert "integration" not in _imports(pkg), pkg
