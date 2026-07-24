"""workflow.execution — Workflow Runtime Execution (M6.4B-2).

Per the approved M6.4A design, and building on Bundle 1's graph-definition-
only core: the second, **execution** lifecycle FSM (restoring Tool Runtime's
dual-FSM shape), a deterministic `WorkflowExecutionEngine` (node scheduling
via Bundle 1's `topological_order` + a conditional-activation frontier,
checkpoint-after-every-completed-node, retry/timeout orchestration, failure
propagation, compensation ownership), the durable `WorkflowExecutionRepository`
(the platform's one deliberate durable-repository exception), a recovery
engine (resumes from the last checkpoint, never from the start),
`WorkflowReplayRecord` + `WorkflowReplayEngine` (pure hash-chain
re-verification), a first concrete `WorkflowObservabilitySink`
(`InMemoryWorkflowObservabilitySink`, actually wired into the runtime's own
recording path -- learning from Hook Framework's own M6.3C certification
finding that a shipped sink went unused), diagnostics, and statistics.

**Never executes a Tool/Skill/Hook directly** -- Tool dispatch goes through
an injected `core.ports.tool.ToolPort` (already frozen, part of `core`);
Skill dispatch through the Workflow-owned `WorkflowSkillPort` seam; Hook
integration through the Workflow-owned `WorkflowHookAdapter` seam. Neither
seam is implemented here -- both remain interfaces a future composition
root implements. Bundle 1 (`workflow/domain.py`, `workflow/registry.py`,
`workflow/graph.py`, `workflow/lifecycle.py`, `workflow/runtime.py`, ...) and
Tool Runtime/Skill Runtime/Hook Framework are untouched by this milestone.
"""

from .cancellation import WorkflowCancellationToken
from .checkpoint import build_checkpoint, compute_checkpoint_hash, compute_variables_hash
from .condition import evaluate_condition
from .config import WorkflowExecutionCollectionsConfig, WorkflowExecutionConfiguration
from .diagnostics import build_diagnostics
from .domain import (
    ReplayComparison,
    WorkflowCheckpoint,
    WorkflowExecution,
    WorkflowExecutionContext,
    WorkflowExecutionDiagnostics,
    WorkflowExecutionResult,
    WorkflowExecutionState,
    WorkflowExecutionStatistics,
    WorkflowExecutionTrace,
    WorkflowReplayRecord,
    WorkflowSnapshot,
)
from .engine import WorkflowExecutionEngine, WorkflowNodeExecutionError
from .events import PHASE_EVENT_TYPE, ExecutionPhase
from .execution_lifecycle import (
    WORKFLOW_EXECUTION_LIFECYCLE,
    WorkflowExecutionTrigger,
    advance_execution,
)
from .factory import WorkflowExecutionFactory
from .ids import checkpoint_id_for, context_id_for, execution_id_for, replay_id_for, trace_id_for
from .observability import InMemoryWorkflowObservabilitySink, trace_for
from .recovery import recover_execution
from .replay import workflow_replay_record_for
from .replay_engine import WorkflowReplayEngine
from .repositories import WorkflowExecutionRepository
from .runtime import WorkflowExecutionRuntime
from .serializer import WorkflowExecutionSerializer
from .statistics import compute_statistics

__all__ = [
    # runtime + composition
    "WorkflowExecutionRuntime",
    "WorkflowExecutionFactory",
    "WorkflowExecutionEngine",
    "WorkflowExecutionSerializer",
    "WorkflowNodeExecutionError",
    # domain
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
    # lifecycle
    "WORKFLOW_EXECUTION_LIFECYCLE",
    "WorkflowExecutionTrigger",
    "advance_execution",
    # condition + checkpoint
    "evaluate_condition",
    "build_checkpoint",
    "compute_checkpoint_hash",
    "compute_variables_hash",
    # cancellation
    "WorkflowCancellationToken",
    # recovery
    "recover_execution",
    # observability
    "trace_for",
    "InMemoryWorkflowObservabilitySink",
    # replay
    "workflow_replay_record_for",
    "WorkflowReplayEngine",
    # diagnostics + statistics
    "build_diagnostics",
    "compute_statistics",
    # repository + serializer + config
    "WorkflowExecutionRepository",
    "WorkflowExecutionConfiguration",
    "WorkflowExecutionCollectionsConfig",
    # ids
    "execution_id_for",
    "context_id_for",
    "checkpoint_id_for",
    "trace_id_for",
    "replay_id_for",
    # events
    "ExecutionPhase",
    "PHASE_EVENT_TYPE",
]
