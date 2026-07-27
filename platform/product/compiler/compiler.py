"""ProductCompiler (M7.0B-2) — the deterministic compilation pipeline.

Transforms a validated `ProductDefinition` into an immutable `CompiledProduct` and a
machine-readable `CompilationReport`, via eight independently-testable stages:

    1 validate definition → 2 resolve bindings → 3 resolve configuration →
    4 analyze dependencies → 5 validate ownership → 6 validate compatibility →
    7 produce CompiledProduct → 8 produce CompilationReport

Every stage is a pure function of (ProductDefinition, BindingSnapshot,
CompilationContext); the compiler never executes anything, performs no I/O, and
holds no state. All stages run so the report is complete, but a `CompiledProduct` is
only produced when there are zero error-severity findings — otherwise the result is
a failure carrying the same deterministic report. Identical inputs (definition +
snapshot + compiler version + context) yield a byte-identical result.
"""

from __future__ import annotations

from dataclasses import dataclass

from product.diagnostics import ProductDiagnostics
from product.domain import ProductDefinition
from product.validation import ProductValidator

from .compatibility import CompatibilityValidator
from .compiled import CompilationMetadata, CompiledProduct
from .configuration import ConfigurationResolver
from .context import CompilationContext
from .graph import DependencyAnalyzer
from .ownership import OwnershipValidator
from .report import CompilationReport, CompilationSummary
from .resolver import BindingResolver
from .serializer import canonical_hash, compiled_to_dict


@dataclass(frozen=True, slots=True)
class CompilationResult:
    """The compiler's output. `compiled` is present iff compilation succeeded (no
    error findings); the `report` is always present and always deterministic."""

    compiled: CompiledProduct | None
    report: CompilationReport

    @property
    def success(self) -> bool:
        return self.compiled is not None


class ProductCompiler:
    """Stateless, deterministic. Compose sub-validators once; reuse freely."""

    def __init__(self) -> None:
        self._definition_validator = ProductValidator()
        self._binding_resolver = BindingResolver()
        self._configuration_resolver = ConfigurationResolver()
        self._dependency_analyzer = DependencyAnalyzer()
        self._ownership_validator = OwnershipValidator()
        self._compatibility_validator = CompatibilityValidator()

    def compile(
        self, definition: ProductDefinition, context: CompilationContext
    ) -> CompilationResult:
        manifest = definition.manifest
        findings: list = []

        # Stage 1 — validate the definition's manifest (fail-fast surface, but we
        # collect rather than raise so the report is complete).
        findings.extend(self._definition_validator.validate(manifest).findings)

        # Stage 2 — resolve bindings against the injected snapshot (ADR-0019).
        resolution = self._binding_resolver.resolve(manifest, context.snapshot)
        findings.extend(resolution.findings)

        # Stage 3 — resolve configuration precedence (Default→Deployment).
        config_resolution = self._configuration_resolver.resolve(manifest, context)
        findings.extend(config_resolution.findings)

        # Stage 4 — analyze dependencies over the resolved bindings.
        analysis = self._dependency_analyzer.analyze(resolution.bindings)
        findings.extend(analysis.findings)

        # Stage 5 — ownership: the product must never assert execution ownership.
        ownership = self._ownership_validator.validate(manifest)
        findings.extend(ownership.findings)

        # Stage 6 — static compatibility.
        compatibility = self._compatibility_validator.validate(manifest, context)
        findings.extend(compatibility.findings)

        diagnostics = ProductDiagnostics.of(findings)
        metadata = CompilationMetadata(
            compiler_version=context.compiler_version,
            snapshot_id=context.snapshot.snapshot_id,
            environment=context.environment,
            tenant_id=context.tenant_id,
            execution_platform_version=context.execution_platform_version,
            runtime_platform_version=context.runtime_platform_version,
        )

        # Stage 7 — produce the CompiledProduct (only when error-free).
        compiled: CompiledProduct | None = None
        content_hash = ""
        content_id = ""
        if not diagnostics.has_errors:
            draft = CompiledProduct(
                definition_id=definition.content_id,
                manifest_hash=definition.content_hash,
                bindings=resolution.bindings,
                configuration=config_resolution.configuration,
                dependency_graph=analysis.graph,
                compatibility=compatibility.metadata,
                compilation=metadata,
                diagnostics=diagnostics,
            )
            content_hash = canonical_hash(_identity_payload(draft))
            content_id = "cp_" + content_hash[:16]
            compiled = CompiledProduct(
                definition_id=draft.definition_id,
                manifest_hash=draft.manifest_hash,
                bindings=draft.bindings,
                configuration=draft.configuration,
                dependency_graph=draft.dependency_graph,
                compatibility=draft.compatibility,
                compilation=draft.compilation,
                diagnostics=draft.diagnostics,
                content_id=content_id,
                content_hash=content_hash,
            )

        # Stage 8 — produce the CompilationReport (always; deterministic on both
        # success and failure). The compilation identity is derived from the same
        # canonical payload, so it is stable even when compilation fails.
        compilation_id = "cx_" + canonical_hash(
            _report_identity_payload(definition, metadata, diagnostics)
        )[:16]
        report = CompilationReport(
            summary=CompilationSummary(
                status="success" if compiled is not None else "failed",
                error_count=len(diagnostics.errors),
                warning_count=len(diagnostics.warnings),
                binding_count=len(resolution.bindings),
                node_count=len(analysis.graph.nodes),
                edge_count=len(analysis.graph.edges),
            ),
            diagnostics=diagnostics,
            dependency_report=analysis.report,
            compatibility_report=compatibility.report,
            ownership_report=ownership.report,
            configuration_report=config_resolution.report,
            compilation_id=compilation_id,
        )
        return CompilationResult(compiled=compiled, report=report)


def _identity_payload(cp: CompiledProduct) -> dict:
    """The canonical payload the CompiledProduct identity hashes over — everything
    that defines the artifact, excluding the identity fields themselves."""
    d = compiled_to_dict(cp)
    d.pop("content_id", None)
    d.pop("content_hash", None)
    return d


def _report_identity_payload(
    definition: ProductDefinition,
    metadata: CompilationMetadata,
    diagnostics: ProductDiagnostics,
) -> dict:
    """Stable identity for the compilation *event* — tied to the definition, the
    exact compiler/snapshot/context, and the resulting diagnostics."""
    return {
        "definition_id": definition.content_id,
        "manifest_hash": definition.content_hash,
        "compiler_version": metadata.compiler_version,
        "snapshot_id": metadata.snapshot_id,
        "environment": metadata.environment,
        "tenant_id": metadata.tenant_id,
        "execution_platform_version": metadata.execution_platform_version,
        "runtime_platform_version": metadata.runtime_platform_version,
        "diagnostics": [
            {"path": d.path, "code": d.code.value, "severity": d.severity.value}
            for d in diagnostics.findings
        ],
    }
