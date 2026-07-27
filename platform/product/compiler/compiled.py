"""CompiledProduct (M7.0B-2) — the immutable compilation artifact.

Contains only inert, resolved data: immutable bindings, resolved configuration, the
dependency graph, compatibility + compilation metadata, diagnostics, and a
deterministic identity. **No runtime state, no execution state, no network handles**
(verified by test). This is what the (future) packaging bundle will package — it is
never itself executed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from product.diagnostics import ProductDiagnostics

from .bindings import Binding
from .compatibility import CompatibilityMetadata
from .configuration import ResolvedConfiguration
from .graph import DependencyGraph


@dataclass(frozen=True, slots=True)
class CompilationMetadata:
    """The deterministic inputs that produced this artifact — enough to audit and
    reproduce it. `snapshot_id` ties the product to the exact catalog it resolved
    against (ADR-0019)."""

    compiler_version: str
    snapshot_id: str
    environment: str
    tenant_id: str | None
    execution_platform_version: str
    runtime_platform_version: str


@dataclass(frozen=True, slots=True)
class CompiledProduct:
    """Immutable, content-addressed compilation output."""

    definition_id: str
    manifest_hash: str
    bindings: tuple[Binding, ...]
    configuration: ResolvedConfiguration
    dependency_graph: DependencyGraph
    compatibility: CompatibilityMetadata
    compilation: CompilationMetadata
    diagnostics: ProductDiagnostics = field(default_factory=ProductDiagnostics)
    content_id: str = ""
    content_hash: str = ""

    @property
    def is_valid(self) -> bool:
        return not self.diagnostics.has_errors
