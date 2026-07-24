"""skill.planning — Skill Runtime Planning (M6.2B-2).

Per the approved M6.2A design, and building on M6.2B-1's registration-only
core: a deterministic, replayable, provider- and transport-neutral
`SkillExecutionPlanner` that turns an already-registered skill's dependency
graph into an immutable `SkillPlan` (a leaf-first execution order plus its
*declared*, never-resolved capability requirements), a `SkillExecutionContext`
(the canonical execution contract future Workflow/Agent/MCP/A2A runtimes will
consume), a first concrete `SkillPolicyEngine` (`AllowAllSkillPolicy`, a
permissive default), planning observability (`SkillPlanningTrace` + an
in-memory sink), and planning-replay (`SkillPlanReplayRecord` +
`PlanReplayEngine`, a pure hash-chain re-verification — never a live re-plan).

This bundle never executes a Skill, never executes a Tool, and never resolves
a capability requirement — `CapabilityResolver` remains interface-only and is
never imported here. Bundle 1 (`skill/domain.py`, `skill/registry.py`,
`skill/runtime.py`, `skill/lifecycle.py`, ...) and Tool Runtime are untouched.
"""

from .config import PlanningCollectionsConfig, PlanningConfiguration
from .context import build_execution_context
from .diagnostics import build_diagnostics
from .domain import (
    PlanningDiagnostics,
    PlanningStatistics,
    ReplayComparison,
    SkillExecutionContext,
    SkillPlan,
    SkillPlanningTrace,
    SkillPlanReplayRecord,
    SkillPlanStep,
    SkillPolicyVerdict,
)
from .events import PHASE_EVENT_TYPE, PlanningPhase
from .factory import PlanningFactory
from .ids import context_id_for, plan_id_for, replay_id_for, trace_id_for
from .observability import InMemoryPlanningObservabilitySink, planning_trace_for
from .planner import SkillExecutionPlanner, build_plan
from .policy import AllowAllSkillPolicy
from .replay import plan_replay_record_for
from .replay_engine import PlanReplayEngine
from .repository import PlanningRepository
from .runtime import SkillPlanningRuntime
from .serializer import PlanningSerializer
from .statistics import compute_statistics

__all__ = [
    # runtime + composition
    "SkillPlanningRuntime",
    "PlanningFactory",
    # planning
    "SkillExecutionPlanner",
    "build_plan",
    "build_execution_context",
    # domain
    "SkillPlan",
    "SkillPlanStep",
    "SkillExecutionContext",
    "SkillPolicyVerdict",
    "SkillPlanningTrace",
    "SkillPlanReplayRecord",
    "ReplayComparison",
    "PlanningDiagnostics",
    "PlanningStatistics",
    # policy
    "AllowAllSkillPolicy",
    # observability
    "planning_trace_for",
    "InMemoryPlanningObservabilitySink",
    # replay
    "plan_replay_record_for",
    "PlanReplayEngine",
    # diagnostics + statistics
    "build_diagnostics",
    "compute_statistics",
    # repository + serializer + config
    "PlanningRepository",
    "PlanningSerializer",
    "PlanningConfiguration",
    "PlanningCollectionsConfig",
    # ids
    "plan_id_for",
    "context_id_for",
    "replay_id_for",
    "trace_id_for",
    # events
    "PlanningPhase",
    "PHASE_EVENT_TYPE",
]
