"""hooks — the Hook Framework (Epic 6, M6.3B: complete implementation).

Per the approved M6.3A design: a Hook is a deterministic, cross-cutting
lifecycle-interception point producing exactly one verdict (ALLOW/DENY/
MODIFY) — never a Skill, Tool, Agent, Workflow, Provider, or business logic.
The Framework owns the interception mechanism (registration, ordering,
filtering, execution policy, observability, replay) and never owns *what a
hook decides* — that is the injected `Hook.evaluate` callback's business.

Built on the same closed-runtime shape as Tool/Skill Runtime (manifest/
descriptor separation, `core.fsm` lifecycle, registry/filter separation,
factory composition root) but with exactly ONE lifecycle FSM — a hook never
executes a multi-phase action, it only returns one verdict per event.

`HookCategory` (validation/transformation/audit/notification/metrics) is
descriptive manifest metadata ONLY — it never affects ordering, filtering,
execution policy, or aggregation.

`hooks → core[, tenant]` only. The Hook Framework imports no sibling runtime
— not Tool Runtime, not Skill Runtime, not Workflow/Agent Runtime. It is a
library every future consumer runtime imports, never the reverse. Wiring
hook call-sites into Tool Runtime and Skill Runtime is explicitly deferred
(both are certified and frozen) — this bundle implements the Framework only,
with zero integration into any existing runtime.
"""

from .config import HookCollectionsConfig, HookConfiguration, StoreConfig
from .context import build_event
from .diagnostics import build_diagnostics
from .domain import (
    HookCapabilityRequirement,
    HookCategory,
    HookDescriptor,
    HookEvent,
    HookExecutionTrace,
    HookFailure,
    HookLifecycleState,
    HookManifest,
    HookOutcome,
    HookPoint,
    HookReplayRecord,
    HookRunResult,
    HookRuntimeDiagnostics,
    HookRuntimeScope,
    HookRuntimeStatistics,
    HookStatistics,
    HookVerdict,
    ReplayComparison,
)
from .engine import HookEngine
from .events import PHASE_EVENT_TYPE, HookPhase
from .extensions import Hook, HookObservabilitySink, HookReplaySink
from .factory import HookFactory
from .filter import HookFilter
from .ids import content_hash, descriptor_id_for, event_id_for, registration_id_for, replay_id_for
from .lifecycle import HOOK_LIFECYCLE, HookTrigger, advance_hook
from .observability import InMemoryHookObservabilitySink, trace_for
from .ordering import ordered_hooks
from .policy import (
    HookAggregationRule,
    HookExecutionPolicy,
    HookFailureMode,
    aggregate_verdicts,
    default_policy_for,
)
from .registry import HookRegistry
from .replay import hook_replay_record_for
from .replay_engine import HookReplayEngine
from .repositories import HookRepository
from .runtime import HookRuntime
from .serializer import HookSerializer
from .statistics import compute_statistics
from .validation import SUPPORTED_INTERFACE_VERSIONS, validate_manifest

__all__ = [
    # runtime + composition
    "HookRuntime",
    "HookFactory",
    "HookRegistry",
    "HookFilter",
    "HookEngine",
    "HookSerializer",
    # domain
    "HookManifest",
    "HookDescriptor",
    "HookLifecycleState",
    "HookPoint",
    "HookRuntimeScope",
    "HookCategory",
    "HookCapabilityRequirement",
    "HookEvent",
    "HookOutcome",
    "HookVerdict",
    "HookFailure",
    "HookRunResult",
    "HookExecutionTrace",
    "HookReplayRecord",
    "ReplayComparison",
    "HookRuntimeDiagnostics",
    "HookRuntimeStatistics",
    "HookStatistics",
    # ordering + policy
    "ordered_hooks",
    "HookFailureMode",
    "HookAggregationRule",
    "HookExecutionPolicy",
    "default_policy_for",
    "aggregate_verdicts",
    # context
    "build_event",
    # ids
    "content_hash",
    "descriptor_id_for",
    "registration_id_for",
    "event_id_for",
    "replay_id_for",
    # lifecycle
    "HOOK_LIFECYCLE",
    "HookTrigger",
    "advance_hook",
    # validation
    "validate_manifest",
    "SUPPORTED_INTERFACE_VERSIONS",
    # observability
    "trace_for",
    "InMemoryHookObservabilitySink",
    # replay
    "hook_replay_record_for",
    "HookReplayEngine",
    # diagnostics + statistics
    "build_diagnostics",
    "compute_statistics",
    # repository + config
    "HookRepository",
    "HookConfiguration",
    "HookCollectionsConfig",
    "StoreConfig",
    # extension hooks (interfaces only, except Hook itself which is the
    # extension point third parties implement concretely)
    "Hook",
    "HookObservabilitySink",
    "HookReplaySink",
    # events
    "HookPhase",
    "PHASE_EVENT_TYPE",
]
