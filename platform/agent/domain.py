"""Agent domain model (M6.5B-1) — immutable value objects.

Per the approved M6.5A design: an `AgentManifest` (the versioned, diffable
**contract only** — never executable code, mirroring `SkillManifest`'s own
discipline) and the runtime-loaded `AgentDescriptor` (manifest + lifecycle
bookkeeping). `AgentCapability` mirrors `SkillCapabilityRequirement(kind,
tag)` exactly, extended with a `required` flag.

An Agent's identity (`AgentIdentity`) is a stable, immutable snapshot a
`AgentGoal`/`AgentConversation` carries by reference — never re-derived from
the registry mid-goal (the registry could deprecate/retire a version while a
goal started under it is still open).

This bundle owns **identity and registration only** — manifest, descriptor,
registry, descriptor lifecycle, and the *structure* of the goal-scoped
execution lifecycle (§7.1 of the M6.5A design). `AgentGoal` carries no
`state` field here: the goal lifecycle FSM (`lifecycle.py`) is defined and
directly testable against bare `AgentGoalState` values, but nothing in this
bundle wires it to a live, stateful goal record — that is explicit Bundle 3
scope (the `AgentExecutionEngine`). No planning, no reasoning, no workflow
delegation, no execution, no replay, no observability exist here or
anywhere in this bundle.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

__all__ = [
    "AgentLifecycleState",
    "AgentGoalState",
    "AgentCapability",
    "AgentManifest",
    "AgentDescriptor",
    "AgentIdentity",
    "AgentGoal",
    "AgentConversation",
    "AgentDecision",
    "AgentExecutionContext",
    "AgentStatistics",
]


class AgentLifecycleState(StrEnum):
    """Descriptor lifecycle only — identical shape to Tool's/Skill's/Hook's/
    Workflow's own descriptor lifecycle."""

    DRAFT = "draft"
    REGISTERED = "registered"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class AgentGoalState(StrEnum):
    """The goal-scoped **execution** lifecycle (§7.1 of the M6.5A design) —
    a second, distinct `core.fsm` shape, restoring the platform's dual-FSM
    pattern (first applied to Workflow Runtime), but attached to `AgentGoal`,
    never to the Agent identity itself: an Agent's identity is durable-but-
    idle between goals, the Goal is the bounded unit that genuinely executes
    over time. **Structure only in this bundle** — no engine, no stateful
    goal record, no execution wires this FSM to a live object; see
    `lifecycle.py`'s own docstring."""

    RECEIVED = "received"
    DECOMPOSING = "decomposing"
    DELEGATING = "delegating"
    AWAITING_WORKFLOW = "awaiting_workflow"
    REVISING = "revising"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"


@dataclass(frozen=True, slots=True)
class AgentCapability:
    """Mirrors `skill.domain.SkillCapabilityRequirement(kind, tag)` exactly,
    extended with `required` since an Agent's plan may tolerate an optional
    capability being unavailable in a way a Skill's dependency graph does
    not. Declared and structurally carried only — resolution is
    `AgentCapabilityResolver`'s job (Bundle 2), never this bundle's."""

    kind: str  # "workflow" | "skill" | "tool" | "memory" | "knowledge"
    tag: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class AgentManifest:
    """The versioned, diffable **contract** for one agent — never executable
    code (mirrors `SkillManifest`'s own discipline, DL-0042 point b).
    Identity is `(namespace, id, version)`."""

    id: str
    namespace: str = "internal"
    version: str = "1.0.0"
    interface_version: str = "1.0"
    display_name: str = ""
    persona_prompt_id: str | None = None  # a PromptPort template_id — opaque here, never rendered
    capabilities: tuple[AgentCapability, ...] = ()
    default_memory_scope: str = "session"
    tags: frozenset[str] = field(default_factory=frozenset)
    owner: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}.{self.id}"


