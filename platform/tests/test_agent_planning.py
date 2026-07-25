"""Agent Runtime Planning (M6.5B-2) tests.

Verifies goal decomposition (an ordinary `ReasoningPort.run` call,
deterministically parsed), capability resolution via the Bundle 1
`AgentCapabilityResolver` seam, workflow selection/delegation via the
Bundle 1 `AgentWorkflowPort` seam (resolve/submit/status/signal — never
constructing or executing a `WorkflowGraph`), plan revision, decision
recording, planning diagnostics/statistics, determinism, and the
`agent.planning → core[, agent]` dependency rule (zero import of Tool
Runtime, Skill Runtime, Hook Framework, or Workflow Runtime). Execution
(driving the goal FSM to completion) is a non-goal here (Bundle 3).
"""

from __future__ import annotations

import ast
import pathlib
from collections.abc import Iterator, Mapping
from typing import Any

import pytest

from agent.domain import AgentCapability, AgentGoal
from agent.planning import (
    PlanStep,
    ResolvedCapability,
    build_planning_diagnostics,
    compute_plan_hash,
    compute_planning_statistics,
    context_ref_for,
    decompose_goal,
    delegate_plan,
    delegate_step,
    plan_goal,
    query_status,
    record_decision,
    resolve_capabilities,
    revise_plan,
    select_workflow,
    send_signal,
)
from core.ports.context import AssembledContext, ContextItem, ContextRequest
from core.ports.reasoning import Decision, DecisionKind, ReasoningRequest


# ── fakes (duck-typed, mirroring workflow's own `_OkToolPort`-style tests) ────
class _FakeContextPort:
    def assemble(self, request: ContextRequest) -> AssembledContext:
        return AssembledContext(
            request=request,
            items=(ContextItem(kind="input", text=request.query or "", score=1.0),),
            truncated=False, used_tokens=10,
        )


class _FakeReasoningPort:
    def __init__(self, steps: tuple[dict[str, Any], ...] = ()) -> None:
        self._steps = steps
        self.requests: list[ReasoningRequest] = []

    def run(self, request: ReasoningRequest) -> Iterator[Decision]:
        self.requests.append(request)
        yield Decision(kind=DecisionKind.TOKEN, payload={"text": "thinking"})
        yield Decision(kind=DecisionKind.FINAL, payload={"steps": list(self._steps)})


class _FakeCapabilityResolver:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available

    def resolve(self, requirement: AgentCapability) -> ResolvedCapability:
        return ResolvedCapability(
            requirement=requirement,
            resolved_ref=f"{requirement.kind}:{requirement.tag}" if self.available else None,
            available=self.available,
        )


class _FakeWorkflowPort:
    def __init__(self) -> None:
        self.resolved: list[str] = []
        self.submitted: list[tuple[str, Mapping[str, Any]]] = []
        self.signals: list[tuple[str, str, Mapping[str, Any]]] = []
        self._statuses: dict[str, str] = {}

    def resolve(self, workflow_ref: str) -> str:
        self.resolved.append(workflow_ref)
        return f"handle:{workflow_ref}"

    def submit(self, handle: str, inputs: Mapping[str, Any]) -> str:
        self.submitted.append((handle, inputs))
        execution_id = f"exec:{handle}"
        self._statuses[execution_id] = "running"
        return execution_id

    def status(self, execution_id: str) -> str:
        return self._statuses.get(execution_id, "unknown")

    def signal(self, execution_id: str, name: str, payload: Mapping[str, Any]) -> None:
        self.signals.append((execution_id, name, payload))


def _goal(goal_id: str = "g1") -> AgentGoal:
    return AgentGoal(
        goal_id=goal_id, agent_id="ag1", conversation_id="c1", description="onboard a user",
    )


def _context_request(session_id: str = "s1") -> ContextRequest:
    return ContextRequest(session_id=session_id, query="onboard a user")


# ── goal decomposition ────────────────────────────────────────────────────────
def test_decompose_goal_parses_final_decision_into_steps():
    steps = (
        {"step_id": "s0", "kind": "delegate_workflow", "workflow_ref": "internal.onboard@1.0.0"},
    )
    reasoning = _FakeReasoningPort(steps=steps)
    plan, assembled = decompose_goal(
        _goal(), context_request=_context_request(), context_port=_FakeContextPort(),
        reasoning_port=reasoning, clock=lambda: 1.0,
    )
    assert len(plan.steps) == 1
    assert plan.steps[0].kind == "delegate_workflow"
    assert plan.steps[0].workflow_ref == "internal.onboard@1.0.0"
    assert plan.plan_hash != ""
    assert assembled.used_tokens == 10


