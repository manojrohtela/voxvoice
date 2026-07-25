"""Agent goal execution domain model (M6.5B-3) — immutable value objects.

`AgentGoalExecution` is the stateful record `AgentExecutionEngine` drives
through Bundle 1's already-defined `AgentGoalState` FSM (`agent.lifecycle`)
— **execution state lives here, on a Goal-scoped record, never on
`AgentIdentity`** (§7.1 of the M6.5A design; `agent.domain.AgentIdentity` is
untouched by this bundle). `AgentGoalSnapshot` is the durable-enough
point-in-time capture (plan + decision history + delegation reference) a
process restart resumes from — resuming always continues from wherever the
snapshot says the goal was, **never restarts decomposition/delegation from
`RECEIVED`** (mirrors `workflow.execution.WorkflowSnapshot`'s own
"recover never restarts" discipline, adapted to a goal FSM that has no
dedicated `RECOVERING` state — see `engine.py`'s own docstring for why).

`AgentReplayRecord`/`ReplayComparison` are a pure hash-chain re-verification
over the already-content-hashed `AgentDecision.payload_hash` sequence —
never a live re-plan, never a live re-call of `ReasoningPort`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from ..domain import AgentDecision, AgentGoalState
from ..planning import AgentPlan

__all__ = [
    "AgentGoalExecution",
    "AgentGoalSnapshot",
    "AgentGoalResult",
    "AgentExecutionTrace",
    "AgentReplayRecord",
    "ReplayComparison",
    "AgentExecutionDiagnostics",
    "AgentExecutionStatistics",
]


@dataclass(frozen=True, slots=True)
class AgentGoalExecution:
    execution_id: str
    goal_id: str
    agent_id: str
    conversation_id: str
    state: AgentGoalState
    plan_id: str = ""
    delegated_execution_id: str | None = None
    started_at: float = 0.0
    updated_at: float = 0.0

    def with_state(self, state: AgentGoalState, *, updated_at: float) -> AgentGoalExecution:
        return replace(self, state=state, updated_at=updated_at)

    def with_delegation(
        self, delegated_execution_id: str | None, plan_id: str, *, updated_at: float,
    ) -> AgentGoalExecution:
        return replace(
            self, delegated_execution_id=delegated_execution_id, plan_id=plan_id,
            updated_at=updated_at,
        )


@dataclass(frozen=True, slots=True)
class AgentGoalSnapshot:
    execution_id: str
    state: AgentGoalState
    plan: AgentPlan | None = None
    decisions: tuple[AgentDecision, ...] = ()
    delegated_execution_id: str | None = None
    pending_error: str | None = None


@dataclass(frozen=True, slots=True)
class AgentGoalResult:
    execution_id: str
    goal_id: str
    final_state: AgentGoalState
    plan_id: str
    delegated_execution_id: str | None
    partial_success: bool
    error: str | None = None
    completed_at: float = 0.0


@dataclass(frozen=True, slots=True)
class AgentExecutionTrace:
    """A read-side observability record of one goal execution — built from
    an already-finished `AgentGoalResult`, never influencing it."""

    trace_id: str
    execution_id: str
    final_state: str
    decision_count: int
    failure_count: int
    created_at: float = 0.0
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentReplayRecord:
    """Everything needed to deterministically re-verify one goal's decision
    chain later — the ordered `AgentDecision.payload_hash` sequence + the
    hash computed over them at record time — never a live re-plan."""

    replay_id: str
    goal_id: str
    decision_hashes: tuple[str, ...]
    final_state: str
    replay_hash: str
    replay_version: str = "1"
    replay_timestamp: float = 0.0


@dataclass(frozen=True, slots=True)
class ReplayComparison:
    matches: bool
    expected_hash: str
    actual_hash: str
    mismatches: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True, slots=True)
class AgentExecutionDiagnostics:
    runtime_version: str
    config_fingerprint: str
    execution_stats: Mapping[str, object] = field(default_factory=dict)
    repository_stats: Mapping[str, object] = field(default_factory=dict)
    replay_stats: Mapping[str, object] = field(default_factory=dict)
    health_summary: str = "unknown"


@dataclass(frozen=True, slots=True)
class AgentExecutionStatistics:
    per_final_state: dict[str, int] = field(default_factory=dict)
    execution_count: int = 0
    failure_count: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)
