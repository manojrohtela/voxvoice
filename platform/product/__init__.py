"""product — the Product Runtime (Epic 7, M7.0B-1: Product Definition & Manifest Core).

Per ADR-0018 and the approved M7.0A design: the Product Runtime is a **declarative
composition layer** above the frozen Execution Platform (Epic 6). It *describes and
binds*; it never *executes*. This first bundle delivers only the immutable authoring
foundation — a `ProductManifest` can be loaded, parsed, validated, canonicalized,
represented as an immutable `ProductDefinition`, and reported through
`ProductDiagnostics`. Nothing compiles, resolves, packages, deploys, or registers.

References (agent/workflow/skill/tool/provider/MCP/knowledge) and secret references
are declared **placeholders** — never resolved here. Compilation, bindings,
packaging, deployment, and the registry are explicit non-goals of this bundle.

`product → core[, tenant]` only (this bundle imports only `core.errors`); no
sibling runtime is imported, and no Epic 5/6 package imports `product` (AR-15,
acyclic — test-enforced).
"""

from .canonical import canonical_json, canonical_manifest_dict
from .compiler import (
    Binding,
    BindingKind,
    BindingSnapshot,
    BindingSnapshotEntry,
    CompilationContext,
    CompilationReport,
    CompilationResult,
    CompiledProduct,
    ProductCompiler,
)
from .deployment import (
    DeploymentDescriptor,
    DeploymentPlan,
    DeploymentPlanner,
    DeploymentProfile,
    DeploymentReport,
    DeploymentTarget,
    DeploymentValidator,
)
from .diagnostics import (
    Diagnostic,
    ProductDiagnosticCode,
    ProductDiagnostics,
    Severity,
)
from .domain import (
    AgentRef,
    DeploymentMetadata,
    DeploymentStrategy,
    EnvironmentProfile,
    FeatureFlag,
    KnowledgeRef,
    MCPServerRef,
    ProductDefinition,
    ProductManifest,
    ProductMetadata,
    ProductVisibility,
    ProviderRef,
    SecretReference,
    SkillRef,
    TenantOverride,
    ToolRef,
    VersionMetadata,
    WorkflowRef,
)
from .errors import ProductError, ProductValidationError
from .factory import ProductFactory
from .ids import content_hash, definition_id_for, manifest_hash
from .registry import (
    PackageValidator,
    ProductPackage,
    ProductPackager,
    ProductRegistry,
    RegistryEntry,
    RegistryQuery,
    RegistryReport,
    RegistrySnapshot,
    VersionManager,
)
from .runtime import ProductRuntime
from .serializer import ProductSerializer
from .validation import (
    SUPPORTED_INTERFACE_VERSIONS,
    ProductValidator,
    is_valid_semver,
    validate_manifest,
)

__all__ = [
    # construction
    "ProductFactory",
    "ProductSerializer",
    # domain — aggregates
    "ProductManifest",
    "ProductDefinition",
    "ProductMetadata",
    # domain — reference placeholders
    "AgentRef",
    "WorkflowRef",
    "SkillRef",
    "ToolRef",
    "ProviderRef",
    "MCPServerRef",
    "KnowledgeRef",
    "SecretReference",
    # domain — configuration layers
    "EnvironmentProfile",
    "TenantOverride",
    "FeatureFlag",
    "DeploymentMetadata",
    "VersionMetadata",
    # domain — enums
    "ProductVisibility",
    "DeploymentStrategy",
    # validation
    "ProductValidator",
    "validate_manifest",
    "is_valid_semver",
    "SUPPORTED_INTERFACE_VERSIONS",
    # diagnostics
    "ProductDiagnostics",
    "Diagnostic",
    "ProductDiagnosticCode",
    "Severity",
    # ids + canonicalization
    "content_hash",
    "manifest_hash",
    "definition_id_for",
    "canonical_json",
    "canonical_manifest_dict",
    # errors
    "ProductError",
    "ProductValidationError",
    # compiler (M7.0B-2) — main entry points; full surface in `product.compiler`
    "ProductCompiler",
    "CompilationContext",
    "CompilationResult",
    "CompiledProduct",
    "CompilationReport",
    "BindingSnapshot",
    "BindingSnapshotEntry",
    "Binding",
    "BindingKind",
    # packaging & registry (M7.0B-3) — main entry points; full surface in `product.registry`
    "ProductPackager",
    "ProductPackage",
    "PackageValidator",
    "ProductRegistry",
    "RegistryEntry",
    "RegistrySnapshot",
    "RegistryQuery",
    "RegistryReport",
    "VersionManager",
    # deployment planning (M7.0C) — main entry points; full surface in `product.deployment`
    "DeploymentPlanner",
    "DeploymentPlan",
    "DeploymentDescriptor",
    "DeploymentValidator",
    "DeploymentReport",
    "DeploymentProfile",
    "DeploymentTarget",
    # composition façade (M7.0D/C1)
    "ProductRuntime",
]
