"""WorkflowExecutionEngine (M6.4B-2) — deterministic node scheduling,
checkpointing, retry/timeout orchestration, failure propagation, and
compensation ownership.

**Never executes Tools/Skills/Hooks directly** — Tool nodes are dispatched
through an injected `core.ports.tool.ToolPort` (already frozen, part of
`core`, not a sibling-runtime import); Skill nodes through the
Workflow-owned `WorkflowSkillPort` seam; Hook integration through the
Workflow-owned `WorkflowHookAdapter` seam. Both seams remain optional —
a node kind whose port was not injected fails fast with `ConfigError`,
never silently no-ops. `node.config` is never read here.

**Deterministic ordering**: `graph.topological_order` (Bundle 1, reused
unchanged) is the traversal spine. A conditional-activation frontier
(`active`) is threaded alongside it — a node only executes if at least one
of its incoming edges was activated by its source's own condition
evaluating true (or the edge is unconditional) — so PARALLEL fan-out
(all-unconditional edges) and CONDITIONAL branching (some edges gated) both
fall out of the same mechanism with zero extra code. A MERGE node
naturally waits for the topological position that guarantees every
predecessor has already been visited; it proceeds as soon as any one
predecessor branch activates it (an OR-join) — an explicit, named
simplification, not an oversight (documented in the certification's future
technical-debt review).

**No real concurrency** — this is a synchronous, deterministic, offline
engine (mirroring every other closed runtime's synchronous default); a
"parallel" fan-out therefore executes its branches sequentially, in the
same deterministic topological order every time, which is what "replay
must produce the same join order every time" (Phase 0 §10) actually
requires — true concurrent execution was never the determinism
requirement, reproducible order was.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from core.errors import ConfigError

from ..domain import WorkflowFailurePolicy, WorkflowGraph, WorkflowNode, WorkflowNodeKind
from ..extensions import WorkflowHookAdapter, WorkflowSkillPort
from ..graph import topological_order
from .cancellation import WorkflowCancellationToken
from .checkpoint import build_checkpoint
from .condition import evaluate_condition
from .domain import (
    WorkflowCheckpoint,
    WorkflowExecution,
    WorkflowExecutionResult,
    WorkflowSnapshot,
)
from .execution_lifecycle import WorkflowExecutionTrigger, advance_execution
from .ids import execution_id_for

EngineOutcome = tuple[
    WorkflowExecution, WorkflowExecutionResult | None,
    tuple[WorkflowCheckpoint, ...], WorkflowSnapshot,
]


class WorkflowNodeExecutionError(Exception):
    """Raised after a node's retry budget is exhausted -- never leaked past
    the engine's own failure-propagation handling."""

    def __init__(self, node_id: str, cause: BaseException | None) -> None:
        super().__init__(f"node {node_id!r} failed: {cause}")
        self.node_id = node_id
        self.cause = cause


