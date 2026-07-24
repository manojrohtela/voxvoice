"""Workflow Runtime Execution (M6.4B-2) tests.

Verifies the execution lifecycle FSM (the dual-FSM restoration), checkpoint
creation (checkpoint-after-every-completed-node, deterministic hashing),
recovery (never restarts from the beginning), replay (pure hash-chain
re-verification), deterministic execution (conditional branching, parallel
fan-out/merge), cancellation, retry, timeout, serialization round-trips, the
durable repository, and the `workflow -> core[, tenant]` dependency rule
extended to `workflow/execution/` (zero import of Tool Runtime, Skill
Runtime, or Hook Framework).
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import FrozenInstanceError

import pytest

from core.errors import ConfigError
from core.fsm import FSMError
from core.policies import RetryPolicy, TimeoutPolicy
from core.ports.tool import ToolResult
from workflow import (
    WorkflowCondition,
    WorkflowConditionOperator,
    WorkflowEdge,
    WorkflowFactory,
    WorkflowFailurePolicy,
    WorkflowGraph,
    WorkflowManifest,
    WorkflowNode,
    WorkflowNodeKind,
)
from workflow.execution import (
    WORKFLOW_EXECUTION_LIFECYCLE,
    WorkflowCancellationToken,
    WorkflowExecution,
    WorkflowExecutionConfiguration,
    WorkflowExecutionEngine,
    WorkflowExecutionFactory,
    WorkflowExecutionRepository,
    WorkflowExecutionRuntime,
    WorkflowExecutionSerializer,
    WorkflowExecutionState,
    WorkflowExecutionTrigger,
    advance_execution,
    build_checkpoint,
    compute_variables_hash,
    evaluate_condition,
    recover_execution,
    workflow_replay_record_for,
)
from workflow.execution.domain import (
    WorkflowExecutionContext,
    WorkflowExecutionResult,
    WorkflowSnapshot,
)
from workflow.execution.replay_engine import WorkflowReplayEngine
from workflow.stores import InMemoryDocumentStore


# ── fakes for tool/skill ports ──────────────────────────────────────────────────
class _OkToolPort:
    async def invoke(self, call):
        return ToolResult(call_id=call.call_id, ok=True, output={"echo": dict(call.arguments)})


class _FailingToolPort:
    def __init__(self, error: str = "boom") -> None:
        self._error = error

    async def invoke(self, call):
        return ToolResult(call_id=call.call_id, ok=False, error=self._error)


class _FlakyToolPort:
    def __init__(self, fail_times: int) -> None:
        self._remaining = fail_times
        self.calls = 0

    async def invoke(self, call):
        self.calls += 1
        if self._remaining > 0:
            self._remaining -= 1
            return ToolResult(call_id=call.call_id, ok=False, error="transient")
        return ToolResult(call_id=call.call_id, ok=True, output={"ok": True})


class _SkillPort:
    def run_skill(self, ref, variables):
        return {"ref": list(ref)}


def _register(rt, graph, id_="wf", **kw):
    manifest = WorkflowManifest(id=id_, namespace="internal", version="1.0.0", graph=graph, **kw)
    rt.register(manifest)
    rt.activate("internal", id_, "1.0.0")
    return manifest


def _base_registry(clock=lambda: 1.0):
    return WorkflowFactory.default(clock=clock).registry


def _exec_runtime(registry, **kwargs) -> WorkflowExecutionRuntime:
    kwargs.setdefault("clock", lambda: 1.0)
    return WorkflowExecutionFactory.default(registry, **kwargs)


def _linear_tool_graph(retry=None, failure_policy=WorkflowFailurePolicy.PROPAGATE, timeout=None):
    node = WorkflowNode(
        node_id="a", kind=WorkflowNodeKind.TOOL, tool_ref=("internal", "t", "1.0.0"),
        retry_policy=retry, failure_policy=failure_policy, timeout_policy=timeout,
    )
    return WorkflowGraph(entry_node="a", nodes=(node,), edges=())


# ── Execution lifecycle FSM ──────────────────────────────────────────────────────
def test_execution_lifecycle_start_to_complete():
    e = WorkflowExecution(
        execution_id="e1", namespace="internal", workflow_id="wf", version="1.0.0", graph_hash="h",
    )
    e = advance_execution(e, WorkflowExecutionTrigger.START, clock=lambda: 1.0)
    assert e.state is WorkflowExecutionState.RUNNING
    e = advance_execution(e, WorkflowExecutionTrigger.COMPLETE, clock=lambda: 2.0)
    assert e.state is WorkflowExecutionState.COMPLETED
    assert e.updated_at == 2.0


def test_execution_lifecycle_pause_resume():
    e = WorkflowExecution(
        execution_id="e1", namespace="internal", workflow_id="wf", version="1.0.0", graph_hash="h",
        state=WorkflowExecutionState.RUNNING,
    )
    e = advance_execution(e, WorkflowExecutionTrigger.PAUSE, clock=lambda: 1.0)
    assert e.state is WorkflowExecutionState.PAUSED
    e = advance_execution(e, WorkflowExecutionTrigger.RESUME, clock=lambda: 1.0)
    assert e.state is WorkflowExecutionState.RUNNING


def test_execution_lifecycle_failure_recovery_path():
    e = WorkflowExecution(
        execution_id="e1", namespace="internal", workflow_id="wf", version="1.0.0", graph_hash="h",
        state=WorkflowExecutionState.RUNNING,
    )
    e = advance_execution(e, WorkflowExecutionTrigger.NODE_FAILED, clock=lambda: 1.0)
    assert e.state is WorkflowExecutionState.RECOVERING
    recovered = advance_execution(e, WorkflowExecutionTrigger.RECOVER, clock=lambda: 1.0)
    assert recovered.state is WorkflowExecutionState.RUNNING


def test_execution_lifecycle_compensation_path_is_terminal():
    e = WorkflowExecution(
        execution_id="e1", namespace="internal", workflow_id="wf", version="1.0.0", graph_hash="h",
        state=WorkflowExecutionState.RECOVERING,
    )
    e = advance_execution(e, WorkflowExecutionTrigger.COMPENSATE, clock=lambda: 1.0)
    assert e.state is WorkflowExecutionState.COMPENSATING
    e = advance_execution(e, WorkflowExecutionTrigger.FAIL, clock=lambda: 1.0)
    assert e.state is WorkflowExecutionState.FAILED


def test_execution_lifecycle_cancel_from_any_nonterminal_state():
    for state in (
        WorkflowExecutionState.PENDING, WorkflowExecutionState.RUNNING,
        WorkflowExecutionState.PAUSED, WorkflowExecutionState.RECOVERING,
        WorkflowExecutionState.COMPENSATING,
    ):
        e = WorkflowExecution(
            execution_id="e1", namespace="internal", workflow_id="wf", version="1.0.0",
            graph_hash="h", state=state,
        )
        cancelled = advance_execution(e, WorkflowExecutionTrigger.CANCEL, clock=lambda: 1.0)
        assert cancelled.state is WorkflowExecutionState.CANCELLED


def test_execution_lifecycle_rejects_illegal_transition():
    e = WorkflowExecution(
        execution_id="e1", namespace="internal", workflow_id="wf", version="1.0.0", graph_hash="h",
    )
    with pytest.raises(FSMError):
        advance_execution(e, WorkflowExecutionTrigger.COMPLETE, clock=lambda: 1.0)


def test_execution_and_descriptor_lifecycles_are_fully_distinct():
    from workflow.lifecycle import WorkflowLifecycleState

    assert set(WORKFLOW_EXECUTION_LIFECYCLE.states) == set(WorkflowExecutionState)
    assert not (set(WorkflowExecutionState) & set(WorkflowLifecycleState))


# ── WorkflowExecutionContext (structural extension) ────────────────────────────
def test_execution_context_carries_skill_context_field_names():
    ctx = WorkflowExecutionContext(context_id="c1", execution_id="e1")
    for field_name in (
        "context_id", "plan_id", "root", "namespace", "org_id", "tenant_id",
        "requested_by", "created_at", "capability_requirements", "metadata",
    ):
        assert hasattr(ctx, field_name)
    for field_name in ("execution_id", "graph_hash", "current_node_id", "checkpoint_id",
                       "retry_count"):
        assert hasattr(ctx, field_name)


def test_execution_context_is_immutable():
    ctx = WorkflowExecutionContext(context_id="c1")
    with pytest.raises(FrozenInstanceError):
        ctx.context_id = "other"  # type: ignore[misc]


# ── Deterministic condition evaluation ──────────────────────────────────────────
@pytest.mark.parametrize(
    ("op", "actual", "value", "expected"),
    [
        (WorkflowConditionOperator.EQ, 1, 1, True),
        (WorkflowConditionOperator.EQ, 1, 2, False),
        (WorkflowConditionOperator.NEQ, 1, 2, True),
        (WorkflowConditionOperator.GT, 5, 2, True),
        (WorkflowConditionOperator.LT, 2, 5, True),
        (WorkflowConditionOperator.GTE, 2, 2, True),
        (WorkflowConditionOperator.LTE, 2, 2, True),
        (WorkflowConditionOperator.IN, 2, [1, 2, 3], True),
    ],
)
def test_evaluate_condition_operators(op, actual, value, expected):
    cond = WorkflowCondition(field="x", operator=op, value=value)
    assert evaluate_condition(cond, {"x": actual}) is expected


def test_evaluate_condition_exists():
    cond = WorkflowCondition(field="x", operator=WorkflowConditionOperator.EXISTS)
    assert evaluate_condition(cond, {"x": 1}) is True
    assert evaluate_condition(cond, {}) is False


def test_evaluate_condition_none_is_always_true():
    assert evaluate_condition(None, {}) is True


def test_evaluate_condition_type_mismatch_is_false_not_raised():
    cond = WorkflowCondition(field="x", operator=WorkflowConditionOperator.GT, value=5)
    assert evaluate_condition(cond, {"x": "not-a-number"}) is False


# ── Checkpoint hashing ───────────────────────────────────────────────────────────
def test_checkpoint_hash_deterministic():
    c1 = build_checkpoint("e1", "a", 1, {"x": 1}, now=1.0)
    c2 = build_checkpoint("e1", "a", 1, {"x": 1}, now=2.0)  # `now` never enters the hash
    assert c1.checkpoint_hash == c2.checkpoint_hash
    assert c1.variables_hash == compute_variables_hash({"x": 1})


def test_checkpoint_hash_changes_with_variables():
    c1 = build_checkpoint("e1", "a", 1, {"x": 1})
    c2 = build_checkpoint("e1", "a", 1, {"x": 2})
    assert c1.checkpoint_hash != c2.checkpoint_hash


# ── Deterministic execution: linear, conditional, parallel/merge ──────────────
def test_run_linear_tool_and_skill_graph():
    nodes = (
        WorkflowNode(
            node_id="a", kind=WorkflowNodeKind.TOOL, tool_ref=("internal", "t", "1.0.0"),
        ),
        WorkflowNode(
            node_id="b", kind=WorkflowNodeKind.SKILL, skill_ref=("internal", "s", "1.0.0"),
        ),
    )
    graph = WorkflowGraph(
        entry_node="a", nodes=nodes, edges=(WorkflowEdge(source="a", target="b"),),
    )
    manifest = WorkflowManifest(id="wf", namespace="internal", version="1.0.0", graph=graph)
    rt = WorkflowFactory.default(clock=lambda: 1.0)
    rt.register(manifest)
    rt.activate("internal", "wf", "1.0.0")

    exec_rt = _exec_runtime(rt.registry, tool_port=_OkToolPort(), skill_port=_SkillPort())
    execution, result = exec_rt.start("internal", "wf")
    assert execution.state is WorkflowExecutionState.COMPLETED
    assert result.completed_nodes == ("a", "b")
    assert result.partial_success is True


def test_conditional_branching_takes_only_the_true_edge():
    nodes = (
        WorkflowNode(node_id="check", kind=WorkflowNodeKind.CONDITIONAL),
        WorkflowNode(node_id="yes", kind=WorkflowNodeKind.DELAY, delay_ms=1),
        WorkflowNode(node_id="no", kind=WorkflowNodeKind.DELAY, delay_ms=2),
    )
    edges = (
        WorkflowEdge(
            source="check", target="yes",
            condition=WorkflowCondition(
                field="flag", operator=WorkflowConditionOperator.EQ, value=True,
            ),
        ),
        WorkflowEdge(
            source="check", target="no",
            condition=WorkflowCondition(
                field="flag", operator=WorkflowConditionOperator.EQ, value=False,
            ),
        ),
    )
    graph = WorkflowGraph(entry_node="check", nodes=nodes, edges=edges)
    rt = WorkflowFactory.default(clock=lambda: 1.0)
    _register(rt, graph, id_="cond")
    exec_rt = _exec_runtime(rt.registry)
    execution, result = exec_rt.start("internal", "cond", initial_variables={"flag": True})
    assert result.completed_nodes == ("check", "yes")


def test_parallel_fan_out_and_merge_is_deterministic_and_reproducible():
    nodes = (
        WorkflowNode(node_id="start", kind=WorkflowNodeKind.PARALLEL),
        WorkflowNode(node_id="left", kind=WorkflowNodeKind.DELAY, delay_ms=1),
        WorkflowNode(node_id="right", kind=WorkflowNodeKind.DELAY, delay_ms=2),
        WorkflowNode(node_id="merge", kind=WorkflowNodeKind.MERGE),
    )
    edges = (
        WorkflowEdge(source="start", target="left"),
        WorkflowEdge(source="start", target="right"),
        WorkflowEdge(source="left", target="merge"),
        WorkflowEdge(source="right", target="merge"),
    )
    graph = WorkflowGraph(entry_node="start", nodes=nodes, edges=edges)
    rt = WorkflowFactory.default(clock=lambda: 1.0)
    _register(rt, graph, id_="parallel")

    exec_rt1 = _exec_runtime(rt.registry)
    _, r1 = exec_rt1.start("internal", "parallel")
    exec_rt2 = _exec_runtime(rt.registry)
    _, r2 = exec_rt2.start("internal", "parallel")
    assert r1.completed_nodes == r2.completed_nodes == ("start", "left", "right", "merge")


# ── Event pause + resume (recovery never restarts from the beginning) ────────
def test_event_node_pauses_then_resumes_from_checkpoint():
    nodes = (
        WorkflowNode(node_id="wait", kind=WorkflowNodeKind.EVENT, event_name="approval"),
        WorkflowNode(node_id="after", kind=WorkflowNodeKind.DELAY, delay_ms=1),
    )
    graph = WorkflowGraph(
        entry_node="wait", nodes=nodes, edges=(WorkflowEdge(source="wait", target="after"),),
    )
    rt = WorkflowFactory.default(clock=lambda: 1.0)
    _register(rt, graph, id_="approval")
    exec_rt = _exec_runtime(rt.registry)

    execution, result = exec_rt.start("internal", "approval")
    assert execution.state is WorkflowExecutionState.PAUSED
    assert result is None

    execution2, result2 = exec_rt.resume_after_event(execution.execution_id, {"approved": True})
    assert execution2.state is WorkflowExecutionState.COMPLETED
    assert result2.completed_nodes == ("wait", "after")
    assert result2.variables["wait"] == {"approved": True}


def test_resume_never_restarts_the_graph():
    """Resuming after a pause must not re-execute the already-completed
    node(s) before the pause point."""

    class _CountingToolPort:
        def __init__(self) -> None:
            self.calls = 0

        async def invoke(self, call):
            self.calls += 1
            return ToolResult(call_id=call.call_id, ok=True, output={"n": self.calls})

    nodes = (
        WorkflowNode(node_id="a", kind=WorkflowNodeKind.TOOL, tool_ref=("internal", "t", "1.0.0")),
        WorkflowNode(node_id="wait", kind=WorkflowNodeKind.EVENT, event_name="go"),
    )
    graph = WorkflowGraph(
        entry_node="a", nodes=nodes, edges=(WorkflowEdge(source="a", target="wait"),),
    )
    rt = WorkflowFactory.default(clock=lambda: 1.0)
    _register(rt, graph, id_="restart_check")
    port = _CountingToolPort()
    exec_rt = _exec_runtime(rt.registry, tool_port=port)
    execution, _ = exec_rt.start("internal", "restart_check")
    assert execution.state is WorkflowExecutionState.PAUSED
    assert port.calls == 1

    execution2, result2 = exec_rt.resume_after_event(execution.execution_id, {})
    assert execution2.state is WorkflowExecutionState.COMPLETED
    assert port.calls == 1  # node "a" was never re-executed on resume
    assert result2.completed_nodes == ("a", "wait")


def test_recover_resumes_from_last_checkpoint_not_from_entry_node():
    nodes = (
        WorkflowNode(node_id="a", kind=WorkflowNodeKind.DELAY, delay_ms=1),
        WorkflowNode(
            node_id="b", kind=WorkflowNodeKind.TOOL, tool_ref=("internal", "t", "1.0.0"),
            retry_policy=RetryPolicy(max_attempts=1),
        ),
    )
    graph = WorkflowGraph(
        entry_node="a", nodes=nodes, edges=(WorkflowEdge(source="a", target="b"),),
    )
    rt = WorkflowFactory.default(clock=lambda: 1.0)
    _register(rt, graph, id_="recover_check")

    failing_port = _FailingToolPort()
    exec_rt = _exec_runtime(rt.registry, tool_port=failing_port)
    execution, result = exec_rt.start("internal", "recover_check")
    # A retry-exhausted node failure pauses at RECOVERING -- a real, caller-
    # invocable state -- rather than auto-advancing to a terminal FAILED.
    assert execution.state is WorkflowExecutionState.RECOVERING
    assert result is None

    # swap in a working port and recover -- must resume at "b", never re-run "a"
    working_exec_rt = _exec_runtime(rt.registry, tool_port=_OkToolPort())
    working_exec_rt._repo = exec_rt._repo  # share the durable store across "processes"
    execution2, result2 = working_exec_rt.recover(execution.execution_id)
    assert execution2.state is WorkflowExecutionState.COMPLETED
    assert result2.completed_nodes == ("a", "b")


def test_give_up_finalizes_a_recovering_execution_as_failed():
    graph = _linear_tool_graph(retry=RetryPolicy(max_attempts=1),
                                failure_policy=WorkflowFailurePolicy.PROPAGATE)
    rt = WorkflowFactory.default(clock=lambda: 1.0)
    _register(rt, graph, id_="give_up_check")
    exec_rt = _exec_runtime(rt.registry, tool_port=_FailingToolPort())
    execution, result = exec_rt.start("internal", "give_up_check")
    assert execution.state is WorkflowExecutionState.RECOVERING
    assert result is None

    execution2, result2 = exec_rt.give_up(execution.execution_id)
    assert execution2.state is WorkflowExecutionState.FAILED
    assert result2.error is not None
    assert result2.partial_success is False


# ── Retry orchestration ──────────────────────────────────────────────────────────
def test_retry_succeeds_within_budget():
    graph = _linear_tool_graph(retry=RetryPolicy(max_attempts=3))
    rt = WorkflowFactory.default(clock=lambda: 1.0)
    _register(rt, graph, id_="retry_ok")
    port = _FlakyToolPort(fail_times=2)
    exec_rt = _exec_runtime(rt.registry, tool_port=port)
    execution, result = exec_rt.start("internal", "retry_ok")
    assert execution.state is WorkflowExecutionState.COMPLETED
    assert port.calls == 3


def test_retry_exhausted_propagates_failure():
    graph = _linear_tool_graph(retry=RetryPolicy(max_attempts=2),
                                failure_policy=WorkflowFailurePolicy.PROPAGATE)
    rt = WorkflowFactory.default(clock=lambda: 1.0)
    _register(rt, graph, id_="retry_fail")
    exec_rt = _exec_runtime(rt.registry, tool_port=_FailingToolPort())
    execution, result = exec_rt.start("internal", "retry_fail")
    assert execution.state is WorkflowExecutionState.RECOVERING
    assert result is None

    execution2, result2 = exec_rt.give_up(execution.execution_id)
    assert execution2.state is WorkflowExecutionState.FAILED
    assert result2.error is not None
    assert result2.partial_success is False


def test_failure_policy_ignore_continues_past_a_failed_node():
    node_a = WorkflowNode(
        node_id="a", kind=WorkflowNodeKind.TOOL, tool_ref=("internal", "t", "1.0.0"),
        retry_policy=RetryPolicy(max_attempts=1), failure_policy=WorkflowFailurePolicy.IGNORE,
    )
    node_b = WorkflowNode(node_id="b", kind=WorkflowNodeKind.DELAY, delay_ms=1)
    graph = WorkflowGraph(entry_node="a", nodes=(node_a, node_b),
                           edges=(WorkflowEdge(source="a", target="b"),))
    rt = WorkflowFactory.default(clock=lambda: 1.0)
    _register(rt, graph, id_="ignore")
    exec_rt = _exec_runtime(rt.registry, tool_port=_FailingToolPort())
    execution, result = exec_rt.start("internal", "ignore")
    assert execution.state is WorkflowExecutionState.COMPLETED
    assert result.completed_nodes == ("a", "b")


def test_failure_policy_compensate_still_terminates_failed():
    node = WorkflowNode(
        node_id="a", kind=WorkflowNodeKind.TOOL, tool_ref=("internal", "t", "1.0.0"),
        retry_policy=RetryPolicy(max_attempts=1), failure_policy=WorkflowFailurePolicy.COMPENSATE,
    )
    graph = WorkflowGraph(entry_node="a", nodes=(node,), edges=())
    rt = WorkflowFactory.default(clock=lambda: 1.0)
    _register(rt, graph, id_="compensate")
    exec_rt = _exec_runtime(rt.registry, tool_port=_FailingToolPort())
    execution, result = exec_rt.start("internal", "compensate")
    assert execution.state is WorkflowExecutionState.RECOVERING

    execution2, _ = exec_rt.give_up(execution.execution_id)
    assert execution2.state is WorkflowExecutionState.FAILED


# ── Timeout orchestration ─────────────────────────────────────────────────────────
def test_timeout_triggers_failure_when_elapsed_exceeds_policy():
    now = [0.0]

    def clock():
        return now[0]

    class _SlowToolPort:
        async def invoke(self, call):
            now[0] += 2.0  # simulate the call itself taking 2s of wall-clock time
            return ToolResult(call_id=call.call_id, ok=True, output={"done": True})

    node = WorkflowNode(
        node_id="a", kind=WorkflowNodeKind.TOOL, tool_ref=("internal", "t", "1.0.0"),
        retry_policy=RetryPolicy(max_attempts=1), timeout_policy=TimeoutPolicy(seconds=1.0),
    )
    graph = WorkflowGraph(entry_node="a", nodes=(node,), edges=())
    rt = WorkflowFactory.default(clock=lambda: 1.0)
    _register(rt, graph, id_="timeout")
    exec_rt = _exec_runtime(rt.registry, tool_port=_SlowToolPort(), clock=clock)
    execution, result = exec_rt.start("internal", "timeout")
    assert execution.state is WorkflowExecutionState.RECOVERING
    assert result is None

    execution2, result2 = exec_rt.give_up(execution.execution_id)
    assert execution2.state is WorkflowExecutionState.FAILED
    assert "timeout" in result2.error.lower() or "exceeded" in result2.error


# ── Cancellation ────────────────────────────────────────────────────────────────
def test_cancellation_checked_between_node_boundaries():
    class _CancelAfterA:
        def __init__(self, token: WorkflowCancellationToken) -> None:
            self._token = token

        def run(self, point, event):
            if point == "after_node" and event.get("node_id") == "a":
                self._token.cancel()

    nodes = (
        WorkflowNode(node_id="a", kind=WorkflowNodeKind.DELAY, delay_ms=1),
        WorkflowNode(node_id="b", kind=WorkflowNodeKind.DELAY, delay_ms=2),
    )
    graph = WorkflowGraph(
        entry_node="a", nodes=nodes, edges=(WorkflowEdge(source="a", target="b"),),
    )
    rt = WorkflowFactory.default(clock=lambda: 1.0)
    _register(rt, graph, id_="cancel")

    token = WorkflowCancellationToken("t1")
    exec_rt = _exec_runtime(rt.registry, hook_adapter=_CancelAfterA(token))
    execution, result = exec_rt.start("internal", "cancel", cancellation=token)
    assert execution.state is WorkflowExecutionState.CANCELLED
    assert result.completed_nodes == ("a",)
    assert result.partial_success is False


# ── Replay ─────────────────────────────────────────────────────────────────────
def test_replay_record_round_trips_and_validates():
    graph = _linear_tool_graph()
    rt = WorkflowFactory.default(clock=lambda: 1.0)
    _register(rt, graph, id_="replay")
    exec_rt = _exec_runtime(rt.registry, tool_port=_OkToolPort())
    execution, result = exec_rt.start("internal", "replay")
    record = exec_rt.record_replay(execution.execution_id, result)
    comparison = exec_rt.validate_replay(record.replay_id)
    assert comparison.matches


def test_replay_validation_detects_tampering():
    record = workflow_replay_record_for(
        [build_checkpoint("e1", "a", 1, {"x": 1})],
        WorkflowExecutionResult(execution_id="e1", final_state=WorkflowExecutionState.COMPLETED),
    )
    tampered = record.__class__(
        replay_id=record.replay_id,
        checkpoints_document=record.checkpoints_document,
        result_document={**record.result_document, "final_state": "failed"},
        replay_hash=record.replay_hash,
    )
    comparison = WorkflowReplayEngine().validate(tampered)
    assert not comparison.matches
    assert comparison.mismatches == ("replay_hash",)


def test_runtime_validate_replay_raises_for_unknown_id():
    rt = WorkflowFactory.default(clock=lambda: 1.0)
    exec_rt = _exec_runtime(rt.registry)
    with pytest.raises(ConfigError):
        exec_rt.validate_replay("nope")


# ── Serialization round-trips ──────────────────────────────────────────────────
def test_execution_serialization_round_trips():
    e = WorkflowExecution(
        execution_id="e1", namespace="internal", workflow_id="wf", version="1.0.0",
        graph_hash="h", state=WorkflowExecutionState.RUNNING, started_at=1.0, updated_at=2.0,
    )
    s = WorkflowExecutionSerializer()
    assert s.execution_from_dict(s.execution_to_dict(e)) == e


def test_snapshot_serialization_round_trips():
    checkpoint = build_checkpoint("e1", "a", 1, {"x": 1}, now=1.0)
    snapshot = WorkflowSnapshot(
        execution_id="e1", current_node="a", variables={"x": 1}, node_history=("a",),
        active_nodes=("b",), last_checkpoint=checkpoint,
    )
    s = WorkflowExecutionSerializer()
    assert s.snapshot_from_dict(s.snapshot_to_dict(snapshot)) == snapshot


def test_result_serialization_round_trips():
    r = WorkflowExecutionResult(
        execution_id="e1", final_state=WorkflowExecutionState.COMPLETED,
        variables={"x": 1}, completed_nodes=("a", "b"), partial_success=True,
    )
    s = WorkflowExecutionSerializer()
    assert s.result_from_dict(s.result_to_dict(r)) == r


def test_context_serialization_round_trips():
    ctx = WorkflowExecutionContext(context_id="c1", execution_id="e1", retry_count=2)
    s = WorkflowExecutionSerializer()
    assert s.context_from_dict(s.context_to_dict(ctx)) == ctx


# ── Durable repository ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_repository_stores_execution_checkpoints_and_snapshot():
    store = InMemoryDocumentStore()
    serializer = WorkflowExecutionSerializer()
    repo = WorkflowExecutionRepository(store, serializer)

    execution = WorkflowExecution(
        execution_id="e1", namespace="internal", workflow_id="wf", version="1.0.0", graph_hash="h",
    )
    await repo.save_execution(execution)
    fetched = await repo.get_execution("e1")
    assert fetched == execution
    assert await repo.execution_count() == 1

    checkpoint = build_checkpoint("e1", "a", 1, {"x": 1}, now=1.0)
    await repo.add_checkpoint(checkpoint)
    checkpoints = await repo.checkpoints_for("e1")
    assert checkpoints == [checkpoint]

    snapshot = WorkflowSnapshot(execution_id="e1", current_node="a", variables={"x": 1})
    await repo.save_snapshot(snapshot)
    assert await repo.get_snapshot("e1") == snapshot


def test_repository_config_has_no_ttl_field():
    """The absence of a `*_ttl_s` field is the durability signal (§7 of the
    M6.4A design) -- unlike every other closed runtime's temporary-repository
    configuration."""
    import dataclasses

    config = WorkflowExecutionConfiguration.default()
    assert not hasattr(config, "registration_ttl_s")
    assert not hasattr(config, "replay_ttl_s")
    field_names = [f.name for f in dataclasses.fields(config)]
    assert not any(name.endswith("ttl_s") for name in field_names)


# ── Determinism ────────────────────────────────────────────────────────────────
def test_execution_id_is_deterministic_given_identical_inputs():
    from workflow.execution import execution_id_for

    assert execution_id_for("internal", "wf", "1.0.0", 1.0) == execution_id_for(
        "internal", "wf", "1.0.0", 1.0
    )


# ── Never-execute-directly + only-interface-seams constraints ────────────────
def test_engine_pauses_at_recovering_when_tool_port_missing():
    graph = _linear_tool_graph()
    engine = WorkflowExecutionEngine()  # no tool_port injected
    execution, result, _, snapshot = engine.run(
        graph, namespace="internal", workflow_id="wf", version="1.0.0",
    )
    assert execution.state is WorkflowExecutionState.RECOVERING
    assert result is None
    assert "tool_port" in (snapshot.pending_error or "")


def test_recover_execution_free_function_resumes_from_snapshot():
    nodes = (
        WorkflowNode(node_id="a", kind=WorkflowNodeKind.DELAY, delay_ms=1),
        WorkflowNode(
            node_id="b", kind=WorkflowNodeKind.TOOL, tool_ref=("internal", "t", "1.0.0"),
            retry_policy=RetryPolicy(max_attempts=1),
        ),
    )
    graph = WorkflowGraph(
        entry_node="a", nodes=nodes, edges=(WorkflowEdge(source="a", target="b"),),
    )
    engine = WorkflowExecutionEngine(tool_port=_FailingToolPort())
    execution, result, checkpoints, snapshot = engine.run(
        graph, namespace="internal", workflow_id="recover_fn", version="1.0.0",
    )
    assert execution.state is WorkflowExecutionState.RECOVERING
    assert result is None

    recovered_engine = WorkflowExecutionEngine(tool_port=_OkToolPort())  # swap port for the retry
    execution2, result2, _, _ = recover_execution(recovered_engine, graph, execution, snapshot)
    assert execution2.state is WorkflowExecutionState.COMPLETED
    assert result2.completed_nodes == ("a", "b")


def test_engine_never_imports_tool_skill_or_hooks_concretely():
    text = pathlib.Path("workflow/execution/engine.py").read_text()
    assert "import tool" not in text
    assert "import skill" not in text
    assert "import hooks" not in text


def test_workflow_skill_port_and_tool_port_remain_the_only_dispatch_seams():
    from workflow.execution.engine import WorkflowExecutionEngine as _E

    for banned in ("execute_tool_directly", "run_skill_directly", "run_hook_directly"):
        assert not hasattr(_E, banned)


# ── Dependency rules (workflow.execution stays acyclic; no ownership leakage) ─
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


def test_workflow_execution_dependency_rules():
    forbidden = {
        "runtime", "api", "gateway", "replay", "events", "memory", "knowledge",
        "context", "prompt", "reasoning", "provider", "bridge", "pipeline",
        "voicebot", "observability", "tenant", "tool", "skill", "hooks", "agent",
    }
    assert not (_imports("workflow/execution") & forbidden), _imports("workflow/execution")


def test_workflow_execution_imports_only_core_and_own_package():
    allowed_extra = {
        "core", "__future__", "abc", "collections", "dataclasses", "enum", "typing",
        "hashlib", "copy", "time",
    }
    non_stdlib_like = {t for t in _imports("workflow/execution") if t not in allowed_extra}
    assert not non_stdlib_like, non_stdlib_like
