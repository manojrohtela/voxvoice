"""Product domain model (M7.0B-1) — immutable value objects.

Per ADR-0018 and the approved M7.0A design: a `ProductManifest` (the declarative,
versioned, diffable **contract only** — data, never executable code, the ADR-0008
"manifest-not-code" discipline applied one layer up) and the validated,
runtime-loaded `ProductDefinition` (manifest + canonical identity — a distinct
object from the manifest, never conflated, mirroring
`skill.domain.SkillManifest`/`SkillDescriptor`'s own separation).

Every reference in this bundle is a **placeholder** — declared structural data the
Product Runtime never resolves into an Epic 6 registry entry (that is a later
bundle's binding step, out of scope here). Secret references carry a *pointer*
only, never a value (§Security of the M7.0A design). Everything is frozen + slotted
(deterministic, cheap, diff-friendly). No execution logic here or anywhere in this
bundle.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any


class ProductVisibility(StrEnum):
    PUBLIC = "public"
    ORG = "org"
    PRIVATE = "private"


class DeploymentStrategy(StrEnum):
    """Declared rollout intent — descriptive metadata only; the Product Runtime
    never actuates a deployment (§Deployment of the M7.0A design). Shared by the
    manifest's `DeploymentMetadata` (M7.0B-1) and the deployment planning layer's
    `DeploymentPolicy` (M7.0C); `IMMEDIATE`/`MANUAL_APPROVAL` were added additively
    for M7.0C's rollout policies."""

    BLUE_GREEN = "blue_green"
    CANARY = "canary"
    ROLLING = "rolling"
    RECREATE = "recreate"
    IMMEDIATE = "immediate"
    MANUAL_APPROVAL = "manual_approval"


# ── reference placeholders (declared, never resolved in this bundle) ──────────────


@dataclass(frozen=True, slots=True)
class AgentRef:
    """A declared reference to an Agent this product composes. `version=None`
    means "unpinned". `workflow_refs`/`skill_refs` name the composition spine
    declaratively — plain identifiers, never resolved here."""

    id: str
    namespace: str = "internal"
    version: str | None = None
    workflow_refs: tuple[str, ...] = ()
    skill_refs: tuple[str, ...] = ()
    config: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkflowRef:
    id: str
    namespace: str = "internal"
    version: str | None = None
    config: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SkillRef:
    id: str
    namespace: str = "internal"
    version: str | None = None
    config: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolRef:
    id: str
    namespace: str = "internal"
    version: str | None = None
    config: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderRef:
    id: str
    namespace: str = "internal"
    version: str | None = None
    config: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MCPServerRef:
    id: str
    namespace: str = "internal"
    version: str | None = None
    config: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class KnowledgeRef:
    id: str
    namespace: str = "internal"
    version: str | None = None
    config: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SecretReference:
    """An opaque pointer to a secret — `provider` + `path` (+ optional pinned
    `version`), resolved by the runtime's secret provider at a far later stage,
    never here. It must NEVER carry a secret value; manifest integrity validation
    rejects any attempt to (§Security of the M7.0A design)."""

    ref: str
    provider: str
    path: str
    version: str | None = None


# ── configuration layers (declared; precedence resolution is a later bundle) ─────


@dataclass(frozen=True, slots=True)
class EnvironmentProfile:
    """A named override layer (dev/test/qa/staging/prod/...). Overrides are inert
    declared data in this bundle — the precedence engine that applies them is a
    later bundle."""

    name: str
    overrides: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TenantOverride:
    """A per-tenant override layer, applied above environment by the (future)
    resolver. Declared data only here."""

    tenant_id: str
    overrides: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FeatureFlag:
    key: str
    default: bool = False
    description: str = ""


@dataclass(frozen=True, slots=True)
class DeploymentMetadata:
    """Declared deployment intent — strategy + target environment names + declared
    resource limits. Descriptive only; no orchestration in this bundle."""

    strategy: DeploymentStrategy = DeploymentStrategy.ROLLING
    target_environments: tuple[str, ...] = ()
    resource_limits: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VersionMetadata:
    """Compatibility + rollback metadata. Compatibility values are declared here;
    the compatibility *check* against live platform versions is a later bundle."""

    requires_runtime: str = ""
    requires_execution: str = ""
    rollback_to: str | None = None


# ── product metadata ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ProductMetadata:
    """Identity + descriptive attributes for one product. `version` is the
    product's own semantic version; distinct from `ProductManifest.
    interface_version` (the manifest schema shape)."""

    id: str
    name: str = ""
    version: str = "1.0.0"
    description: str = ""
    owner: str = ""
    created_at: str = ""  # author-supplied ISO8601; never machine-stamped here
    labels: Mapping[str, str] = field(default_factory=dict)


# ── the manifest ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ProductManifest:
    """The declarative, immutable **contract** for one product — data, never
    executable code (ADR-0018). Identity is `(namespace, metadata.id,
    metadata.version)`.

    `interface_version` is the manifest **shape/schema** version this document
    conforms to — distinct from `metadata.version` (the product's own semantic
    version). Loading fails fast for an unsupported `interface_version`
    (`validation.SUPPORTED_INTERFACE_VERSIONS`), the same "fail fast, do not run
    degraded" discipline every closed runtime's config validation uses.
    """

    metadata: ProductMetadata
    namespace: str = "internal"
    interface_version: str = "1.0"
    agents: tuple[AgentRef, ...] = ()
    workflows: tuple[WorkflowRef, ...] = ()
    skills: tuple[SkillRef, ...] = ()
    tools: tuple[ToolRef, ...] = ()
    providers: tuple[ProviderRef, ...] = ()
    mcp_servers: tuple[MCPServerRef, ...] = ()
    knowledge: tuple[KnowledgeRef, ...] = ()
    environments: tuple[EnvironmentProfile, ...] = ()
    tenants: tuple[TenantOverride, ...] = ()
    feature_flags: tuple[FeatureFlag, ...] = ()
    secrets: tuple[SecretReference, ...] = ()
    deployment: DeploymentMetadata = field(default_factory=DeploymentMetadata)
    version_metadata: VersionMetadata = field(default_factory=VersionMetadata)
    visibility: ProductVisibility = ProductVisibility.PRIVATE

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}.{self.metadata.id}"

    @property
    def version(self) -> str:
        return self.metadata.version


@dataclass(frozen=True, slots=True)
class ProductDefinition:
    """A validated, canonicalized product: the immutable `ProductManifest` plus its
    deterministic content identity. Deliberately a separate object from the
    manifest — the definition is what downstream Epic 7 bundles (compiler,
    packaging) consume. Holds **no runtime state and no execution state** — only
    the manifest and its derived, deterministic identity.

    `content_id` and `content_hash` are functions of the manifest's canonical form
    (authoring order does not affect them); `interface_version` is surfaced for
    convenience.
    """

    manifest: ProductManifest
    content_id: str
    content_hash: str

    @property
    def qualified_name(self) -> str:
        return self.manifest.qualified_name

    @property
    def version(self) -> str:
        return self.manifest.version

    @property
    def interface_version(self) -> str:
        return self.manifest.interface_version

    def with_manifest(self, manifest: ProductManifest) -> ProductDefinition:
        """Return a copy carrying a different manifest. The caller is responsible
        for recomputing identity via the factory — this does not re-hash (identity
        derivation lives in one place, the factory/ids seam)."""
        return replace(self, manifest=manifest)
