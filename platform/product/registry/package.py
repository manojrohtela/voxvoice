"""ProductPackage & friends (M7.0B-3) — the immutable distributable artifact.

A `ProductPackage` is the ONLY deployable artifact of Product Runtime. It contains
only inert, resolved data (manifest, compiled product, package manifest, compilation
report — the dependency graph and compatibility are exposed as properties off the
compiled product, never duplicated). **No runtime state, no deployment state, no
network handles, no execution objects.** Immutable throughout.
"""

from __future__ import annotations

from dataclasses import dataclass

from product.compiler import CompilationReport, CompiledProduct, DependencyGraph
from product.compiler.compatibility import CompatibilityMetadata
from product.domain import ProductManifest

from .identity import PackageIdentity
from .integrity import IntegrityMetadata, SignatureMetadata
from .provenance import ProvenanceRecord
from .versioning import CompatibilityMatrix


@dataclass(frozen=True, slots=True)
class ProductPackageMetadata:
    """A lightweight, immutable summary of a package — what the registry indexes and
    returns without materializing the full package."""

    package_id: str
    qualified_name: str  # "namespace.id"
    package_version: str
    product_version: str
    manifest_id: str
    definition_id: str
    compilation_id: str
    compiler_version: str
    created_at: str = ""
    creator: str = ""
    certified: bool = False


@dataclass(frozen=True, slots=True)
class PackageManifest:
    """The package's own descriptor (distinct from the product's `ProductManifest`):
    identity + versions + integrity + provenance + signature + compatibility matrix."""

    identity: PackageIdentity
    package_version: str
    product_version: str
    manifest_interface_version: str
    schema_version: str
    compiler_version: str
    integrity: IntegrityMetadata
    provenance: ProvenanceRecord
    signature: SignatureMetadata
    compatibility_matrix: CompatibilityMatrix
    created_at: str = ""
    creator: str = ""
    certified: bool = False


@dataclass(frozen=True, slots=True)
class ProductPackage:
    """The immutable, content-addressed distributable artifact."""

    product_manifest: ProductManifest
    compiled_product: CompiledProduct
    compilation_report: CompilationReport
    package_manifest: PackageManifest
    content_id: str = ""  # == package_manifest.identity.package_id
    content_hash: str = ""  # == package_manifest.integrity.package_hash

    # ── convenience projections (never duplicated state) ──────────────────────
    @property
    def package_id(self) -> str:
        return self.package_manifest.identity.package_id

    @property
    def qualified_name(self) -> str:
        return self.product_manifest.qualified_name

    @property
    def package_version(self) -> str:
        return self.package_manifest.package_version

    @property
    def dependency_graph(self) -> DependencyGraph:
        return self.compiled_product.dependency_graph

    @property
    def compatibility(self) -> CompatibilityMetadata:
        return self.compiled_product.compatibility

    def metadata(self) -> ProductPackageMetadata:
        pm = self.package_manifest
        return ProductPackageMetadata(
            package_id=pm.identity.package_id,
            qualified_name=self.qualified_name,
            package_version=pm.package_version,
            product_version=pm.product_version,
            manifest_id=pm.identity.manifest_id,
            definition_id=pm.identity.definition_id,
            compilation_id=pm.identity.compilation_id,
            compiler_version=pm.compiler_version,
            created_at=pm.created_at,
            creator=pm.creator,
            certified=pm.certified,
        )
