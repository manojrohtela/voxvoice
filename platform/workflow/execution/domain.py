"""Workflow execution domain model (M6.4B-2) — immutable value objects.

Per the approved M6.4A design (§6): Workflow Runtime **restores Tool
Runtime's dual-FSM shape** — Bundle 1 shipped the descriptor lifecycle only;
this module adds the second, **execution** lifecycle, since a Workflow,
unlike a Skill or a Hook, genuinely executes over time. Everything here is
frozen + slotted, deterministic, and offline (no wall-clock-driven branching
without an injected clock, no `random()`, no unseeded UUID).

`WorkflowExecutionContext` extends `SkillExecutionContext` (`skill.planning.
domain.SkillExecutionContext`) by **structural field duplication** — every
field name/type that shape already has, duplicated verbatim — never by
inheritance or import (§8 of the M6.4A design; ADR item 3). This keeps
`workflow → core[, tenant]` exactly true with zero exception.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

__all__ = [
    "WorkflowExecutionState",
    "WorkflowExecution",
    "WorkflowExecutionContext",
    "WorkflowExecutionResult",
    "WorkflowCheckpoint",
    "WorkflowSnapshot",
    "WorkflowExecutionTrace",
    "WorkflowReplayRecord",
    "ReplayComparison",
    "WorkflowExecutionDiagnostics",
    "WorkflowExecutionStatistics",
]


class WorkflowExecutionState(StrEnum):
    """The second, execution lifecycle (§6.1 of the M6.4A design) —
    deliberately distinct from Bundle 1's descriptor `WorkflowLifecycleState`,
    which this module never touches or extends."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    RECOVERING = "recovering"
    COMPENSATING = "compensating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class WorkflowExecution:
    """The identity + handle of one running instance. Replaced (never
    mutated) on every state transition, mirroring every closed runtime's
    descriptor-replacement convention."""

    execution_id: str
    namespace: str
    workflow_id: str
    version: str
    graph_hash: str
    state: WorkflowExecutionState = WorkflowExecutionState.PENDING
    started_at: float = 0.0
    updated_at: float = 0.0

    def with_state(self, state: WorkflowExecutionState, *, now: float) -> WorkflowExecution:
        return replace(self, state=state, updated_at=now)


@dataclass(frozen=True, slots=True)
class WorkflowExecutionContext:
    # -- fields present on skill.planning.domain.SkillExecutionContext,
    #    duplicated verbatim (same names/types) for structural, backward-
    #    compatible extension without importing skill/ --
    context_id: str
    plan_id: str = ""
    root: str = ""
    namespace: str = "internal"
    org_id: str = ""
    tenant_id: str = ""
    requested_by: str = ""
    created_at: float = 0.0
    capability_requirements: tuple[Any, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=dict)

    # -- additive, Workflow-specific fields --
    execution_id: str = ""
    graph_hash: str = ""
    current_node_id: str = ""
    checkpoint_id: str | None = None
    retry_count: int = 0


@dataclass(frozen=True, slots=True)
class WorkflowExecutionResult:
    """The terminal outcome of one execution. `partial_success` is always an
    honest reflection of whether the run reached `COMPLETED` on its own
    terms (which may still have skipped never-activated conditional
    branches by design) versus an involuntary early stop (`FAILED`/
    `CANCELLED`) -- `completed_nodes` always lists exactly what ran either
    way, never silently discarded."""

    execution_id: str
    final_state: WorkflowExecutionState
    variables: Mapping[str, Any] = field(default_factory=dict)
    completed_nodes: tuple[str, ...] = ()
    partial_success: bool = False
    error: str | None = None
    completed_at: float = 0.0
    duration_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class WorkflowCheckpoint:
    checkpoint_id: str
    execution_id: str
    node_id: str
    sequence: int  # monotonic, per-execution
    variables_hash: str
    checkpoint_hash: str
    created_at: float = 0.0


@dataclass(frozen=True, slots=True)
class WorkflowSnapshot:
    """The complete, reconstructable state at a checkpoint -- sufficient, on
    its own, to resume a `RECOVERING`/`PAUSED` execution without replaying
    the graph from the start."""

    execution_id: str
    current_node: str
    variables: Mapping[str, Any] = field(default_factory=dict)
    node_history: tuple[str, ...] = ()
    active_nodes: tuple[str, ...] = ()  # the conditional-branch activation frontier
    last_checkpoint: WorkflowCheckpoint | None = None
    pending_error: str | None = None  # set only when paused at RECOVERING (§ engine.py)


@dataclass(frozen=True, slots=True)
class WorkflowExecutionTrace:
    """A read-side observability record of one execution run -- built from
    an already-finished `WorkflowExecutionResult`, never influencing it."""

    trace_id: str
    execution_id: str
    final_state: str
    node_count: int
    failure_count: int
    duration_ms: float
    created_at: float = 0.0
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkflowReplayRecord:
    """Everything needed to deterministically re-verify one execution's
    checkpoint chain later -- the serialized checkpoint sequence + result,
    and the hash computed over them at record time -- never a live
    registry/port reference."""

    replay_id: str
    checkpoints_document: tuple[Mapping[str, object], ...]
    result_document: Mapping[str, object]
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
class WorkflowExecutionDiagnostics:
    runtime_version: str
    config_fingerprint: str
    execution_stats: Mapping[str, object] = field(default_factory=dict)
    repository_stats: Mapping[str, object] = field(default_factory=dict)
    replay_stats: Mapping[str, object] = field(default_factory=dict)
    health_summary: str = "unknown"


@dataclass(frozen=True, slots=True)
class WorkflowExecutionStatistics:
    per_final_state: dict[str, int] = field(default_factory=dict)
    execution_count: int = 0
    failure_count: int = 0
    avg_duration_ms: float = 0.0
    avg_completed_nodes: float = 0.0
