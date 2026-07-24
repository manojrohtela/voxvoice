"""Workflow domain model (M6.4B-1) — immutable value objects.

Per the approved M6.4A design: a `WorkflowManifest` (the versioned, diffable
**contract only** — never executable code, embedding a frozen, content-hashed
`WorkflowGraph`) and the runtime-loaded `WorkflowDescriptor` (manifest +
lifecycle bookkeeping). A `WorkflowNode` is one data-only shape with a `kind`
discriminator — never a class hierarchy — so every node type stays
declarative, satisfying the frozen `WorkflowPort`'s own docstring commitment
("declarative graphs, not code-as-intelligence"). `WorkflowCondition` is a
small, closed expression grammar evaluated against accumulated execution
variables — never arbitrary code, never `eval()` (Bundle 2's concern; this
bundle only carries the shape).

This bundle owns **graph definition only** — registration, discovery,
lifecycle, and structural validation. No execution, no checkpoints, no
recovery, no replay, no observability exist here or anywhere in this bundle;
`node.config` is opaque metadata this bundle never interprets or executes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from core.policies import RetryPolicy, TimeoutPolicy

__all__ = [
    "WorkflowNodeKind",
    "WorkflowConditionOperator",
    "WorkflowCondition",
    "WorkflowNode",
    "WorkflowEdge",
    "WorkflowGraph",
    "WorkflowLifecycleState",
    "WorkflowFailurePolicy",
    "WorkflowManifest",
    "WorkflowDescriptor",
    "WorkflowStatistics",
]


class WorkflowNodeKind(StrEnum):
    """Additive only (AR-32) — a new kind is a backward-compatible enum
    member, never a breaking change. `WorkflowNode.config` is the generic
    escape hatch for a kind's parameters not yet promoted to a first-class
    field."""

    SKILL = "skill"
    TOOL = "tool"
    CONDITIONAL = "conditional"
    PARALLEL = "parallel"
    MERGE = "merge"
    DELAY = "delay"
    EVENT = "event"


class WorkflowConditionOperator(StrEnum):
    EQ = "eq"
    NEQ = "neq"
    GT = "gt"
    LT = "lt"
    GTE = "gte"
    LTE = "lte"
    IN = "in"
    EXISTS = "exists"


class WorkflowFailurePolicy(StrEnum):
    """Ownership-only, structural data in this bundle -- never interpreted
    or enforced (that is Bundle 2's execution engine)."""

    PROPAGATE = "propagate"
    COMPENSATE = "compensate"
    IGNORE = "ignore"


class WorkflowLifecycleState(StrEnum):
    DRAFT = "draft"
    REGISTERED = "registered"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


@dataclass(frozen=True, slots=True)
class WorkflowCondition:
    """A small, closed expression grammar -- never arbitrary code, never
    `eval()`. Evaluation against accumulated execution variables is Bundle
    2's concern; this bundle only carries the shape and validates it is
    well-formed."""

    field: str
    operator: WorkflowConditionOperator
    value: Any = None


@dataclass(frozen=True, slots=True)
class WorkflowNode:
    """One data-only node shape with a `kind` discriminator -- never a class
    hierarchy. Exactly one of `skill_ref`/`tool_ref`/`condition`/`delay_ms`/
    `event_name` is meaningful per `kind` (structurally validated at
    registration, `validation.py`), never enforced by subclassing.
    `retry_policy`/`timeout_policy` are carried as opaque, unused data in
    this bundle -- reused verbatim from `core.policies`, never invoked
    (execution is Bundle 2's concern). `config` is never interpreted or
    executed here or anywhere in this bundle."""

    node_id: str
    kind: WorkflowNodeKind
    skill_ref: tuple[str, str, str] | None = None
    tool_ref: tuple[str, str, str] | None = None
    condition: WorkflowCondition | None = None
    delay_ms: int | None = None
    event_name: str | None = None
    retry_policy: RetryPolicy | None = None
    timeout_policy: TimeoutPolicy | None = None
    failure_policy: WorkflowFailurePolicy = WorkflowFailurePolicy.PROPAGATE
    config: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkflowEdge:
    source: str  # node_id
    target: str  # node_id
    condition: WorkflowCondition | None = None  # None -> unconditional edge


@dataclass(frozen=True, slots=True)
class WorkflowGraph:
    """A frozen, content-hashed, declarative structure. `graph_hash` is
    always registry-computed (`graph.compute_graph_hash`), never trusted
    from a caller-supplied value -- the same "never trust a caller-supplied
    hash, always derive" discipline every closed runtime in this repository
    already follows for its own content-hash fields."""

    entry_node: str
    nodes: tuple[WorkflowNode, ...] = ()
    edges: tuple[WorkflowEdge, ...] = ()
    graph_hash: str = ""

    def with_hash(self, graph_hash: str) -> WorkflowGraph:
        return replace(self, graph_hash=graph_hash)


@dataclass(frozen=True, slots=True)
class WorkflowManifest:
    """The versioned, diffable **contract** for one workflow -- never
    executable code. Embeds the workflow's `WorkflowGraph` verbatim; there
    is no separate "workflow body" anywhere else."""

    id: str
    namespace: str = "internal"
    version: str = "1.0.0"
    interface_version: str = "1.0"
    description: str = ""
    graph: WorkflowGraph = field(default_factory=lambda: WorkflowGraph(entry_node=""))
    tags: frozenset[str] = field(default_factory=frozenset)
    owner: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}.{self.id}"


@dataclass(frozen=True, slots=True)
class WorkflowDescriptor:
    """Loaded runtime metadata: the immutable `WorkflowManifest` (with its
    `graph.graph_hash` already registry-computed) plus lifecycle bookkeeping.
    Deliberately a separate object from the manifest -- the registry replaces
    this on a state transition, never the manifest itself. Bundle 1 owns only
    this descriptor lifecycle; the second, execution lifecycle (§6.1 of the
    M6.4A design) is explicit Bundle 2 scope, not implemented here."""

    manifest: WorkflowManifest
    state: WorkflowLifecycleState = WorkflowLifecycleState.DRAFT
    registered_at: float = 0.0

    @property
    def qualified_name(self) -> str:
        return self.manifest.qualified_name

    @property
    def version(self) -> str:
        return self.manifest.version

    def with_state(self, state: WorkflowLifecycleState) -> WorkflowDescriptor:
        return replace(self, state=state)


@dataclass(frozen=True, slots=True)
class WorkflowStatistics:
    """A point-in-time snapshot: counters + registered workflows + repository
    size."""

    name: str
    state: str
    healthy: bool
    workflows: tuple[str, ...] = ()
    registrations: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)
