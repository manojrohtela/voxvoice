"""Agent Runtime Execution (M6.5B-3) tests.

Verifies the goal-scoped execution lifecycle (submit → decompose → delegate
→ await → complete/fail, revise-and-redelegate, cancellation, recovery),
workflow delegation via `AgentWorkflowPort` (never constructing/executing a
`WorkflowGraph`), replay (record + validate + compare, tamper detection),
diagnostics/statistics, serialization round-trips, determinism, and the
`agent.execution → core[, agent]` dependency rule. Execution state is
verified to live on `AgentGoalExecution`, never on `AgentIdentity`.
"""

from __future__ import annotations

import ast
import pathlib
from collections.abc import Iterator, Mapping
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from agent.domain import AgentCapability, AgentGoal, AgentGoalState
from agent.execution import (
    AgentExecutionConfiguration,
    AgentExecutionEngine,
    AgentExecutionFactory,
    AgentGoalExecution,
    AgentGoalResult,
    AgentReplayEngine,
    agent_replay_record_for,
    build_diagnostics,
    compute_statistics,
)
from agent.execution.serializer import AgentExecutionSerializer
from agent.planning import ResolvedCapability
from core.errors import ConfigError
from core.fsm import FSMError
from core.ports.context import AssembledContext, ContextItem, ContextRequest
from core.ports.reasoning import Decision, DecisionKind, ReasoningRequest


# ── fakes (duck-typed, mirroring workflow's own `_OkToolPort`-style tests) ────
class _FakeContextPort:
    def assemble(self, request: ContextRequest) -> AssembledContext:
        return AssembledContext(
            request=request,
            items=(ContextItem(kind="input", text=request.query or "", score=1.0),),
            truncated=False, used_tokens=5,
        )


class _FakeReasoningPort:
    def __init__(self, steps: tuple[dict[str, Any], ...] = ()) -> None:
        self._steps = steps

    def run(self, request: ReasoningRequest) -> Iterator[Decision]:
        yield Decision(kind=DecisionKind.FINAL, payload={"steps": list(self._steps)})


class _FakeCapabilityResolver:
    def resolve(self, requirement: AgentCapability) -> ResolvedCapability:
        return ResolvedCapability(
            requirement=requirement, resolved_ref=f"{requirement.kind}:{requirement.tag}",
            available=True,
        )


class _FakeWorkflowPort:
    def __init__(self) -> None:
        self._statuses: dict[str, str] = {}
        self._counter = 0

    def resolve(self, workflow_ref: str) -> str:
        return f"handle:{workflow_ref}"

    def submit(self, handle: str, inputs: Mapping[str, Any]) -> str:
        self._counter += 1
        execution_id = f"exec:{handle}:{self._counter}"
        self._statuses[execution_id] = "running"
        return execution_id

    def status(self, execution_id: str) -> str:
        return self._statuses.get(execution_id, "unknown")

    def signal(self, execution_id: str, name: str, payload: Mapping[str, Any]) -> None:
        pass

    def set_status(self, execution_id: str, status: str) -> None:
        self._statuses[execution_id] = status


def _goal(goal_id: str = "g1") -> AgentGoal:
    return AgentGoal(
        goal_id=goal_id, agent_id="ag1", conversation_id="c1", description="do a thing",
    )


def _one_step_plan_reasoning(workflow_ref: str = "internal.wf@1.0.0") -> _FakeReasoningPort:
    return _FakeReasoningPort(steps=(
        {"step_id": "s0", "kind": "delegate_workflow", "workflow_ref": workflow_ref},
    ))


def _engine(reasoning: _FakeReasoningPort, port: _FakeWorkflowPort, **kw) -> AgentExecutionEngine:
    return AgentExecutionEngine(
        workflow_port=port, capability_resolver=_FakeCapabilityResolver(),
        reasoning_port=reasoning, context_port=_FakeContextPort(), clock=lambda: 1.0, **kw,
    )


# ── domain immutability ───────────────────────────────────────────────────────
def test_goal_execution_is_immutable():
    e = AgentGoalExecution(
        execution_id="e1", goal_id="g1", agent_id="a1", conversation_id="c1",
        state=AgentGoalState.RECEIVED,
    )
    with pytest.raises(FrozenInstanceError):
        e.state = AgentGoalState.COMPLETED  # type: ignore[misc]


