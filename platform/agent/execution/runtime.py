"""AgentExecutionRuntime — the execution facade (M6.5B-3).

Ties `AgentExecutionEngine` (goal FSM driving, planning composition,
workflow delegation), the temporary `AgentGoalExecutionRepository`,
observability, and replay into one object. **Never executes a Tool/Skill/
Workflow directly** — dispatch always goes through the injected
`AgentWorkflowPort` seam, never a concrete `workflow`/`skill`/`tool`
import.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from core.errors import ConfigError
from core.events import Event, EventType
from core.ports.context import ContextPort, ContextRequest
from core.ports.reasoning import ReasoningPort

from .._run import run_coro_sync
from ..domain import AgentCapability, AgentGoal
from ..extensions import AgentCapabilityResolver, AgentWorkflowPort
from .config import AgentExecutionConfiguration
from .diagnostics import AgentExecutionDiagnostics, build_diagnostics
from .domain import (
    AgentExecutionStatistics,
    AgentGoalExecution,
    AgentGoalResult,
    AgentGoalSnapshot,
    ReplayComparison,
)
from .engine import AgentExecutionEngine
from .events import PHASE_EVENT_TYPE, ExecutionPhase
from .observability import InMemoryAgentObservabilitySink, trace_for
from .replay import agent_replay_record_for
from .replay_engine import AgentReplayEngine
from .repositories import AgentGoalExecutionRepository
from .serializer import AgentExecutionSerializer
from .statistics import compute_statistics

Publisher = Callable[..., Event | None]


class AgentExecutionRuntime:
    def __init__(
        self,
        *,
        config: AgentExecutionConfiguration,
        repository: AgentGoalExecutionRepository,
        workflow_port: AgentWorkflowPort | None = None,
        capability_resolver: AgentCapabilityResolver | None = None,
        reasoning_port: ReasoningPort | None = None,
        context_port: ContextPort | None = None,
        observability_sink: InMemoryAgentObservabilitySink | None = None,
        replay_engine: AgentReplayEngine | None = None,
        serializer: AgentExecutionSerializer | None = None,
        clock: Callable[[], float] | None = None,
        publish: Publisher | None = None,
    ) -> None:
        import time

        self._config = config
        self._repo = repository
        self._clock = clock or time.time
        self._engine = AgentExecutionEngine(
            workflow_port=workflow_port, capability_resolver=capability_resolver,
            reasoning_port=reasoning_port, context_port=context_port, clock=self._clock,
            max_revisions=config.max_revisions,
        )
        self._observability = observability_sink or InMemoryAgentObservabilitySink()
        self._replay_engine = replay_engine or AgentReplayEngine()
        self._serializer = serializer or AgentExecutionSerializer()
        self._publish = publish

    # ── submit / poll / signal / cancel / recover ──────────────────────────────
    def submit_goal(
        self, goal: AgentGoal, *, capabilities: Sequence[AgentCapability],
        context_request: ContextRequest,
    ) -> tuple[AgentGoalExecution, AgentGoalResult | None]:
        execution, result, snapshot = self._engine.submit(
            goal, capabilities=capabilities, context_request=context_request,
        )
        self._persist(execution, snapshot, result)
        return execution, result

    def poll_goal(
        self, execution_id: str, goal: AgentGoal,
        *, revision_context_request: ContextRequest | None = None,
    ) -> tuple[AgentGoalExecution, AgentGoalResult | None]:
        execution, snapshot = self._load(execution_id)
        execution, result, snapshot = self._engine.poll(
            execution, snapshot, goal, revision_context_request=revision_context_request,
        )
        self._persist(execution, snapshot, result)
        return execution, result

    def send_signal(self, execution_id: str, name: str, payload: dict[str, Any]) -> None:
        execution, _snapshot = self._load(execution_id)
        self._engine.send_signal(execution, name, payload)

    def cancel_goal(
        self, execution_id: str,
    ) -> tuple[AgentGoalExecution, AgentGoalResult]:
        execution, snapshot = self._load(execution_id)
        execution, result, snapshot = self._engine.cancel(execution, snapshot)
        self._persist(execution, snapshot, result)
        return execution, result

    def recover_goal(
        self, execution_id: str, goal: AgentGoal,
        *, revision_context_request: ContextRequest | None = None,
    ) -> tuple[AgentGoalExecution, AgentGoalResult | None]:
        execution, snapshot = self._load(execution_id)
        execution, result, snapshot = self._engine.recover(
            execution, snapshot, goal, revision_context_request=revision_context_request,
        )
        self._persist(execution, snapshot, result)
        return execution, result

    def _load(self, execution_id: str) -> tuple[AgentGoalExecution, AgentGoalSnapshot]:
        execution = run_coro_sync(self._repo.get_execution(execution_id))
        snapshot = run_coro_sync(self._repo.get_snapshot(execution_id))
        if execution is None or snapshot is None:
            raise ConfigError(f"unknown agent goal execution {execution_id!r}")
        return execution, snapshot

    def _persist(
        self, execution: AgentGoalExecution, snapshot: AgentGoalSnapshot,
        result: AgentGoalResult | None,
    ) -> None:
        run_coro_sync(self._repo.save_execution(execution))
        run_coro_sync(self._repo.save_snapshot(snapshot))
        self._observability.record(
            {"execution_id": execution.execution_id, "state": execution.state.value}
        )

        if result is None:
            self._emit(ExecutionPhase.STARTED, execution_id=execution.execution_id)
            return

        trace = trace_for(
            result, decision_count=len(snapshot.decisions), now=self._clock(),
        )
        run_coro_sync(self._repo.add_trace(trace.trace_id, {
            "trace_id": trace.trace_id, "execution_id": trace.execution_id,
            "final_state": trace.final_state, "decision_count": trace.decision_count,
            "failure_count": trace.failure_count, "created_at": trace.created_at,
        }))
        self._observability.record({"trace_id": trace.trace_id, "final_state": trace.final_state})

        if result.error and execution.state.value == "abandoned":
            self._emit(ExecutionPhase.CANCELLED, execution_id=execution.execution_id)
        elif result.error:
            self._emit(ExecutionPhase.FAILED, execution_id=execution.execution_id,
                       extra={"error": result.error})
        else:
            self._emit(ExecutionPhase.COMPLETED, execution_id=execution.execution_id)

    # ── replay ─────────────────────────────────────────────────────────────────
    def record_replay(self, decisions: Sequence[Any], result: AgentGoalResult) -> Any:
        record = agent_replay_record_for(decisions, result, now=self._clock())
        run_coro_sync(self._repo.add_replay(record))
        self._emit(ExecutionPhase.REPLAY_RECORDED, execution_id=result.execution_id,
                   extra={"replay_id": record.replay_id})
        return record

    def validate_replay(self, replay_id: str) -> ReplayComparison:
        record = run_coro_sync(self._repo.get_replay(replay_id))
        if record is None:
            raise ConfigError(f"unknown agent replay {replay_id!r}")
        comparison = self._replay_engine.validate(record)
        phase = (
            ExecutionPhase.REPLAY_VALIDATED if comparison.matches
            else ExecutionPhase.REPLAY_MISMATCH
        )
        self._emit(phase, execution_id="", extra={"replay_id": replay_id,
                                                    "matches": comparison.matches})
        return comparison

    def compare_replays(self, replay_id_a: str, replay_id_b: str) -> ReplayComparison:
        a = run_coro_sync(self._repo.get_replay(replay_id_a))
        b = run_coro_sync(self._repo.get_replay(replay_id_b))
        if a is None or b is None:
            raise ConfigError("unknown agent replay id(s) for comparison")
        return self._replay_engine.compare(a, b)

    # ── diagnostics + statistics ───────────────────────────────────────────────
    def diagnostics(self, *, healthy: bool = True) -> AgentExecutionDiagnostics:
        execution_count = run_coro_sync(self._repo.execution_count())
        replay_count = run_coro_sync(self._repo.replay_count())
        diag = build_diagnostics(
            config=self._config, execution_count=execution_count, replay_count=replay_count,
            healthy=healthy,
        )
        self._emit(ExecutionPhase.DIAGNOSTICS_GENERATED, execution_id="")
        return diag

    def statistics(self, results: Sequence[AgentGoalResult]) -> AgentExecutionStatistics:
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
