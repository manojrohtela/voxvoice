"""WorkflowExecutionRuntime — the execution facade (M6.4B-2).

Ties `WorkflowExecutionEngine` (node scheduling, checkpointing, retry/
timeout, failure propagation), the durable `WorkflowExecutionRepository`,
observability, and replay into one object. **Never executes a Tool/Skill/
Hook directly** — dispatch always goes through the injected
`core.ports.tool.ToolPort` / `WorkflowSkillPort` / `WorkflowHookAdapter`
seams, never a concrete `tool`/`skill`/`hooks` import.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from core.errors import ConfigError
from core.events import Event, EventType

from .._run import run_coro_sync
from ..domain import WorkflowGraph
from ..extensions import WorkflowHookAdapter, WorkflowSkillPort
from ..registry import WorkflowRegistry
from .cancellation import WorkflowCancellationToken
from .config import WorkflowExecutionConfiguration
from .diagnostics import build_diagnostics
from .domain import (
    ReplayComparison,
    WorkflowCheckpoint,
    WorkflowExecution,
    WorkflowExecutionDiagnostics,
    WorkflowExecutionResult,
    WorkflowExecutionState,
    WorkflowExecutionStatistics,
    WorkflowReplayRecord,
    WorkflowSnapshot,
)
from .engine import WorkflowExecutionEngine
from .events import PHASE_EVENT_TYPE, ExecutionPhase
from .observability import InMemoryWorkflowObservabilitySink, trace_for
from .recovery import recover_execution
from .replay import workflow_replay_record_for
from .replay_engine import WorkflowReplayEngine
from .repositories import WorkflowExecutionRepository
from .serializer import WorkflowExecutionSerializer
from .statistics import compute_statistics

Publisher = Callable[..., Event | None]


class WorkflowExecutionRuntime:
    def __init__(
        self,
        *,
        config: WorkflowExecutionConfiguration,
        repository: WorkflowExecutionRepository,
        registry: WorkflowRegistry,
        tool_port: Any | None = None,
        skill_port: WorkflowSkillPort | None = None,
        hook_adapter: WorkflowHookAdapter | None = None,
        observability_sink: InMemoryWorkflowObservabilitySink | None = None,
        replay_engine: WorkflowReplayEngine | None = None,
        serializer: WorkflowExecutionSerializer | None = None,
        clock: Callable[[], float] | None = None,
        publish: Publisher | None = None,
    ) -> None:
        import time

        self._config = config
        self._repo = repository
        self._registry = registry
        self._clock = clock or time.time
        self._engine = WorkflowExecutionEngine(
            tool_port=tool_port, skill_port=skill_port, hook_adapter=hook_adapter,
            clock=self._clock,
        )
        self._observability = observability_sink or InMemoryWorkflowObservabilitySink()
        self._replay_engine = replay_engine or WorkflowReplayEngine()
        self._serializer = serializer or WorkflowExecutionSerializer()
        self._publish = publish

    # ── start / resume / recover ──────────────────────────────────────────────
    def start(
        self, namespace: str, id_: str, version: str | None = None,
        *, initial_variables: Mapping[str, Any] | None = None,
        cancellation: WorkflowCancellationToken | None = None,
    ) -> tuple[WorkflowExecution, WorkflowExecutionResult | None]:
        descriptor = self._registry.get_descriptor(namespace, id_, version)
        graph = descriptor.manifest.graph
        execution, result, checkpoints, snapshot = self._engine.run(
            graph, namespace=descriptor.manifest.namespace, workflow_id=descriptor.manifest.id,
            version=descriptor.manifest.version, initial_variables=initial_variables,
            cancellation=cancellation,
        )
        self._persist(execution, checkpoints, snapshot, result)
        return execution, result

    def resume_after_event(
        self, execution_id: str, signal_payload: Mapping[str, Any] | None = None,
        *, cancellation: WorkflowCancellationToken | None = None,
    ) -> tuple[WorkflowExecution, WorkflowExecutionResult | None]:
        execution, snapshot, graph = self._load(execution_id)
        execution, result, checkpoints, snapshot = self._engine.resume_after_event(
            graph, execution, snapshot, signal_payload, cancellation=cancellation,
        )
        self._persist(execution, checkpoints, snapshot, result)
        return execution, result

    def recover(
        self, execution_id: str, *, cancellation: WorkflowCancellationToken | None = None,
    ) -> tuple[WorkflowExecution, WorkflowExecutionResult | None]:
        execution, snapshot, graph = self._load(execution_id)
        execution, result, checkpoints, snapshot = recover_execution(
            self._engine, graph, execution, snapshot, cancellation=cancellation,
        )
        self._persist(execution, checkpoints, snapshot, result)
        return execution, result

    def give_up(self, execution_id: str) -> tuple[WorkflowExecution, WorkflowExecutionResult]:
        """The counterpart to `recover()`: finalizes a `RECOVERING` execution
        as terminally failed, applying `node.failure_policy`'s COMPENSATE/
        PROPAGATE split only at this point."""
        execution, snapshot, graph = self._load(execution_id)
        execution, result, checkpoints, snapshot = self._engine.give_up(graph, execution, snapshot)
        self._persist(execution, checkpoints, snapshot, result)
        return execution, result

    def _load(
        self, execution_id: str,
    ) -> tuple[WorkflowExecution, WorkflowSnapshot, WorkflowGraph]:
        execution = run_coro_sync(self._repo.get_execution(execution_id))
        snapshot = run_coro_sync(self._repo.get_snapshot(execution_id))
        if execution is None or snapshot is None:
            raise ConfigError(f"unknown workflow execution {execution_id!r}")
        descriptor = self._registry.get_descriptor(
            execution.namespace, execution.workflow_id, execution.version
        )
        return execution, snapshot, descriptor.manifest.graph

    def _persist(
        self, execution: WorkflowExecution, checkpoints: Sequence[WorkflowCheckpoint],
        snapshot: WorkflowSnapshot, result: WorkflowExecutionResult | None,
    ) -> None:
        run_coro_sync(self._repo.save_execution(execution))
        run_coro_sync(self._repo.save_snapshot(snapshot))
        for checkpoint in checkpoints:
            run_coro_sync(self._repo.add_checkpoint(checkpoint))
            self._emit(ExecutionPhase.CHECKPOINT_CREATED, execution_id=execution.execution_id,
                       extra={"node_id": checkpoint.node_id, "sequence": checkpoint.sequence})
        self._observability.record(
            {"execution_id": execution.execution_id, "state": execution.state.value}
        )

        if result is None:
            self._emit(ExecutionPhase.STARTED, execution_id=execution.execution_id)
            return

        trace = trace_for(result, now=self._clock())
        run_coro_sync(self._repo.add_trace(trace.trace_id, {
            "trace_id": trace.trace_id, "execution_id": trace.execution_id,
            "final_state": trace.final_state, "node_count": trace.node_count,
            "failure_count": trace.failure_count, "duration_ms": trace.duration_ms,
            "created_at": trace.created_at,
        }))
        self._observability.record({"trace_id": trace.trace_id, "final_state": trace.final_state})

        if result.error:
            self._emit(ExecutionPhase.FAILED, execution_id=execution.execution_id,
                       extra={"error": result.error})
        elif execution.state is WorkflowExecutionState.CANCELLED:
            self._emit(ExecutionPhase.CANCELLED, execution_id=execution.execution_id)
        else:
            self._emit(ExecutionPhase.COMPLETED, execution_id=execution.execution_id)

    # ── replay ─────────────────────────────────────────────────────────────────
    def record_replay(
        self, execution_id: str, result: WorkflowExecutionResult,
    ) -> WorkflowReplayRecord:
        checkpoints = run_coro_sync(self._repo.checkpoints_for(execution_id))
        record = workflow_replay_record_for(
            checkpoints, result, now=self._clock(), serializer=self._serializer,
        )
        run_coro_sync(self._repo.add_replay(record))
        self._emit(ExecutionPhase.REPLAY_RECORDED, execution_id=execution_id,
                   extra={"replay_id": record.replay_id})
        return record

    def validate_replay(self, replay_id: str) -> ReplayComparison:
        record = run_coro_sync(self._repo.get_replay(replay_id))
        if record is None:
            raise ConfigError(f"unknown workflow replay {replay_id!r}")
        comparison = self._replay_engine.validate(record)
        phase = (
            ExecutionPhase.REPLAY_VALIDATED if comparison.matches
            else ExecutionPhase.REPLAY_MISMATCH
        )
        self._emit(phase, execution_id="",
                   extra={"replay_id": replay_id, "matches": comparison.matches})
        return comparison

    def compare_replays(self, replay_id_a: str, replay_id_b: str) -> ReplayComparison:
        a = run_coro_sync(self._repo.get_replay(replay_id_a))
        b = run_coro_sync(self._repo.get_replay(replay_id_b))
        if a is None or b is None:
            raise ConfigError("unknown workflow replay id(s) for comparison")
        return self._replay_engine.compare(a, b)

    # ── diagnostics + statistics ───────────────────────────────────────────────
    def diagnostics(self, *, healthy: bool = True) -> WorkflowExecutionDiagnostics:
        execution_count = run_coro_sync(self._repo.execution_count())
        replay_count = run_coro_sync(self._repo.replay_count())
        diag = build_diagnostics(
            config=self._config, execution_count=execution_count, replay_count=replay_count,
            healthy=healthy,
        )
        self._emit(ExecutionPhase.DIAGNOSTICS_GENERATED, execution_id="")
        return diag

    def statistics(
        self, results: Sequence[WorkflowExecutionResult],
    ) -> WorkflowExecutionStatistics:
        return compute_statistics(results)

    # ── internals ────────────────────────────────────────────────────────────
    def _emit(
        self, phase: ExecutionPhase, *, execution_id: str, extra: dict[str, Any] | None = None,
    ) -> None:
        if self._publish is None:
            return
        event_type: EventType = PHASE_EVENT_TYPE[phase]
        payload = {"phase": phase.value, "execution_id": execution_id, **(extra or {})}
        self._publish(event_type, payload, session_id="", org_id="")
