"""product.compiler — the Product Compiler & Binding Resolution layer (Epic 7, M7.0B-2).

Transforms a validated `ProductDefinition` into an immutable `CompiledProduct` +
machine-readable `CompilationReport` through a deterministic, side-effect-free,
independently-testable pipeline. Resolves manifest references into immutable
bindings against an injected `BindingSnapshot` (ADR-0019/0020) — never a live Epic 6
registry, never executing, initializing, loading, or communicating with anything.

Non-goals of this bundle (explicit): packaging, product registry, deployment, and
any runtime execution. `product → core` only (no sibling runtime imported;
AST-test-enforced).
"""

from .bindings import (
    AgentBinding,
    Binding,
    BindingEdge,
    BindingKind,
    KnowledgeBinding,
    MCPBinding,
    ProviderBinding,
    SkillBinding,
    ToolBinding,
    WorkflowBinding,
    make_binding,
)
from .compatibility import (
    CompatibilityMetadata,
    CompatibilityReport,
    CompatibilityResult,
    CompatibilityValidator,
)
from .compiled import CompilationMetadata, CompiledProduct
from .compiler import CompilationResult, ProductCompiler
from .configuration import (
    ConfigurationReport,
    ConfigurationResolution,
    ConfigurationResolver,
    OverrideTrace,
    ResolvedConfiguration,
)
from .context import COMPILER_VERSION, CompilationContext
from .extensions import CompilationCache, IncrementalCompiler
from .graph import (
    DependencyAnalysis,
    DependencyAnalyzer,
    DependencyGraph,
    DependencyReport,
    GraphEdge,
    GraphNode,
)
from .ownership import OwnershipReport, OwnershipResult, OwnershipValidator
from .report import CompilationReport, CompilationSummary
from .resolver import BindingResolution, BindingResolver
from .serializer import canonical_hash, compiled_to_dict, report_to_dict
from .snapshot import BindingSnapshot, BindingSnapshotEntry
from .versions import highest_version, is_compatible, parse_semver, version_sort_key

__all__ = [
    # pipeline
    "ProductCompiler",
    "CompilationResult",
    "CompilationContext",
    "COMPILER_VERSION",
    # artifacts
    "CompiledProduct",
    "CompilationMetadata",
    "CompilationReport",
    "CompilationSummary",
    # bindings
    "Binding",
    "BindingKind",
    "BindingEdge",
    "make_binding",
    "AgentBinding",
    "WorkflowBinding",
    "SkillBinding",
    "ToolBinding",
    "ProviderBinding",
    "MCPBinding",
    "KnowledgeBinding",
    # resolution
    "BindingResolver",
    "BindingResolution",
    "BindingSnapshot",
    "BindingSnapshotEntry",
    # dependency analysis
    "DependencyAnalyzer",
    "DependencyAnalysis",
    "DependencyGraph",
    "DependencyReport",
    "GraphNode",
    "GraphEdge",
    # configuration
    "ConfigurationResolver",
    "ConfigurationResolution",
    "ResolvedConfiguration",
    "ConfigurationReport",
    "OverrideTrace",
    # compatibility
    "CompatibilityValidator",
    "CompatibilityResult",
    "CompatibilityMetadata",
    "CompatibilityReport",
    # ownership
    "OwnershipValidator",
    "OwnershipResult",
    "OwnershipReport",
    # serialization
    "compiled_to_dict",
    "report_to_dict",
    "canonical_hash",
    # versions
    "parse_semver",
    "version_sort_key",
    "highest_version",
    "is_compatible",
    # extension points (interfaces only)
    "CompilationCache",
    "IncrementalCompiler",
]