def test_execution_state_lives_on_execution_record_never_on_identity():
    """§7.1 of the M6.5A design: execution state lives on `AgentGoalExecution`,
    never on `AgentIdentity`."""
    from dataclasses import fields

    from agent.domain import AgentIdentity

    names = {f.name for f in fields(AgentIdentity)}
    assert "state" not in names
    exec_names = {f.name for f in fields(AgentGoalExecution)}
    assert "state" in exec_names


# ── submit → complete ──────────────────────────────────────────────────────────
def test_submit_reaches_awaiting_workflow():
    port = _FakeWorkflowPort()
    engine = _engine(_one_step_plan_reasoning(), port)
    execution, result, snapshot = engine.submit(
        _goal(), capabilities=(), context_request=ContextRequest(session_id="c1"),
    )
    assert execution.state is AgentGoalState.AWAITING_WORKFLOW
    assert result is None
    assert execution.delegated_execution_id is not None
    assert snapshot.plan is not None


def test_poll_completes_when_workflow_succeeds():
    port = _FakeWorkflowPort()
    engine = _engine(_one_step_plan_reasoning(), port)
    execution, _, snapshot = engine.submit(
        _goal(), capabilities=(), context_request=ContextRequest(session_id="c1"),
    )
    port.set_status(execution.delegated_execution_id, "completed")
    execution, result, snapshot = engine.poll(execution, snapshot, _goal())
    assert execution.state is AgentGoalState.COMPLETED
    assert result is not None
    assert result.error is None
    assert result.partial_success is True


def test_poll_returns_no_result_while_workflow_still_running():
    port = _FakeWorkflowPort()
    engine = _engine(_one_step_plan_reasoning(), port)
    execution, _, snapshot = engine.submit(
        _goal(), capabilities=(), context_request=ContextRequest(session_id="c1"),
    )
    execution, result, snapshot = engine.poll(execution, snapshot, _goal())
    assert execution.state is AgentGoalState.AWAITING_WORKFLOW
    assert result is None


def test_poll_rejects_non_awaiting_state():
    port = _FakeWorkflowPort()
    engine = _engine(_one_step_plan_reasoning(), port)
    execution, _, snapshot = engine.submit(
        _goal(), capabilities=(), context_request=ContextRequest(session_id="c1"),
    )
    port.set_status(execution.delegated_execution_id, "completed")
    execution, _, snapshot = engine.poll(execution, snapshot, _goal())
    with pytest.raises(ValueError):
        engine.poll(execution, snapshot, _goal())


# ── failure handling: revise-or-fail ──────────────────────────────────────────
def test_workflow_failure_triggers_revision_within_budget():
    port = _FakeWorkflowPort()
    engine = _engine(_one_step_plan_reasoning(), port, max_revisions=1)
    execution, _, snapshot = engine.submit(
        _goal(), capabilities=(), context_request=ContextRequest(session_id="c1"),
    )
    port.set_status(execution.delegated_execution_id, "failed")
    execution, result, snapshot = engine.poll(execution, snapshot, _goal())
    assert execution.state is AgentGoalState.AWAITING_WORKFLOW
    assert result is None
    assert snapshot.plan.revision == 1
    assert execution.delegated_execution_id is not None


def test_workflow_failure_fails_goal_once_revision_budget_exhausted():
    port = _FakeWorkflowPort()
    engine = _engine(_one_step_plan_reasoning(), port, max_revisions=0)
    execution, _, snapshot = engine.submit(
        _goal(), capabilities=(), context_request=ContextRequest(session_id="c1"),
    )
    port.set_status(execution.delegated_execution_id, "failed")
    execution, result, snapshot = engine.poll(execution, snapshot, _goal())
    assert execution.state is AgentGoalState.FAILED
    assert result is not None
    assert result.error is not None
    assert snapshot.pending_error == result.error


def test_revision_then_second_failure_exhausts_budget_and_fails():
    port = _FakeWorkflowPort()
    engine = _engine(_one_step_plan_reasoning(), port, max_revisions=1)
    execution, _, snapshot = engine.submit(
        _goal(), capabilities=(), context_request=ContextRequest(session_id="c1"),
    )
    port.set_status(execution.delegated_execution_id, "failed")
    execution, _, snapshot = engine.poll(execution, snapshot, _goal())
    assert execution.state is AgentGoalState.AWAITING_WORKFLOW  # revised once

    port.set_status(execution.delegated_execution_id, "failed")
    execution, result, snapshot = engine.poll(execution, snapshot, _goal())
    assert execution.state is AgentGoalState.FAILED
    assert result.error is not None


