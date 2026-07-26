"""integration.bridges — the composition-root adapter classes (M6.7B-1).

Each bridge implements an **already-frozen** port by delegating to an
**already-certified** concrete runtime — never a new field, method, or
behaviour inside a capability (design §1, §2). The same category of artifact
as `bridge.ProviderModelBridge`, extended one layer up:

- `PipelineRoutedReasoningPort` — `core.ports.reasoning.ReasoningPort` backed
  by `pipeline.RuntimePipeline` (design §11).
- `WorkflowDelegationBridge` — the concrete `agent.extensions.AgentWorkflowPort`
  backed by `workflow.execution.WorkflowExecutionRuntime` (design §6).
- `WorkflowPortBridge` — the frozen `core.ports.workflow.WorkflowPort` backed
  by the same runtime, closing Deferred Decisions item 10 (design §18).
- `AgentPortBridge` — the frozen `core.ports.agent.AgentPort` backed by
  `agent.AgentRegistry`, closing Deferred Decisions item 1 (design §18).
- `AgentPlanValidator` — the first real `loop.LoopValidator` (design §5.4).
"""

from .agent_port import AgentPortBridge
from .capability_resolver import PlatformCapabilityResolver
from .plan_revision import PlanRevisionCoordinator
from .plan_validator import AgentPlanValidator
from .reasoning_port import PipelineRoutedReasoningPort
from .refs import parse_ref, parse_workflow_ref
from .workflow_delegation import WorkflowDelegationBridge
from .workflow_port import WorkflowPortBridge

__all__ = [
    "PipelineRoutedReasoningPort",
    "WorkflowDelegationBridge",
    "WorkflowPortBridge",
    "AgentPortBridge",
    "AgentPlanValidator",
    "PlatformCapabilityResolver",
    "PlanRevisionCoordinator",
    "parse_ref",
    "parse_workflow_ref",
]
