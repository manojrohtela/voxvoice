"""skill — the Skill Runtime (Epic 6, M6.2B-1: core).

Per the approved M6.2A design: a Skill is a reusable, versioned, declarative
capability that *composes* Tools, other Skills, and runtime services — never
an Agent, Workflow, Tool, Prompt, or executable code, and never executed by
this runtime. Built on the same closed-runtime shape as Tool Runtime
(manifest/descriptor separation, `core.fsm` lifecycle, registry/catalog
separation, factory composition root) but with exactly ONE lifecycle FSM —
there is no second, execution-lifecycle FSM here, because a Skill never
executes.

M6.2B-1 delivers the core: domain (manifest/descriptor), the single lifecycle
FSM, `SkillRegistry` (registration/discovery/namespacing/version-resolution/
dependency-graph validation — no execution, no capability resolution) +
`SkillCapabilityCatalog` (no intelligence), structural manifest validation
(including `interface_version` support), the temporary `SkillRepository`
(a registration log — the registry itself is the durable-for-process
catalog), serializer/config/factory, and `CapabilityResolver` as an
interface-only extension point. Planning, execution context, policies,
observability, and replay are explicit non-goals here (Bundle 2).

`skill → core[, tenant]` only. Skill Runtime imports no sibling runtime,
including Tool Runtime — capability resolution is always deferred to a
future caller via the injected `CapabilityResolver` seam.
"""

from .catalog import SkillCapabilityCatalog
from .config import CollectionsConfig, SkillConfiguration, StoreConfig
from .domain import (
    CapabilityKind,
    SkillCapabilityRequirement,
    SkillDependency,
    SkillDescriptor,
    SkillLifecycleState,
    SkillManifest,
    SkillResource,
    SkillStatistics,
    SkillVisibility,
)
from .extensions import (
    CapabilityResolver,
    SkillObservabilitySink,
    SkillPolicyEngine,
    SkillReplaySink,
)
from .factory import SkillFactory
from .ids import content_hash, descriptor_id_for, registration_id_for
from .lifecycle import SKILL_LIFECYCLE, SkillTrigger, advance_skill
from .registry import SkillRegistry
from .repositories import SkillRepository
from .runtime import SkillRuntime
from .serializer import SkillSerializer
from .validation import SUPPORTED_INTERFACE_VERSIONS, validate_manifest

__all__ = [
    # runtime + composition
    "SkillRuntime",
    "SkillFactory",
    "SkillRegistry",
    "SkillCapabilityCatalog",
    "SkillSerializer",
    # domain
    "SkillManifest",
    "SkillDescriptor",
    "SkillCapabilityRequirement",
    "SkillDependency",
    "SkillResource",
    "SkillStatistics",
    "SkillLifecycleState",
    "SkillVisibility",
    "CapabilityKind",
    # ids
    "content_hash",
    "descriptor_id_for",
    "registration_id_for",
    # lifecycle
    "SKILL_LIFECYCLE",
    "SkillTrigger",
    "advance_skill",
    # validation
    "validate_manifest",
    "SUPPORTED_INTERFACE_VERSIONS",
    # repository
    "SkillRepository",
    # config
    "SkillConfiguration",
    "StoreConfig",
    "CollectionsConfig",
    # extension hooks (interfaces only)
    "CapabilityResolver",
    "SkillPolicyEngine",
    "SkillObservabilitySink",
    "SkillReplaySink",
]