# ── cancellation ──────────────────────────────────────────────────────────────
def test_cancel_reaches_abandoned_from_awaiting_workflow():
    port = _FakeWorkflowPort()
    engine = _engine(_one_step_plan_reasoning(), port)
    execution, _, snapshot = engine.submit(
        _goal(), capabilities=(), context_request=ContextRequest(session_id="c1"),
    )
    execution, result, snapshot = engine.cancel(execution, snapshot)
    assert execution.state is AgentGoalState.ABANDONED
    assert result.error == "cancelled"


# ── recovery: session/process continuity, never restarts from RECEIVED ───────
def test_recover_continues_polling_without_restarting_decomposition():
    port = _FakeWorkflowPort()
    reasoning = _one_step_plan_reasoning()
    engine = _engine(reasoning, port)
    execution, _, snapshot = engine.submit(
        _goal(), capabilities=(), context_request=ContextRequest(session_id="c1"),
    )
    original_plan_id = snapshot.plan.plan_id
    port.set_status(execution.delegated_execution_id, "completed")

    # Simulate a process restart: a *new* engine (fresh reasoning call count),
    # recovering purely from the persisted execution/snapshot.
    fresh_engine = _engine(reasoning, port)
    execution, result, snapshot = fresh_engine.recover(execution, snapshot, _goal())
    assert execution.state is AgentGoalState.COMPLETED
    assert result is not None
    # The plan used for delegation is untouched by recovery -- no re-decomposition.
    assert snapshot.plan.plan_id == original_plan_id


def test_recover_on_non_awaiting_state_returns_execution_unchanged():
    port = _FakeWorkflowPort()
    engine = _engine(_one_step_plan_reasoning(), port)
    execution, _, snapshot = engine.submit(
        _goal(), capabilities=(), context_request=ContextRequest(session_id="c1"),
    )
    port.set_status(execution.delegated_execution_id, "completed")
    execution, _, snapshot = engine.poll(execution, snapshot, _goal())  # -> COMPLETED
    recovered_execution, result, recovered_snapshot = engine.recover(execution, snapshot, _goal())
    assert recovered_execution == execution
    assert result is None


def test_goal_lifecycle_terminal_states_refuse_further_triggers():
    port = _FakeWorkflowPort()
    engine = _engine(_one_step_plan_reasoning(), port)
    execution, _, snapshot = engine.submit(
        _goal(), capabilities=(), context_request=ContextRequest(session_id="c1"),
    )
    port.set_status(execution.delegated_execution_id, "completed")
    execution, _, snapshot = engine.poll(execution, snapshot, _goal())
    with pytest.raises(FSMError):
        engine.cancel(execution, snapshot)


# ── send_signal ────────────────────────────────────────────────────────────────
def test_send_signal_requires_a_delegated_execution():
    port = _FakeWorkflowPort()
    engine = _engine(_one_step_plan_reasoning(), port)
    execution = AgentGoalExecution(
        execution_id="e1", goal_id="g1", agent_id="a1", conversation_id="c1",
        state=AgentGoalState.AWAITING_WORKFLOW,
    )
    with pytest.raises(ValueError):
        engine.send_signal(execution, "resume", {})


def test_send_signal_forwards_through_workflow_port():
    port = _FakeWorkflowPort()
    engine = _engine(_one_step_plan_reasoning(), port)
    execution, _, snapshot = engine.submit(
        _goal(), capabilities=(), context_request=ContextRequest(session_id="c1"),
    )
    engine.send_signal(execution, "resume", {"ok": True})  # no exception


# ── delegation never constructs/executes a WorkflowGraph ─────────────────────
def test_engine_module_never_imports_workflow_graph_construction():
    import agent.execution.engine as engine_mod

    tree = ast.parse(pathlib.Path(engine_mod.__file__).read_text())
    imported_names = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "WorkflowGraph" not in imported_names
    assert "topological_order" not in imported_names


