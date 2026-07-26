"""Agent Platform Integration — Bundle 1 (M6.7B-1) test suite.

Verifies the composition root only (design §1): bootstrap, dependency
injection + deterministic construction/startup/shutdown ordering, the bridge
adapters' correctness against *real* certified runtimes (not Bundle 3's fakes,
per design §17), trace propagation, cancellation chaining, and the
`integration` dependency boundaries. No certified capability's behaviour is
exercised beyond its own already-certified public surface.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import integration as I
from agent import AgentFactory, AgentGoal, AgentManifest
from agent.execution import AgentExecutionConfiguration, AgentExecutionFactory
from core.errors import PlatformError
from core.ports.agent import AgentPort
from core.ports.reasoning import Decision, DecisionKind, ReasoningRequest
from core.ports.workflow import WorkflowHandle, WorkflowPort, WorkflowStart
from integration.bridges import (
    AgentPlanValidator,
    AgentPortBridge,
    PipelineRoutedReasoningPort,
    WorkflowDelegationBridge,
    WorkflowPortBridge,
)
from integration.config import IntegrationConfiguration, PlatformConfiguration
from loop import LoopProposal
from loop.core.cancellation import LoopCancellationToken
from pipeline import RuntimePipelineFactory
from pipeline.cancellation import PipelineCancellation
from workflow import (
    WorkflowFactory,
    WorkflowGraph,
    WorkflowManifest,
    WorkflowNode,
    WorkflowNodeKind,
)
from workflow.execution import WorkflowExecutionFactory
from workflow.execution.cancellation import WorkflowCancellationToken

CLOCK = lambda: 0.0  # noqa: E731 — deterministic clock for reproducible ids


# ── fixtures / helpers ──────────────────────────────────────────────────────
def _real_workflow_runtimes(workflow_ref: str = "internal.onboard@1.0.0"):
    """A real WorkflowRuntime (definition) + WorkflowExecutionRuntime sharing
    one registry, with a single-node DELAY workflow registered and active."""
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
    """A minimal *real* `ReasoningPort` that yields a one-step delegate_workflow
    plan. Used only where the agent must produce a workflow step: the offline
    `EchoProvider` cannot emit structured plan steps (DL-0670), so — exactly as
    the certified Agent Runtime's own `tests/test_agent_execution.py` does — a
    structured reasoning stub stands in for a structured-output provider. Every
    other instance in the delegation smoke test is the real certified runtime."""

    def __init__(self, workflow_ref: str) -> None:
        self._steps = ({"kind": "delegate_workflow", "workflow_ref": workflow_ref, "inputs": {}},)

    def run(self, request: ReasoningRequest):
        yield Decision(kind=DecisionKind.FINAL, payload={"steps": list(self._steps)})


# ── bootstrap / DI / ordering ───────────────────────────────────────────────
def test_platform_factory_builds_all_capabilities():
    root = I.PlatformFactory.default(clock=CLOCK)
    cfg = root.config
    assert cfg.provider_pipeline is not None
    assert cfg.tool_runtime is not None
    assert cfg.skill_runtime is not None
    assert cfg.workflow_runtime is not None
    assert cfg.workflow_execution is not None
    assert cfg.agent_execution is not None
    assert cfg.agent_runtime is not None


def test_construction_order_is_deterministic():
    root = I.PlatformFactory.default(clock=CLOCK)
    assert root.construction_order == PlatformConfiguration.CONSTRUCTION_ORDER
    # Provider/Pipeline first, Integration last — the acyclic order of design §15.
    assert root.construction_order[0] == "provider_pipeline"
    assert root.construction_order[-1] == "integration"


def test_bootstrap_startup_then_shutdown_is_reverse_order():
    boot = I.PlatformBootstrap(clock=CLOCK)
    root = boot.startup()
    assert boot.startup_order == root.construction_order
    shutdown = boot.shutdown()
    assert shutdown == tuple(reversed(boot.startup_order))


def test_platform_configuration_missing_capability_raises():
    cfg = PlatformConfiguration(provider_pipeline=object())  # everything else None
    with pytest.raises(I.IntegrationError):
        cfg.validate()


def test_integration_configuration_validation():
    IntegrationConfiguration().validate()  # defaults are valid
    with pytest.raises(I.IntegrationError):
        IntegrationConfiguration(max_loop_iterations=0).validate()
    with pytest.raises(I.IntegrationError):
        IntegrationConfiguration(max_plan_revisions=-1).validate()


def test_integration_error_is_platform_error():
    assert issubclass(I.IntegrationError, PlatformError)


# ── bridge: PipelineRoutedReasoningPort (against the real pipeline) ──────────
def test_pipeline_routed_reasoning_port_yields_final_from_real_pipeline():
    pl = RuntimePipelineFactory.default(clock=CLOCK)
    port = PipelineRoutedReasoningPort(pl, clock=CLOCK)
    decisions = list(port.run(ReasoningRequest(session_id="s1", context_ref="cref")))
    assert decisions and decisions[-1].kind is DecisionKind.FINAL
    assert "content" in decisions[-1].payload  # loop's _parse_final_content reads this key


def test_with_trace_is_immutable_rebind():
    pl = RuntimePipelineFactory.default(clock=CLOCK)
    port = PipelineRoutedReasoningPort(pl, clock=CLOCK)
    bound = port.with_trace("tr_root")
    assert bound is not port
    assert port._trace_id == "" and bound._trace_id == "tr_root"  # no shared mutable state


# ── bridge: WorkflowDelegationBridge (concrete AgentWorkflowPort) ────────────
def test_workflow_delegation_bridge_resolve_submit_status_against_real_runtime():
    wf, ex = _real_workflow_runtimes()
    bridge = WorkflowDelegationBridge(ex, wf.registry)
    handle = bridge.resolve("internal.onboard@1.0.0")
    execution_id = bridge.submit(handle, {"x": 1})
    # The offline engine runs synchronously to a terminal state inside start().
    assert bridge.status(execution_id) == "completed"


def test_workflow_delegation_bridge_resolve_defaults_version():
    wf, ex = _real_workflow_runtimes()
    bridge = WorkflowDelegationBridge(ex, wf.registry)
    handle = bridge.resolve("internal.onboard")  # no @version -> active version resolved
    assert handle == "internal.onboard@1.0.0"


def test_workflow_delegation_bridge_unknown_handle_raises():
    wf, ex = _real_workflow_runtimes()
    bridge = WorkflowDelegationBridge(ex, wf.registry)
    with pytest.raises(I.IntegrationError):
        bridge.submit("internal.never@1.0.0", {})


def test_workflow_delegation_bridge_malformed_ref_raises():
    wf, ex = _real_workflow_runtimes()
    bridge = WorkflowDelegationBridge(ex, wf.registry)
    with pytest.raises(I.IntegrationError):
        bridge.resolve("no-namespace")


# ── bridge: WorkflowPortBridge (frozen core WorkflowPort; Deferred item 10) ──
def test_workflow_port_bridge_conforms_and_starts():
    wf, ex = _real_workflow_runtimes()
    bridge = WorkflowPortBridge(ex, wf.registry)
    assert isinstance(bridge, WorkflowPort)
    handle = bridge.start(WorkflowStart(workflow_id="internal.onboard@1.0.0", session_id="s1"))
    assert isinstance(handle, WorkflowHandle)
    assert handle.status == "completed"
    assert bridge.status(handle.instance_id).status == "completed"


# ── bridge: AgentPortBridge (frozen core AgentPort; Deferred item 1) ─────────
def test_agent_port_bridge_conforms_and_resolves():
    agent_rt = AgentFactory.default(clock=CLOCK)
    agent_rt.register(AgentManifest(id="coach", namespace="internal", version="1.0.0",
                                    display_name="Coach"))
    agent_rt.activate("internal", "coach", "1.0.0")
    bridge = AgentPortBridge(agent_rt.registry)
    assert isinstance(bridge, AgentPort)
    ref = bridge.resolve("internal.coach@1.0.0")
    assert ref.agent_id == "internal.coach@1.0.0"
    assert ref.name == "Coach"
    # activate/handoff resolve the target (session binding is the API layer's).
    assert bridge.activate("internal.coach@1.0.0", session_id="s1").name == "Coach"
    assert bridge.handoff(session_id="s1", to_agent_id="internal.coach@1.0.0").name == "Coach"


# ── full successful-execution path (§16.1), real Agent + Workflow instances ──
def test_full_success_path_agent_delegates_to_real_workflow():
    wf, ex = _real_workflow_runtimes()
    workflow_ref = "internal.onboard@1.0.0"
    bridge = WorkflowDelegationBridge(ex, wf.registry)
    from context import ContextFactory

    agent = AgentExecutionFactory.create(
        AgentExecutionConfiguration.default(),
        workflow_port=bridge,
        reasoning_port=_StepReasoningPort(workflow_ref),
        context_port=ContextFactory.default(clock=CLOCK),
        clock=CLOCK,
    )
    goal = AgentGoal(goal_id="g1", agent_id="a1", conversation_id="c1", description="onboard user")
    from core.ports.context import ContextRequest

    # No capabilities: AgentCapabilityResolver is a Bundle 2 seam (design §8),
    # not wired here — the reasoning step supplies the workflow_ref directly.
    execution, result = agent.submit_goal(
        goal, capabilities=(), context_request=ContextRequest(session_id="c1", query="x"),
    )
    assert execution.state.value == "awaiting_workflow"
    execution, result = agent.poll_goal(execution.execution_id, goal)
    assert result is not None
    assert result.partial_success is True
    assert execution.state.value == "completed"


def test_composition_root_execute_preserves_trace_and_returns_response():
    root = I.PlatformFactory.default(clock=CLOCK)
    req = I.PlatformRequest(
        session_id="s1", agent_id="internal.coach@1.0.0", goal_description="do a thing",
    )
    resp = root.execute(req)
    assert isinstance(resp, I.PlatformResponse)
    # trace_id minted once at entry and carried onto the response (§16.1).
    assert resp.trace_id.startswith("tr_")
    assert resp.goal_id and resp.execution_id
    # Offline EchoProvider yields a zero-step plan (DL-0670): no workflow is
    # delegated, so the goal never reaches a terminal state — reported honestly,
    # never a fabricated success.
    assert resp.final_state in {"awaiting_workflow", "completed", "failed"}


# ── trace propagation ───────────────────────────────────────────────────────
def test_default_trace_id_is_deterministic_not_random():
    a = I.default_trace_id("trace", "s1", "agent", "desc")
    b = I.default_trace_id("trace", "s1", "agent", "desc")
    c = I.default_trace_id("trace", "s1", "agent", "OTHER")
    assert a == b and a != c
    assert a.startswith("tr_")


def test_one_trace_id_threaded_from_request_entry():
    entry = I.PlatformExecution(IntegrationConfiguration())
    req = I.PlatformRequest(session_id="s1", agent_id="a1", goal_description="d")
    pec = entry.begin(req)
    # one root trace_id, present on the context and available to downstream
    # capability contexts by value.
    assert pec.trace_id == pec.context.trace_id
    assert pec.cancellation.token == pec.trace_id
    assert pec.goal.goal_id == pec.context.goal_id
    # deterministic factory override is honoured.
    fixed = I.PlatformExecution(IntegrationConfiguration(trace_id_factory=lambda: "tr_fixed"))
    assert fixed.begin(req).trace_id == "tr_fixed"


def test_integration_execution_context_with_goal_id_is_immutable():
    ctx = I.IntegrationExecutionContext(trace_id="tr", session_id="s")
    updated = ctx.with_goal_id("g1")
    assert ctx.goal_id is None and updated.goal_id == "g1"
    assert updated.trace_id == "tr"


# ── cancellation propagation (chaining only, no shared state) ────────────────
def test_cancellation_chains_into_every_capability_token():
    root = I.IntegrationCancellationToken("tr")
    children = [
        root.chain(PipelineCancellation("tr")),
        root.chain(LoopCancellationToken("tr")),
        root.chain(WorkflowCancellationToken("tr")),
    ]
    assert all(not c.is_cancelled() for c in children)
    root.cancel(reason="client-abort")
    assert root.is_cancelled() and root.reason == "client-abort"
    assert all(c.is_cancelled() for c in children)


def test_cancellation_late_chain_still_honours_prior_cancel():
    root = I.IntegrationCancellationToken("tr")
    root.cancel()
    child = root.chain(PipelineCancellation("tr"))  # linked after cancel
    assert child.is_cancelled()


def test_cancellation_does_not_share_state_with_unchained_tokens():
    root = I.IntegrationCancellationToken("tr")
    unchained = WorkflowCancellationToken("tr")
    root.cancel()
    assert not unchained.is_cancelled()  # never mutated a token we did not chain


# ── AgentPlanValidator (first real LoopValidator) ───────────────────────────
def test_agent_plan_validator_passes_when_refs_resolve():
    wf, ex = _real_workflow_runtimes()
    bridge = WorkflowDelegationBridge(ex, wf.registry)
    validator = AgentPlanValidator(bridge, expected_workflow_refs=("internal.onboard@1.0.0",))
    result = validator.validate(LoopProposal(proposal_id="p1", content_ref="abc"))
    assert result.passed is True and result.confidence == 1.0


def test_agent_plan_validator_fails_on_empty_and_unresolvable():
    wf, ex = _real_workflow_runtimes()
    bridge = WorkflowDelegationBridge(ex, wf.registry)
    empty = AgentPlanValidator(bridge)
    assert empty.validate(LoopProposal(proposal_id="p1", content_ref="")).passed is False
    unresolvable = AgentPlanValidator(bridge, expected_workflow_refs=("internal.ghost@1.0.0",))
    assert unresolvable.validate(LoopProposal(proposal_id="p2", content_ref="x")).passed is False


# ── diagnostics: reference aggregation only ─────────────────────────────────
def test_diagnostics_aggregates_references_only():
    root = I.PlatformFactory.default(clock=CLOCK)
    diag = root.diagnostics(trace_id="tr_x")
    assert diag.trace_id == "tr_x"
    assert diag.healthy is True
    # references to each capability's own diagnostics — not None where wired.
    assert diag.agent_diagnostics is not None
    assert diag.workflow_diagnostics is not None


# ── dependency boundaries (design §15) ──────────────────────────────────────
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
        # composition-root dependencies permitted by design §15
        "core", "tenant", "agent", "workflow", "loop", "tool", "skill", "hooks",
        "pipeline", "bridge", "context",
        # own package + stdlib
        "integration", "__future__", "abc", "collections", "dataclasses", "typing",
        "hashlib", "time", "contextlib", "contextvars",  # contextvars: F1 hardening (M6.7.1)
    }
    tops = _imports("integration")
    assert tops <= allowed, tops - allowed


def test_no_certified_capability_imports_integration():
    # Positive boundary assertion replacing the prior "integration does not
    # exist" assumption (design §17 "no certified capability gains a new
    # import"): integration is a pure composition root; nothing it composes
    # imports it back (§15).
    for pkg in ("core", "agent", "workflow", "loop", "tool", "skill", "hooks",
                "pipeline", "bridge", "reasoning", "provider", "context", "prompt",
                "memory", "knowledge"):
        assert "integration" not in _imports(pkg), pkg