def test_decompose_goal_ignores_non_final_decisions():
    reasoning = _FakeReasoningPort(steps=())
    plan, _ = decompose_goal(
        _goal(), context_request=_context_request(), context_port=_FakeContextPort(),
        reasoning_port=reasoning, clock=lambda: 1.0,
    )
    assert plan.steps == ()


def test_decompose_goal_assigns_generated_step_id_when_missing():
    steps = ({"kind": "await_result"},)
    reasoning = _FakeReasoningPort(steps=steps)
    plan, _ = decompose_goal(
        _goal(), context_request=_context_request(), context_port=_FakeContextPort(),
        reasoning_port=reasoning, clock=lambda: 1.0,
    )
    assert plan.steps[0].step_id != ""


def test_decompose_goal_builds_reasoning_request_with_context_ref():
    reasoning = _FakeReasoningPort(steps=())
    decompose_goal(
        _goal(), context_request=_context_request(session_id="s42"),
        context_port=_FakeContextPort(), reasoning_port=reasoning, clock=lambda: 1.0,
    )
    assert len(reasoning.requests) == 1
    assert reasoning.requests[0].session_id == "s42"
    assert reasoning.requests[0].context_ref != ""


def test_context_ref_is_deterministic_for_identical_assembled_context():
    assembled = AssembledContext(
        request=_context_request(), items=(ContextItem(kind="input", text="x", score=1.0),),
        truncated=False, used_tokens=5,
    )
    assert context_ref_for(assembled) == context_ref_for(assembled)


def test_decomposition_is_deterministic_for_identical_input():
    steps = ({"step_id": "s0", "kind": "delegate_workflow", "workflow_ref": "internal.wf@1.0.0"},)
    plan1, _ = decompose_goal(
        _goal(), context_request=_context_request(), context_port=_FakeContextPort(),
        reasoning_port=_FakeReasoningPort(steps=steps), clock=lambda: 1.0,
    )
    plan2, _ = decompose_goal(
        _goal(), context_request=_context_request(), context_port=_FakeContextPort(),
        reasoning_port=_FakeReasoningPort(steps=steps), clock=lambda: 1.0,
    )
    assert plan1.plan_hash == plan2.plan_hash
    assert plan1.steps == plan2.steps


# ── plan hashing ──────────────────────────────────────────────────────────────
def test_plan_hash_changes_with_step_content():
    a = _FakeReasoningPort(steps=({"kind": "await_result", "step_id": "s0"},))
    b = _FakeReasoningPort(steps=({"kind": "revise", "step_id": "s0"},))
    plan_a, _ = decompose_goal(
        _goal(), context_request=_context_request(), context_port=_FakeContextPort(),
        reasoning_port=a, clock=lambda: 1.0,
    )
    plan_b, _ = decompose_goal(
        _goal(), context_request=_context_request(), context_port=_FakeContextPort(),
        reasoning_port=b, clock=lambda: 1.0,
    )
    assert plan_a.plan_hash != plan_b.plan_hash


def test_plan_hash_is_never_caller_supplied():
    plan = decompose_goal(
        _goal(), context_request=_context_request(), context_port=_FakeContextPort(),
        reasoning_port=_FakeReasoningPort(steps=()), clock=lambda: 1.0,
    )[0]
    tampered = plan.with_hash("not-the-real-hash")
    assert compute_plan_hash(tampered) == compute_plan_hash(plan)
    assert compute_plan_hash(tampered) != "not-the-real-hash"


# ── capability resolution ─────────────────────────────────────────────────────
def test_resolve_capabilities_calls_resolver_per_capability():
    caps = (
        AgentCapability(kind="workflow", tag="onboarding"),
        AgentCapability(kind="tool", tag="send_email"),
    )
    resolved = resolve_capabilities(caps, resolver=_FakeCapabilityResolver())
    assert len(resolved) == 2
    assert all(r.available for r in resolved)
    assert resolved[0].resolved_ref == "workflow:onboarding"