# ── determinism ──────────────────────────────────────────────────────────────
def test_submit_is_deterministic_for_identical_input():
    port1, port2 = _FakeWorkflowPort(), _FakeWorkflowPort()
    e1 = _engine(_one_step_plan_reasoning(), port1)
    e2 = _engine(_one_step_plan_reasoning(), port2)
    execution1, _, snapshot1 = e1.submit(
        _goal(), capabilities=(), context_request=ContextRequest(session_id="c1"),
    )
    execution2, _, snapshot2 = e2.submit(
        _goal(), capabilities=(), context_request=ContextRequest(session_id="c1"),
    )
    assert execution1.execution_id == execution2.execution_id
    assert snapshot1.plan.plan_hash == snapshot2.plan.plan_hash


# ── replay ────────────────────────────────────────────────────────────────────
def test_replay_record_validates_successfully_when_untampered():
    port = _FakeWorkflowPort()
    engine = _engine(_one_step_plan_reasoning(), port)
    execution, _, snapshot = engine.submit(
        _goal(), capabilities=(), context_request=ContextRequest(session_id="c1"),
    )
    port.set_status(execution.delegated_execution_id, "completed")
    execution, result, snapshot = engine.poll(execution, snapshot, _goal())

    record = agent_replay_record_for(snapshot.decisions, result, now=2.0)
    comparison = AgentReplayEngine().validate(record)
    assert comparison.matches is True


def test_replay_record_detects_tampering():
    port = _FakeWorkflowPort()
    engine = _engine(_one_step_plan_reasoning(), port)
    execution, _, snapshot = engine.submit(
        _goal(), capabilities=(), context_request=ContextRequest(session_id="c1"),
    )
    port.set_status(execution.delegated_execution_id, "completed")
    execution, result, snapshot = engine.poll(execution, snapshot, _goal())

    record = agent_replay_record_for(snapshot.decisions, result, now=2.0)
    from dataclasses import replace

    tampered = replace(record, decision_hashes=(*record.decision_hashes, "extra"))
    comparison = AgentReplayEngine().validate(tampered)
    assert comparison.matches is False
    assert comparison.mismatches == ("replay_hash",)


def test_replay_compare_detects_divergence():
    port1, port2 = _FakeWorkflowPort(), _FakeWorkflowPort()
    engine1 = _engine(_one_step_plan_reasoning("internal.a@1.0.0"), port1)
    engine2 = _engine(_one_step_plan_reasoning("internal.b@1.0.0"), port2)

    ctx_request = ContextRequest(session_id="c1")
    e1, _, s1 = engine1.submit(_goal(), capabilities=(), context_request=ctx_request)
    port1.set_status(e1.delegated_execution_id, "completed")
    e1, r1, s1 = engine1.poll(e1, s1, _goal())

    e2, _, s2 = engine2.submit(_goal(), capabilities=(), context_request=ctx_request)
    port2.set_status(e2.delegated_execution_id, "completed")
    e2, r2, s2 = engine2.poll(e2, s2, _goal())

    record1 = agent_replay_record_for(s1.decisions, r1, now=2.0)
    record2 = agent_replay_record_for(s2.decisions, r2, now=2.0)
    comparison = AgentReplayEngine().compare(record1, record2)
    assert comparison.matches is False


# ── diagnostics + statistics ───────────────────────────────────────────────────
def test_build_diagnostics_reports_counts():
    config = AgentExecutionConfiguration.default()
    diag = build_diagnostics(config=config, execution_count=2, replay_count=1)
    assert diag.health_summary == "healthy"
    assert diag.execution_stats["count"] == 2
    assert diag.replay_stats["count"] == 1


def test_compute_statistics_aggregates_final_states():
    results = (
        AgentGoalResult(
            execution_id="e1", goal_id="g1", final_state=AgentGoalState.COMPLETED,
            plan_id="p1", delegated_execution_id="d1", partial_success=True,
        ),
        AgentGoalResult(
            execution_id="e2", goal_id="g2", final_state=AgentGoalState.FAILED,
            plan_id="p2", delegated_execution_id="d2", partial_success=False, error="boom",
        ),
    )
    stats = compute_statistics(results)
    assert stats.execution_count == 2
    assert stats.failure_count == 1
    assert stats.per_final_state == {"completed": 1, "failed": 1}


# ── configuration ─────────────────────────────────────────────────────────────
def test_configuration_rejects_negative_max_revisions():
    from dataclasses import replace

    with pytest.raises(ConfigError):
        replace(AgentExecutionConfiguration.default(), max_revisions=-1)


