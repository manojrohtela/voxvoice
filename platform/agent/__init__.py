"""agent — the Agent Runtime (Epic 6, M6.5B-1: core).

Per the approved M6.5A design: an Agent is a long-lived, autonomous
orchestration entity — identity + capability model persisting across many
goals/conversations — that decomposes goals, resolves capabilities, and
delegates to Workflow Runtime; never a Workflow/Skill/Tool/Hook/Provider
itself, never executes anything directly. Agent Runtime owns **identity and
registration only** in this bundle: manifest/descriptor/registry/
namespacing/version-resolution/descriptor-lifecycle, plus the *structure* of
the goal-scoped execution lifecycle (`AgentGoalState`, `lifecycle.py`) and
the `AgentExecutionContext` shape — neither wired to any live planning or
execution code yet.

**Central architectural call, restored a second time (after Workflow
Runtime)**: the dual-FSM shape attaches to `AgentGoal`, never to the Agent
identity itself — an Agent's identity is durable-but-idle between goals; the
Goal is the bounded unit that genuinely executes over time. This bundle
defines both FSMs but wires neither to a live, stateful record: the
descriptor FSM drives `AgentRegistry`; the goal FSM is directly testable
against bare `AgentGoalState` values only.

No planning, no reasoning, no workflow delegation, no execution, no replay,
no observability exist here or anywhere in this bundle. `AgentGoal.
description` is opaque prose this bundle never parses or interprets.

`agent → core[, tenant]` only. Agent Runtime imports no sibling runtime —
not Tool Runtime, not Skill Runtime, not Hook Framework, not Workflow
Runtime, not Reasoning/Memory/Knowledge/Prompt/Context Runtime.
`AgentCapabilityResolver`/`AgentWorkflowPort`/`AgentHookAdapter`/
`AgentPolicyEngine`/`AgentObservabilitySink`/`AgentReplaySink` remain
interface-only seams for a future composition root to implement.
"""

from .config import AgentCollectionsConfig, AgentConfiguration, StoreConfig
from .diagnostics import AgentRuntimeDiagnostics, build_diagnostics
from .domain import (
    AgentCapability,
    AgentConversation,
    AgentDecision,
    AgentDescriptor,
    AgentExecutionContext,
    AgentGoal,
    AgentGoalState,
    AgentIdentity,
    AgentLifecycleState,
    AgentManifest,
    AgentStatistics,
)
from .extensions import (
    AgentCapabilityResolver,
    AgentHookAdapter,
    AgentObservabilitySink,
    AgentPolicyEngine,
    AgentReplaySink,
    AgentWorkflowPort,
)
from .factory import AgentFactory
from .ids import content_hash, descriptor_id_for, registration_id_for
from .lifecycle import (
    AGENT_GOAL_LIFECYCLE,
    AGENT_LIFECYCLE,
    AgentGoalTrigger,
    AgentTrigger,
    advance_agent,
    advance_goal_state,
)
from .registry import AgentRegistry
from .repositories import AgentRepository
from .runtime import AgentRuntime
from .serializer import AgentSerializer
from .statistics import AgentRuntimeStatistics, compute_statistics
from .validation import SUPPORTED_INTERFACE_VERSIONS, validate_manifest

__all__ = [
    # runtime + composition
    "AgentRuntime",
    "AgentFactory",
    "AgentRegistry",
    "AgentSerializer",
    # domain — identity/registration
    "AgentManifest",
    "AgentDescriptor",
    "AgentLifecycleState",
    "AgentCapability",
    "AgentIdentity",
    "AgentStatistics",
    # domain — goal/conversation/decision (structure only)
    "AgentGoal",
    "AgentGoalState",
    "AgentConversation",
    "AgentDecision",
    "AgentExecutionContext",
    # ids
    "content_hash",
    "descriptor_id_for",
    "registration_id_for",
    # lifecycle (descriptor)
    "AGENT_LIFECYCLE",
    "AgentTrigger",
    "advance_agent",
    # lifecycle (goal — structure only)
    "AGENT_GOAL_LIFECYCLE",
    "AgentGoalTrigger",
    "advance_goal_state",
    # validation
    "validate_manifest",
    "SUPPORTED_INTERFACE_VERSIONS",
    # repository
    "AgentRepository",
    # config
    "AgentConfiguration",
    "StoreConfig",
    "AgentCollectionsConfig",
    # diagnostics + statistics
    "build_diagnostics",
    "AgentRuntimeDiagnostics",
    "compute_statistics",
    "AgentRuntimeStatistics",
    # extension hooks (interfaces only)
    "AgentCapabilityResolver",
    "AgentWorkflowPort",
    "AgentHookAdapter",
    "AgentPolicyEngine",
    "AgentObservabilitySink",
    "AgentReplaySink",
]