def test_resolve_capabilities_reports_unavailable():
    caps = (AgentCapability(kind="workflow", tag="onboarding", required=False),)
    resolved = resolve_capabilities(caps, resolver=_FakeCapabilityResolver(available=False))
    assert resolved[0].available is False
    assert resolved[0].resolved_ref is None


# ── workflow selection + delegation ────────────────────────────────────────────
def test_select_workflow_resolves_via_workflow_port():
    port = _FakeWorkflowPort()
    handle = select_workflow("internal.onboard@1.0.0", workflow_port=port)
    assert handle == "handle:internal.onboard@1.0.0"
    assert port.resolved == ["internal.onboard@1.0.0"]


def test_delegate_step_resolves_then_submits():
    port = _FakeWorkflowPort()
    step = PlanStep(
        step_id="s0", kind="delegate_workflow", workflow_ref="internal.wf@1.0.0", inputs={"x": 1},
    )
    execution_id = delegate_step(step, workflow_port=port)
    assert execution_id == "exec:handle:internal.wf@1.0.0"
    assert port.submitted == [("handle:internal.wf@1.0.0", {"x": 1})]


def test_delegate_step_rejects_non_delegation_step():
    port = _FakeWorkflowPort()
    step = PlanStep(step_id="s0", kind="await_result")
    with pytest.raises(ValueError):
        delegate_step(step, workflow_port=port)


def test_query_status_and_send_signal_forward_through_the_port():
    port = _FakeWorkflowPort()
    step = PlanStep(step_id="s0", kind="delegate_workflow", workflow_ref="internal.wf@1.0.0")
    execution_id = delegate_step(step, workflow_port=port)
    assert query_status(execution_id, workflow_port=port) == "running"
    send_signal(execution_id, "resume", {"ok": True}, workflow_port=port)
    assert port.signals == [(execution_id, "resume", {"ok": True})]


def test_delegation_never_imports_workflow_graph_construction():
    import agent.planning.delegation as delegation_mod

    tree = ast.parse(pathlib.Path(delegation_mod.__file__).read_text())
    imported_names = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "WorkflowGraph" not in imported_names
    assert "topological_order" not in imported_names


# ── plan_goal / delegate_plan orchestration ────────────────────────────────────
def test_plan_goal_produces_plan_and_decision_trail():
    steps = (
        {"step_id": "s0", "kind": "delegate_workflow", "workflow_ref": "internal.onboard@1.0.0"},
    )
    result = plan_goal(
        _goal(), capabilities=(AgentCapability(kind="workflow", tag="onboarding"),),
        context_request=_context_request(), context_port=_FakeContextPort(),
        reasoning_port=_FakeReasoningPort(steps=steps),
        capability_resolver=_FakeCapabilityResolver(),
        clock=lambda: 1.0,
    )
    assert len(result.plan.steps) == 1
    assert len(result.resolved_capabilities) == 1
    kinds = [d.kind for d in result.decisions]
    assert kinds == ["resolve_capability", "decompose", "select_workflow"]


def test_delegate_plan_submits_every_delegation_step_and_records_decisions():
    plan, _ = decompose_goal(
        _goal(), context_request=_context_request(), context_port=_FakeContextPort(),
        reasoning_port=_FakeReasoningPort(steps=(
            {"step_id": "s0", "kind": "delegate_workflow", "workflow_ref": "internal.a@1.0.0"},
            {"step_id": "s1", "kind": "await_result"},
            {"step_id": "s2", "kind": "delegate_workflow", "workflow_ref": "internal.b@1.0.0"},
        )),
        clock=lambda: 1.0,
    )
    port = _FakeWorkflowPort()
    result = delegate_plan(plan, workflow_port=port, clock=lambda: 2.0)
    assert set(result.execution_ids) == {"s0", "s2"}
    assert len(result.decisions) == 2
    assert all(d.kind == "delegate" for d in result.decisions)


def test_delegate_plan_never_executes_await_result_or_revise_steps():
    plan, _ = decompose_goal(
        _goal(), context_request=_context_request(), context_port=_FakeContextPort(),
        reasoning_port=_FakeReasoningPort(steps=({"step_id": "s0", "kind": "await_result"},)),
        clock=lambda: 1.0,
    )
    port = _FakeWorkflowPort()
    result = delegate_plan(plan, workflow_port=port)
    assert result.execution_ids == {}
    assert port.submitted == []


