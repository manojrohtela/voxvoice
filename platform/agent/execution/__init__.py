"""agent.execution — Agent Runtime Execution (M6.5B-3).

Per the approved M6.5A design, and building on Bundle 1's identity/
registration core and Bundle 2's planning pipeline: `AgentExecutionEngine`
drives Bundle 1's already-defined, goal-scoped `AgentGoalState` FSM
(`agent.lifecycle`) — submitting a goal (decompose → delegate → await),
polling a delegated workflow's status, handling success/failure (revise-or-
fail, bounded by `max_revisions`), cancellation, and session/process
recovery. **Execution state lives on `AgentGoalExecution`, never on
`AgentIdentity`** — Bundle 1's identity/registration files are untouched by
this bundle.

**Never executes a Tool/Skill/Workflow directly** — workflow dispatch goes
through the Agent-owned `AgentWorkflowPort` seam (Bundle 1) exclusively;
this subpackage never constructs or runs a `WorkflowGraph`. Reasoning/
Context integration reuses the already-frozen `core.ports.reasoning`/
`core.ports.context` interfaces via Bundle 2's planning functions — no new
seam.

`AgentReplayRecord`/`AgentReplayEngine` provide pure hash-chain
re-verification over the `AgentDecision` history — never a live re-plan.
`InMemoryAgentObservabilitySink` is a concrete sink `AgentExecutionRuntime`
actually calls from its own recording path (learning from Hook Framework's
own M6.3C certification finding, and Workflow Runtime's own proactive fix of
the same gap in M6.4B-2).

`agent.execution → core[, agent]` only. Bundle 1 and Bundle 2 are untouched
by this bundle.
"""

from .cancellation import AgentCancellationToken
from .config import AgentExecutionCollectionsConfig, AgentExecutionConfiguration
from .diagnostics import build_diagnostics
from .domain import (
    AgentExecutionDiagnostics,
    AgentExecutionStatistics,
    AgentExecutionTrace,
    AgentGoalExecution,
    AgentGoalResult,
    AgentGoalSnapshot,
    AgentReplayRecord,
    ReplayComparison,
)
from .engine import AgentExecutionEngine, EngineOutcome
from .events import PHASE_EVENT_TYPE, ExecutionPhase
from .factory import AgentExecutionFactory
from .ids import execution_id_for, replay_id_for, trace_id_for
from .observability import InMemoryAgentObservabilitySink, trace_for
from .replay import agent_replay_record_for
from .replay_engine import AgentReplayEngine
from .repositories import AgentGoalExecutionRepository
from .runtime import AgentExecutionRuntime
from .serializer import AgentExecutionSerializer
from .statistics import compute_statistics

__all__ = [
    # domain
    "AgentGoalExecution",
    "AgentGoalSnapshot",
    "AgentGoalResult",
    "AgentExecutionTrace",
    "AgentReplayRecord",
    "ReplayComparison",
    "AgentExecutionDiagnostics",
    "AgentExecutionStatistics",
    # engine
    "AgentExecutionEngine",
    "EngineOutcome",
    # runtime + composition
    "AgentExecutionRuntime",
    "AgentExecutionFactory",
    "AgentExecutionSerializer",
    # cancellation
    "AgentCancellationToken",
    # ids
    "execution_id_for",
    "trace_id_for",
    "replay_id_for",
    # observability
    "InMemoryAgentObservabilitySink",
    "trace_for",
    # replay
    "agent_replay_record_for",
    "AgentReplayEngine",
    # repository
    "AgentGoalExecutionRepository",
    # config
    "AgentExecutionConfiguration",
    "AgentExecutionCollectionsConfig",
    # diagnostics + statistics
    "build_diagnostics",
    "compute_statistics",
    # events
    "ExecutionPhase",
    "PHASE_EVENT_TYPE",
]