class WorkflowExecutionEngine:
    def __init__(
        self,
        *,
        tool_port: Any | None = None,
        skill_port: WorkflowSkillPort | None = None,
        hook_adapter: WorkflowHookAdapter | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._tool_port = tool_port
        self._skill_port = skill_port
        self._hook_adapter = hook_adapter
        self._clock = clock or time.time

    # ── entry points ─────────────────────────────────────────────────────────
    def run(
        self,
        graph: WorkflowGraph,
        *,
        namespace: str, workflow_id: str, version: str,
        initial_variables: Mapping[str, Any] | None = None,
        cancellation: WorkflowCancellationToken | None = None,
    ) -> EngineOutcome:
        now = self._clock()
        execution = WorkflowExecution(
            execution_id=execution_id_for(namespace, workflow_id, version, now),
            namespace=namespace, workflow_id=workflow_id, version=version,
            graph_hash=graph.graph_hash, started_at=now, updated_at=now,
        )
        execution = advance_execution(execution, WorkflowExecutionTrigger.START, clock=self._clock)
        self._emit_hook("before_workflow", execution)

        order = topological_order(graph)
        result = self._continue_loop(
            graph, execution, order, variables={}, active={graph.entry_node},
            completed=[], checkpoints=[], sequence=0,
            initial_variables=initial_variables, cancellation=cancellation,
            resume_index=0,
        )
        return result

    def recover(
        self,
        graph: WorkflowGraph,
        execution: WorkflowExecution,
        snapshot: WorkflowSnapshot,
        *,
        cancellation: WorkflowCancellationToken | None = None,
    ) -> EngineOutcome:
        """Recovery **never restarts from the beginning** -- it resumes from
        `snapshot`'s recorded state, re-attempting `snapshot.current_node`
        (the node whose failure drove the execution to `RECOVERING`)."""
        execution = advance_execution(
            execution, WorkflowExecutionTrigger.RECOVER, clock=self._clock
        )
        order = topological_order(graph)
        resume_index = order.index(snapshot.current_node)
        return self._continue_loop(
            graph, execution, order,
            variables=dict(snapshot.variables), active=set(snapshot.active_nodes),
            completed=list(snapshot.node_history), checkpoints=[],
            sequence=len(snapshot.node_history),
            initial_variables=None, cancellation=cancellation, resume_index=resume_index,
        )

    def give_up(
        self,
        graph: WorkflowGraph,
        execution: WorkflowExecution,
        snapshot: WorkflowSnapshot,
    ) -> EngineOutcome:
        """Finalizes a `RECOVERING` execution as terminally failed -- applies
        `node.failure_policy`'s COMPENSATE/PROPAGATE split only at this
        point, never automatically. The counterpart to `recover()`: a
        caller reaching `RECOVERING` chooses exactly one of these two."""
        node = next(n for n in graph.nodes if n.node_id == snapshot.current_node)
        if node.failure_policy is WorkflowFailurePolicy.COMPENSATE:
            execution = advance_execution(execution, WorkflowExecutionTrigger.COMPENSATE,
                                           clock=self._clock)
        execution = advance_execution(execution, WorkflowExecutionTrigger.FAIL, clock=self._clock)
        self._emit_hook("workflow_failure", execution, node_id=snapshot.current_node)
        error = snapshot.pending_error or f"node {snapshot.current_node!r} failed"
        result = self._build_result(
            execution, snapshot.variables, list(snapshot.node_history),
            partial_success=False, error=error,
        )
        return execution, result, (), snapshot

    def resume_after_event(
        self,
        graph: WorkflowGraph,
        execution: WorkflowExecution,
        snapshot: WorkflowSnapshot,
        signal_payload: Mapping[str, Any] | None = None,
        *,
        cancellation: WorkflowCancellationToken | None = None,
    ) -> EngineOutcome:
        """Continues **from the last checkpoint**, never from `entry_node` --
        completes the paused EVENT node with `signal_payload` and proceeds."""
        execution = advance_execution(execution, WorkflowExecutionTrigger.RESUME, clock=self._clock)
        order = topological_order(graph)
        nodes_by_id = {n.node_id: n for n in graph.nodes}
        event_node = nodes_by_id[snapshot.current_node]

        variables = dict(snapshot.variables)
        variables[event_node.node_id] = dict(signal_payload or {})
        completed = [*snapshot.node_history, event_node.node_id]
        active = set(snapshot.active_nodes)
        sequence = len(completed)
        checkpoint = build_checkpoint(execution.execution_id, event_node.node_id, sequence,
                                       variables, now=self._clock())
        active |= self._activate_targets(graph, event_node.node_id, variables)

        resume_index = order.index(event_node.node_id) + 1
        return self._continue_loop(
            graph, execution, order, variables=variables, active=active,
            completed=completed, checkpoints=[checkpoint], sequence=sequence,
            initial_variables=None, cancellation=cancellation, resume_index=resume_index,
        )

    # ── shared loop ────────────────────────────────────────────────────────
    def _continue_loop(
        self,
        graph: WorkflowGraph,
        execution: WorkflowExecution,
        order: Sequence[str],
        *,
        variables: dict[str, Any],
        active: set[str],
        completed: list[str],
        checkpoints: list[WorkflowCheckpoint],
        sequence: int,
        initial_variables: Mapping[str, Any] | None,
        cancellation: WorkflowCancellationToken | None,
        resume_index: int,
    ) -> EngineOutcome:
        if initial_variables:
            variables.update(initial_variables)
        nodes_by_id = {n.node_id: n for n in graph.nodes}

        for node_id in order[resume_index:]:
            if node_id not in active:
                continue

            if cancellation is not None and cancellation.is_cancelled():
                execution = advance_execution(execution, WorkflowExecutionTrigger.CANCEL,
                                               clock=self._clock)
                result = self._build_result(execution, variables, completed, partial_success=False)
                return execution, result, tuple(checkpoints), self._snapshot(
                    execution, node_id, variables, completed, active, checkpoints
                )

            node = nodes_by_id[node_id]
            self._emit_hook("before_node", execution, node_id=node_id)

            if node.kind is WorkflowNodeKind.EVENT:
                execution = advance_execution(execution, WorkflowExecutionTrigger.PAUSE,
                                               clock=self._clock)
                snapshot = self._snapshot(
                    execution, node_id, variables, completed, active, checkpoints
                )
                return execution, None, tuple(checkpoints), snapshot

            try:
                output = self._execute_node(node, variables)
            except WorkflowNodeExecutionError as exc:
                if node.failure_policy is WorkflowFailurePolicy.IGNORE:
                    output = None
                else:
                    # A retry-exhausted node failure pauses at RECOVERING -- exactly
                    # like an EVENT node pauses at PAUSED -- rather than auto-advancing
                    # to a terminal state. This is what makes "recovery" a real,
                    # caller-invocable action instead of a transient pass-through: the
                    # caller decides whether to `recover()` (retry from this exact
                    # checkpoint, never from the start) or `give_up()` (finalize the
                    # failure, applying `node.failure_policy`'s COMPENSATE/PROPAGATE
                    # split only at that point).
                    execution = advance_execution(execution, WorkflowExecutionTrigger.NODE_FAILED,
                                                   clock=self._clock)
                    snapshot = self._snapshot(execution, node_id, variables, completed, active,
                                               checkpoints, pending_error=str(exc))
                    return execution, None, tuple(checkpoints), snapshot

            if output:
                variables.update(output)
            completed.append(node_id)
            self._emit_hook("after_node", execution, node_id=node_id)

            sequence += 1
            checkpoints.append(build_checkpoint(execution.execution_id, node_id, sequence,
                                                 variables, now=self._clock()))
            active |= self._activate_targets(graph, node_id, variables)

        execution = advance_execution(
            execution, WorkflowExecutionTrigger.COMPLETE, clock=self._clock
        )
        self._emit_hook("after_workflow", execution)
        result = self._build_result(execution, variables, completed, partial_success=True)
        return execution, result, tuple(checkpoints), self._snapshot(
            execution, completed[-1] if completed else graph.entry_node,
            variables, completed, active, checkpoints,
        )

    # ── node dispatch (Tool/Skill ports only; never a direct execution) ──────
    def _execute_node(
        self, node: WorkflowNode, variables: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        max_attempts = node.retry_policy.max_attempts if node.retry_policy else 1
        timeout_s = node.timeout_policy.seconds if node.timeout_policy else None
        last_exc: BaseException | None = None

        for _attempt in range(1, max_attempts + 1):
            start = self._clock()
            try:
                output = self._dispatch(node, variables)
            except Exception as exc:  # noqa: BLE001 -- retried, never silently swallowed
                last_exc = exc
                continue
            elapsed = self._clock() - start
            if timeout_s is not None and elapsed > timeout_s:
                last_exc = TimeoutError(
                    f"node {node.node_id!r} exceeded timeout {timeout_s}s (took {elapsed}s)"
                )
                continue
            return output

        raise WorkflowNodeExecutionError(node.node_id, last_exc)

    def _dispatch(
        self, node: WorkflowNode, variables: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        from .._run import run_coro_sync

        if node.kind is WorkflowNodeKind.TOOL:
            if self._tool_port is None:
                raise ConfigError(f"node {node.node_id!r} requires a tool_port, none injected")
            from core.ports.tool import ToolCall

            call = ToolCall(
                call_id=node.node_id, name=".".join(node.tool_ref or ()), arguments=dict(variables),
            )
            outcome = run_coro_sync(self._tool_port.invoke(call))
            if not outcome.ok:
                raise RuntimeError(outcome.error or f"tool call failed for node {node.node_id}")
            return {node.node_id: outcome.output}

        if node.kind is WorkflowNodeKind.SKILL:
            if self._skill_port is None:
                raise ConfigError(f"node {node.node_id!r} requires a skill_port, none injected")
            output = self._skill_port.run_skill(node.skill_ref, variables)
            return {node.node_id: output}

        if node.kind is WorkflowNodeKind.DELAY:
            return {node.node_id: {"delayed_ms": node.delay_ms}}

        return None  # CONDITIONAL / PARALLEL / MERGE: pure structural markers, no output

    def _activate_targets(
        self, graph: WorkflowGraph, node_id: str, variables: Mapping[str, Any],
    ) -> set[str]:
        activated: set[str] = set()
        for edge in graph.edges:
            if edge.source == node_id and evaluate_condition(edge.condition, variables):
                activated.add(edge.target)
        return activated

    def _build_result(
        self, execution: WorkflowExecution, variables: Mapping[str, Any], completed: list[str],
        *, partial_success: bool, error: str | None = None,
    ) -> WorkflowExecutionResult:
        return WorkflowExecutionResult(
            execution_id=execution.execution_id, final_state=execution.state,
            variables=dict(variables), completed_nodes=tuple(completed),
            partial_success=partial_success, error=error, completed_at=execution.updated_at,
            duration_ms=max(execution.updated_at - execution.started_at, 0.0) * 1000.0,
        )

    def _snapshot(
        self, execution: WorkflowExecution, current_node: str, variables: Mapping[str, Any],
        completed: list[str], active: set[str], checkpoints: list[WorkflowCheckpoint],
        *, pending_error: str | None = None,
    ) -> WorkflowSnapshot:
        return WorkflowSnapshot(
            execution_id=execution.execution_id, current_node=current_node,
            variables=dict(variables), node_history=tuple(completed),
            active_nodes=tuple(sorted(active)),
            last_checkpoint=checkpoints[-1] if checkpoints else None,
            pending_error=pending_error,
        )

    def _emit_hook(self, point: str, execution: WorkflowExecution, *, node_id: str = "") -> None:
        if self._hook_adapter is None:
            return
        self._hook_adapter.run(point, {"execution_id": execution.execution_id, "node_id": node_id})