# ── plan revision ─────────────────────────────────────────────────────────────
def test_revise_plan_increments_revision_and_recomputes_hash():
    plan, _ = decompose_goal(
        _goal(), context_request=_context_request(), context_port=_FakeContextPort(),
        reasoning_port=_FakeReasoningPort(steps=({"step_id": "s0", "kind": "await_result"},)),
        clock=lambda: 1.0,
    )
    revised_steps = (
        {"step_id": "s0", "kind": "delegate_workflow", "workflow_ref": "internal.x@1.0.0"},
    )
    revised, decision = revise_plan(
        plan, _goal(), context_request=_context_request(), context_port=_FakeContextPort(),
        reasoning_port=_FakeReasoningPort(steps=revised_steps),
        clock=lambda: 2.0,
    )
    assert revised.revision == plan.revision + 1
    assert revised.plan_id != plan.plan_id
    assert revised.plan_hash != ""
    assert decision.kind == "revise_plan"
    assert decision.goal_id == plan.goal_id


# ── decision recording ─────────────────────────────────────────────────────────
def test_record_decision_hashes_payload_never_stores_it_raw():
    decision = record_decision(
        goal_id="g1", kind="decompose", payload={"secret": "value"}, sequence=0, clock=lambda: 1.0,
    )
    assert decision.payload_hash != ""
    assert "secret" not in decision.payload_hash
    assert "value" not in decision.payload_hash


def test_record_decision_is_deterministic_for_identical_input():
    d1 = record_decision(
        goal_id="g1", kind="decompose", payload={"a": 1}, sequence=0, clock=lambda: 1.0,
    )
    d2 = record_decision(
        goal_id="g1", kind="decompose", payload={"a": 1}, sequence=0, clock=lambda: 1.0,
    )
    assert d1 == d2


# ── planning diagnostics + statistics ─────────────────────────────────────────
def test_planning_diagnostics_reports_counts():
    diag = build_planning_diagnostics(plans_generated=3, decisions_recorded=9)
    assert diag.healthy is True
    assert diag.plans_generated == 3
    assert diag.decisions_recorded == 9


def test_planning_statistics_aggregates_step_kinds():
    plan, _ = decompose_goal(
        _goal(), context_request=_context_request(), context_port=_FakeContextPort(),
        reasoning_port=_FakeReasoningPort(steps=(
            {"step_id": "s0", "kind": "delegate_workflow", "workflow_ref": "internal.a@1.0.0"},
            {"step_id": "s1", "kind": "delegate_workflow", "workflow_ref": "internal.b@1.0.0"},
        )),
        clock=lambda: 1.0,
    )
    stats = compute_planning_statistics((plan,))
    assert stats.plan_count == 1
    assert stats.per_step_kind == {"delegate_workflow": 2}
    assert stats.avg_step_count == 2.0


# ── dependency rules (agent.planning stays acyclic; no ownership leakage) ────
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


def test_agent_planning_dependency_rules():
    forbidden = {
        "runtime", "api", "gateway", "replay", "events", "memory", "knowledge",
        "prompt", "provider", "bridge", "pipeline", "voicebot", "observability",
        "tenant", "tool", "skill", "hooks", "workflow",
    }
    assert not (_imports("agent/planning") & forbidden), _imports("agent/planning")


def test_agent_planning_imports_only_core_and_own_package():
    allowed_extra = {
        "core", "__future__", "abc", "collections", "dataclasses", "enum", "typing", "hashlib",
    }
    non_stdlib_like = {t for t in _imports("agent/planning") if t not in allowed_extra}
    assert not non_stdlib_like, non_stdlib_like


def test_agent_execution_module_did_not_exist_as_of_bundle_2():
    """Historical record: at Bundle 2 time, `agent.execution` (the goal-
    driving engine) did not exist yet -- that was Bundle 3's own legitimate
    scope, and it exists from that bundle onward. This test now only
    documents the boundary rather than asserting the module's absence,
    since asserting absence would go stale the moment Bundle 3 landed (see
    `test_agent_execution.py` for that bundle's own suite)."""
    import importlib

    module = importlib.import_module("agent.execution")
    assert module is not None
