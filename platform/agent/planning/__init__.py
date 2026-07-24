"""agent.planning — Agent Runtime Planning (M6.5B-2).

Per the approved M6.5A design, and building on Bundle 1's identity/
registration core: goal decomposition, capability resolution, workflow
selection, plan revision, and workflow delegation. **Planning is structure,
never intelligence** (§5, §11 item 2) — goal decomposition and plan
revision are ordinary `core.ports.reasoning.ReasoningPort.run` calls;
`agent.planning` code only assembles the request (via `core.ports.context.
ContextPort.assemble`, no duplicate seam) and deterministically parses the
response. Capability resolution goes through the Bundle 1 `AgentCapability
Resolver` seam; workflow selection and delegation go through the Bundle 1
`AgentWorkflowPort` seam — resolve/submit/status/signal only, **never**
constructing or executing a `WorkflowGraph` directly.

No execution occurs anywhere in this subpackage — `delegate_plan` submits
execution requests and returns immediately; driving the goal-scoped
`AgentGoalState` FSM (Bundle 1, `agent.lifecycle`) to completion by polling/
awaiting a delegated workflow's result is explicit Bundle 3 scope (the
`AgentExecutionEngine`), not this subpackage's.

`agent.planning → core[, agent]` only. No `tool`/`skill`/`hooks`/`workflow`
import — Reasoning/Memory/Knowledge/Prompt/Context integration uses their
already-frozen `core.ports` interfaces directly; no duplicate seam is
declared for any of them.
"""

from .capability import resolve_capabilities
from .decisions import record_decision
from .decomposer import context_ref_for, decompose_goal, parse_plan_steps
from .delegation import delegate_step, query_status, send_signal
from .diagnostics import PlanningDiagnostics, build_planning_diagnostics
from .domain import AgentPlan, PlanStep, ResolvedCapability
from .hashing import compute_plan_hash
from .ids import decision_id_for, plan_id_for, step_id_for
from .planner import DelegationResult, PlanningResult, delegate_plan, plan_goal
from .revision import revise_plan
from .statistics import PlanningStatistics, compute_planning_statistics
from .workflow_selection import select_workflow

__all__ = [
    # domain
    "AgentPlan",
    "PlanStep",
    "ResolvedCapability",
    # capability resolution
    "resolve_capabilities",
    # goal decomposition
    "decompose_goal",
    "parse_plan_steps",
    "context_ref_for",
    # workflow selection
    "select_workflow",
    # workflow delegation
    "delegate_step",
    "query_status",
    "send_signal",
    # plan revision
    "revise_plan",
    # decision recording
    "record_decision",
    # hashing
    "compute_plan_hash",
    # ids
    "plan_id_for",
    "step_id_for",
    "decision_id_for",
    # orchestration
    "plan_goal",
    "delegate_plan",
    "PlanningResult",
    "DelegationResult",
    # diagnostics + statistics
    "PlanningDiagnostics",
    "build_planning_diagnostics",
    "PlanningStatistics",
    "compute_planning_statistics",
]
