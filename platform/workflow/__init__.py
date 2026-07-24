"""workflow — the Workflow Runtime (Epic 6, M6.4B-1: core).

Per the approved M6.4A design: a Workflow is a deterministic orchestration
graph — nodes and edges, frozen and content-hashed — that coordinates
already-existing Skill/Tool/Hook capabilities. Workflow Runtime owns
**graph definition only** in this bundle: registration/discovery/manifests/
descriptors/versioning/lifecycle/graph-structural-validation (duplicate-node/
duplicate-edge/orphan/cycle detection)/deterministic graph hashing/metadata.

Cycle detection here is a **real, primary** topological-sort algorithm, not
defense-in-depth — a `WorkflowGraph` arrives as one complete manifest at
registration time, unlike Skill Runtime's incrementally-built dependency
graph where a cycle is structurally unreachable through the public API.

No execution, no checkpoints, no recovery, no replay, no observability exist
here or anywhere in this bundle — `WorkflowNode.config` is opaque metadata
this bundle never interprets or executes. The second, execution lifecycle
(§6.1 of the M6.4A design) is explicit Bundle 2 scope and is not defined
here either.

`workflow → core[, tenant]` only. Workflow Runtime imports no sibling
runtime — not Tool Runtime, not Skill Runtime, not Hook Framework.
`WorkflowSkillPort`/`WorkflowHookAdapter` remain interface-only seams for a
future composition root to implement.
"""

from .config import StoreConfig, WorkflowCollectionsConfig, WorkflowConfiguration
from .diagnostics import WorkflowRuntimeDiagnostics, build_diagnostics
from .domain import (
    WorkflowCondition,
    WorkflowConditionOperator,
    WorkflowDescriptor,
    WorkflowEdge,
    WorkflowFailurePolicy,
    WorkflowGraph,
    WorkflowLifecycleState,
    WorkflowManifest,
    WorkflowNode,
    WorkflowNodeKind,
    WorkflowStatistics,
)
from .extensions import (
    WorkflowHookAdapter,
    WorkflowObservabilitySink,
    WorkflowPolicyEngine,
    WorkflowReplaySink,
    WorkflowSkillPort,
)
from .factory import WorkflowFactory
from .graph import compute_graph_hash, topological_order, validate_graph
from .ids import content_hash, descriptor_id_for, registration_id_for
from .lifecycle import WORKFLOW_LIFECYCLE, WorkflowTrigger, advance_workflow
from .registry import WorkflowRegistry
from .repositories import WorkflowRepository
from .runtime import WorkflowRuntime
from .serializer import WorkflowSerializer
from .statistics import WorkflowRuntimeStatistics, compute_statistics
from .validation import SUPPORTED_INTERFACE_VERSIONS, validate_manifest

__all__ = [
    # runtime + composition
    "WorkflowRuntime",
    "WorkflowFactory",
    "WorkflowRegistry",
    "WorkflowSerializer",
    # domain
    "WorkflowManifest",
    "WorkflowDescriptor",
    "WorkflowLifecycleState",
    "WorkflowNode",
    "WorkflowNodeKind",
    "WorkflowEdge",
    "WorkflowCondition",
    "WorkflowConditionOperator",
    "WorkflowGraph",
    "WorkflowFailurePolicy",
    "WorkflowStatistics",
    # graph
    "validate_graph",
    "compute_graph_hash",
    "topological_order",
    # ids
    "content_hash",
    "descriptor_id_for",
    "registration_id_for",
    # lifecycle
    "WORKFLOW_LIFECYCLE",
    "WorkflowTrigger",
    "advance_workflow",
    # validation
    "validate_manifest",
    "SUPPORTED_INTERFACE_VERSIONS",
    # repository
    "WorkflowRepository",
    # config
    "WorkflowConfiguration",
    "StoreConfig",
    "WorkflowCollectionsConfig",
    # diagnostics + statistics
    "build_diagnostics",
    "WorkflowRuntimeDiagnostics",
    "compute_statistics",
    "WorkflowRuntimeStatistics",
    # extension hooks (interfaces only)
    "WorkflowSkillPort",
    "WorkflowHookAdapter",
    "WorkflowPolicyEngine",
    "WorkflowObservabilitySink",
    "WorkflowReplaySink",
]
