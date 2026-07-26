"""integration — Agent Platform Integration (Epic 6, M6.7B-1: Core Integration,
Execution Wiring, Context Propagation).

Per the approved M6.7A design (`docs/architecture/epic6/agent-platform-
integration-design.md`): the platform's **second composition root**, mirroring
`pipeline/`'s own role for the six Epic 5 runtimes but one layer up — wiring
Agent Runtime's delegation lineage (Agent→Workflow, Agent/Loop→Reasoning)
together with the already-composed `pipeline.RuntimePipeline`.

**This layer owns composition only** (design §1): request entry, wiring,
context/trace/cancellation propagation. It is **not** a fourteenth capability —
no domain model, no lifecycle, no registry — exactly like `pipeline/` and
`bridge/`. Every integration point is expressed as *composition* (a bridge
adapter implementing an already-frozen port by delegating to an already-
certified concrete runtime), never a new field/method/behaviour inside any
certified capability. All thirteen certified capabilities remain untouched and
frozen.

Bundle 1 delivers: the composition root (`PlatformBuilder`/
`PlatformCompositionRoot`/`PlatformBootstrap`/`PlatformFactory`), the
request/response shapes and one root `trace_id` + one
`IntegrationCancellationToken`, the bridge adapters (`PipelineRoutedReasoningPort`,
`WorkflowDelegationBridge`, `WorkflowPortBridge`, `AgentPortBridge`,
`AgentPlanValidator`), and reference-only `IntegrationDiagnostics`. Replay,
observability aggregation, failure/retry handling, and the plan-revision
end-to-end path are explicit Bundle 2 (M6.7B-2) scope.

`integration → core, tenant, agent, workflow, loop, tool, skill, hooks,
pipeline, bridge` only — and never the reverse (design §15). No certified
capability imports `integration`.
"""

from .bootstrap import (
    PlatformBootstrap,
    PlatformBuilder,
    PlatformCompositionRoot,
    PlatformFactory,
)
from .bridges import (
    AgentPlanValidator,
    AgentPortBridge,
    PipelineRoutedReasoningPort,
    PlanRevisionCoordinator,
    PlatformCapabilityResolver,
    WorkflowDelegationBridge,
    WorkflowPortBridge,
)
from .cancellation import IntegrationCancellationToken
from .config import IntegrationConfiguration, PlatformConfiguration
from .context import IntegrationExecutionContext, default_trace_id
from .diagnostics import IntegrationDiagnostics, build_diagnostics
from .errors import (
    FailureTranslator,
    IntegrationError,
    IntegrationFailurePolicy,
    PlatformFailureSummary,
)
from .http import HttpErrorResponse, translate_error
from .observability import (
    LatencyRecorder,
    PlatformCorrelation,
    PlatformMetrics,
    PlatformSpan,
    PlatformTrace,
    RoutingLatencyStats,
)
from .replay import (
    PlatformReplayCorrelation,
    PlatformReplayIndex,
    PlatformReplayQuery,
    ReplayArtifact,
)
from .request import (
    PlatformExecution,
    PlatformExecutionContext,
    PlatformRequest,
    PlatformResponse,
)

RUNTIME_VERSION = "6.7.0"

__all__ = [
    "RUNTIME_VERSION",
    # bootstrap / composition root
    "PlatformBuilder",
    "PlatformCompositionRoot",
    "PlatformBootstrap",
    "PlatformFactory",
    # configuration
    "IntegrationConfiguration",
    "PlatformConfiguration",
    # request entry
    "PlatformRequest",
    "PlatformExecution",
    "PlatformExecutionContext",
    "PlatformResponse",
    # context / trace / cancellation
    "IntegrationExecutionContext",
    "default_trace_id",
    "IntegrationCancellationToken",
    # bridges
    "PipelineRoutedReasoningPort",
    "WorkflowDelegationBridge",
    "WorkflowPortBridge",
    "AgentPortBridge",
    "AgentPlanValidator",
    "PlatformCapabilityResolver",
    "PlanRevisionCoordinator",
    # diagnostics
    "IntegrationDiagnostics",
    "build_diagnostics",
    # replay correlation (Bundle 2)
    "PlatformReplayCorrelation",
    "PlatformReplayIndex",
    "PlatformReplayQuery",
    "ReplayArtifact",
    # observability (Bundle 2)
    "PlatformTrace",
    "PlatformSpan",
    "PlatformMetrics",
    "PlatformCorrelation",
    "LatencyRecorder",
    "RoutingLatencyStats",
    # failure + HTTP (Bundle 2)
    "IntegrationFailurePolicy",
    "FailureTranslator",
    "PlatformFailureSummary",
    "HttpErrorResponse",
    "translate_error",
    # errors
    "IntegrationError",
]
