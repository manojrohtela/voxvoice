"""actuator — the Deployment Actuator (Epic 8, M8.16), the sole owner of deployment execution.

Ratified by **ADR-0022**: a new top-level package, a peer of `product/`/`integration/`/`bridge/`,
sitting **above** the frozen Product Runtime (Epic 7) and consuming its immutable `DeploymentPlan`
**read-only**. Product Runtime never learns the Actuator exists (dependency is one-way:
`actuator → product (read-only), core, tenant`). The Actuator owns deployment *execution* — the
deterministic lifecycle FSM, the execution queue/scheduler, approvals, rollback, health
verification, retries, cancellation, timeouts — and reaches infrastructure **only** through a
pluggable `InfrastructureAdapter` (§A6). The Local adapter is the deterministic offline default;
Docker/Kubernetes/GitOps/Terraform/ECS/Azure/GCP/Nomad are declared production adapters.

This package contains **no control-plane, no HTTP, no persistence, no audit-store** dependency —
those bind to it (foundation → actuator), never the reverse.
"""

from __future__ import annotations

from .domain import (
    ApprovalDecision,
    ApprovalRecord,
    DeploymentExecution,
    DeploymentExecutionHistory,
    DeploymentExecutionResult,
    DeploymentExecutionState,
    DeploymentExecutionSummary,
    DeploymentExecutionView,
    DeploymentJob,
    HealthReport,
    HealthState,
    RollbackReason,
    RollbackRecord,
    RollbackTarget,
    StateTransition,
)
from .engine import (
    ApprovalCoordinator,
    CancellationManager,
    DeploymentActuator,
    DeploymentEngine,
    DeploymentExecutor,
    DeploymentQueue,
    DeploymentScheduler,
    HealthVerifier,
    RetryManager,
    RollbackCoordinator,
    TimeoutManager,
)
from .errors import (
    ActuatorError,
    AdapterNotFoundError,
    ApprovalError,
    DeploymentCancelledError,
    DeploymentNotFoundError,
    DeploymentTimeoutError,
    IllegalTransitionError,
    InfrastructureError,
    InvalidDeploymentPlanError,
    ProductionAdapterUnavailableError,
)
from .events import DeploymentEventType
from .infrastructure import (
    INFRASTRUCTURE_TARGETS,
    ApplyResult,
    HealthProbe,
    InfrastructureAdapter,
    InfrastructureCapabilities,
    InfrastructureRegistry,
    LocalDeploymentAdapter,
)
from .lifecycle import LEGAL_TRANSITIONS, TERMINAL_STATES, assert_transition, can_transition
from .planning import (
    PlanRef,
    build_deployment_plan,
    is_production_environment,
    profile_for,
)
from .repository import (
    DeploymentExecutionRepository,
    InMemoryDeploymentExecutionRepository,
)

__all__ = [
    # domain
    "DeploymentExecution", "DeploymentExecutionState", "DeploymentExecutionResult",
    "DeploymentExecutionView", "DeploymentExecutionSummary", "DeploymentExecutionHistory",
    "DeploymentJob", "StateTransition", "ApprovalRecord", "ApprovalDecision",
    "RollbackTarget", "RollbackRecord", "RollbackReason", "HealthReport", "HealthState",
    # lifecycle
    "LEGAL_TRANSITIONS", "TERMINAL_STATES", "assert_transition", "can_transition",
    # engine + coordinators
    "DeploymentEngine", "DeploymentActuator", "DeploymentQueue", "DeploymentScheduler",
    "ApprovalCoordinator", "RollbackCoordinator", "HealthVerifier", "CancellationManager",
    "RetryManager", "TimeoutManager", "DeploymentExecutor",
    # infrastructure
    "InfrastructureAdapter", "InfrastructureCapabilities", "InfrastructureRegistry",
    "LocalDeploymentAdapter", "ApplyResult", "HealthProbe", "INFRASTRUCTURE_TARGETS",
    # repository
    "DeploymentExecutionRepository", "InMemoryDeploymentExecutionRepository",
    # planning (read-only reuse of Epic 7 DeploymentPlan)
    "PlanRef", "build_deployment_plan", "is_production_environment", "profile_for",
    # events + errors
    "DeploymentEventType", "ActuatorError", "IllegalTransitionError", "DeploymentNotFoundError",
    "ApprovalError", "InvalidDeploymentPlanError", "AdapterNotFoundError",
    "ProductionAdapterUnavailableError", "InfrastructureError", "DeploymentTimeoutError",
    "DeploymentCancelledError",
]