def test_configuration_rejects_non_positive_ttl():
    from dataclasses import replace

    with pytest.raises(ConfigError):
        replace(AgentExecutionConfiguration.default(), execution_ttl_s=0)


def test_configuration_fingerprint_is_deterministic():
    a = AgentExecutionConfiguration.default()
    b = AgentExecutionConfiguration.default()
    assert a.fingerprint() == b.fingerprint()


# ── serialization round-trips ─────────────────────────────────────────────────
def test_execution_serialization_round_trips():
    ser = AgentExecutionSerializer()
    e = AgentGoalExecution(
        execution_id="e1", goal_id="g1", agent_id="a1", conversation_id="c1",
        state=AgentGoalState.AWAITING_WORKFLOW, plan_id="p1",
        delegated_execution_id="d1", started_at=1.0, updated_at=2.0,
    )
    back = ser.execution_from_dict(ser.execution_to_dict(e))
    assert back == e


def test_snapshot_serialization_round_trips_with_plan_and_decisions():
    port = _FakeWorkflowPort()
    engine = _engine(_one_step_plan_reasoning(), port)
    execution, _, snapshot = engine.submit(
        _goal(), capabilities=(), context_request=ContextRequest(session_id="c1"),
    )
    ser = AgentExecutionSerializer()
    back = ser.snapshot_from_dict(ser.snapshot_to_dict(snapshot))
    assert back == snapshot


def test_replay_record_serialization_round_trips():
    ser = AgentExecutionSerializer()
    result = AgentGoalResult(
        execution_id="e1", goal_id="g1", final_state=AgentGoalState.COMPLETED,
        plan_id="p1", delegated_execution_id="d1", partial_success=True,
    )
    record = agent_replay_record_for((), result, now=1.0)
    back = ser.replay_from_dict(ser.replay_to_dict(record))
    assert back == record


# ── factory + runtime facade (end-to-end via the repository) ─────────────────
def test_factory_end_to_end_submit_and_poll_persist_via_repository():
    port = _FakeWorkflowPort()
    rt = AgentExecutionFactory.default(
        workflow_port=port, capability_resolver=_FakeCapabilityResolver(),
        reasoning_port=_one_step_plan_reasoning(), context_port=_FakeContextPort(),
        clock=lambda: 1.0,
    )
    execution, result = rt.submit_goal(
        _goal(), capabilities=(), context_request=ContextRequest(session_id="c1"),
    )
    assert execution.state is AgentGoalState.AWAITING_WORKFLOW
    assert result is None

    port.set_status(execution.delegated_execution_id, "completed")
    execution, result = rt.poll_goal(execution.execution_id, _goal())
    assert execution.state is AgentGoalState.COMPLETED
    assert result is not None

    diag = rt.diagnostics()
    assert diag.execution_stats["count"] == 1


def test_runtime_facade_has_no_workflow_execution_surface():
    forbidden = {"execute_workflow", "run_graph", "topological_order"}
    from agent.execution import AgentExecutionRuntime

    assert not (forbidden & set(dir(AgentExecutionRuntime)))


# ── dependency rules ───────────────────────────────────────────────────────────
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


def test_agent_execution_dependency_rules():
    forbidden = {
        "runtime", "api", "gateway", "replay", "events", "memory", "knowledge",
        "prompt", "provider", "bridge", "pipeline", "voicebot", "observability",
        "tenant", "tool", "skill", "hooks", "workflow",
    }
    assert not (_imports("agent/execution") & forbidden), _imports("agent/execution")


def test_agent_execution_imports_only_core_and_own_package():
    allowed_extra = {
        "core", "__future__", "abc", "collections", "dataclasses", "enum", "typing",
        "hashlib", "copy", "time",
    }
    non_stdlib_like = {t for t in _imports("agent/execution") if t not in allowed_extra}
    assert not non_stdlib_like, non_stdlib_like


def test_bundle_1_and_2_files_untouched_still_expose_expected_symbols():
    """A cheap regression guard, not a git diff -- confirms Bundle 1/2's own
    public surface is unchanged by importing their still-expected symbols."""
    from agent import AgentDescriptor, AgentManifest, AgentRegistry  # Bundle 1
    from agent.planning import AgentPlan, plan_goal  # Bundle 2

    assert AgentManifest and AgentDescriptor and AgentRegistry and AgentPlan and plan_goal