@dataclass(frozen=True, slots=True)
class AgentDescriptor:
    """Loaded runtime metadata: the immutable `AgentManifest` plus lifecycle
    bookkeeping. Deliberately a separate object from the manifest — the
    registry replaces this on a state transition, never the manifest
    itself."""

    manifest: AgentManifest
    state: AgentLifecycleState = AgentLifecycleState.DRAFT
    registered_at: float = 0.0

    @property
    def qualified_name(self) -> str:
        return self.manifest.qualified_name

    @property
    def version(self) -> str:
        return self.manifest.version

    def with_state(self, state: AgentLifecycleState) -> AgentDescriptor:
        return replace(self, state=state)


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    """A thin, immutable identity token carried by reference — never
    re-derived from the registry mid-goal."""

    agent_id: str
    namespace: str
    version: str
    display_name: str
    created_at: float = 0.0


@dataclass(frozen=True, slots=True)
class AgentGoal:
    """One bounded unit of work an Agent pursues. `description` is opaque
    prose, never parsed or interpreted by Agent Runtime code — it is handed
    to Reasoning Runtime as-is (Bundle 2's concern). Carries no `state`
    field in this bundle — see this module's own docstring."""

    goal_id: str
    agent_id: str
    conversation_id: str
    description: str
    constraints: Mapping[str, Any] = field(default_factory=dict)
    created_at: float = 0.0


@dataclass(frozen=True, slots=True)
class AgentConversation:
    """The container an Agent's identity persists across; a goal is one
    bounded unit of work within it — the structural expression of the
    Agent/Goal distinction (§1 of the M6.5A design)."""

    conversation_id: str
    agent_id: str
    goal_ids: tuple[str, ...] = ()
    started_at: float = 0.0
    updated_at: float = 0.0

    def with_goal(self, goal_id: str, *, updated_at: float) -> AgentConversation:
        return replace(self, goal_ids=(*self.goal_ids, goal_id), updated_at=updated_at)


@dataclass(frozen=True, slots=True)
class AgentDecision:
    """The audit-trail unit every planning/delegation step will produce
    (Bundle 2+). `payload_hash` is a content-hash reference, never the raw
    payload itself — the same `HookVerdict.MODIFY` discipline applied
    here."""

    decision_id: str
    goal_id: str
    # "decompose" | "resolve_capability" | "select_workflow" | "delegate" |
    # "revise_plan" | "abandon"
    kind: str
    reasoning_decision_ref: str | None = None
    payload_hash: str = ""
    created_at: float = 0.0


@dataclass(frozen=True, slots=True)
class AgentExecutionContext:
    """Extends `WorkflowExecutionContext` (which itself extends
    `SkillExecutionContext`) by **structural field duplication** — never
    inheritance or import, the same "duplicate, don't cross-import"
    precedent applied a third time (§4.10 of the M6.5A design). Carried by
    every future planning/delegation call for one goal — never constructed
    or consumed anywhere in this bundle (no planning, no execution exist
    here)."""

    # --- structural duplicate of WorkflowExecutionContext's own fields, verbatim ---
    session_id: str = ""
    trace_id: str = ""
    org_id: str | None = None
    workspace_id: str | None = None
    variables: Mapping[str, Any] = field(default_factory=dict)
    execution_id: str = ""  # here: the goal's execution id
    graph_hash: str = ""  # here: the delegated workflow's graph_hash, once known
    current_node_id: str = ""  # here: repurposed as "current step id"
    checkpoint_id: str | None = None
    retry_count: int = 0
    # --- additive, Agent-specific ---
    goal_id: str = ""
    plan_id: str = ""
    conversation_id: str = ""
    decision_count: int = 0
    delegated_execution_id: str | None = None


@dataclass(frozen=True, slots=True)
class AgentStatistics:
    """A point-in-time snapshot: counters + registered agents + repository
    size."""

    name: str
    state: str
    healthy: bool
    agents: tuple[str, ...] = ()
    registrations: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)
