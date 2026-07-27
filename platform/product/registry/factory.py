"""ProductPackager (M7.0B-3) — assembles an immutable ProductPackage.

The one place a `ProductPackage` is constructed: it takes a validated
`ProductDefinition` and a successful `CompilationResult` and derives the identity
chain, integrity hashes, compatibility matrix, and provenance, producing a
content-addressed, immutable package. Deterministic and side-effect free — it never
executes, deploys, or communicates. Author-supplied `creator`/`created_at` are
descriptive provenance and never enter the package identity (so identity stays
reproducible).
"""

from __future__ import annotations

from product.compiler import CompilationResult
from product.compiler.serializer import (
    canonical_hash,
    compiled_to_dict,
    graph_to_dict,
    report_to_dict,
)
from product.domain import ProductDefinition
from product.errors import ProductError
from product.serializer import ProductSerializer

from .identity import PackageIdentity, manifest_id_for, package_id_for
from .integrity import IntegrityMetadata, SignatureMetadata
from .package import PackageManifest, ProductPackage
from .provenance import ProvenanceRecord
from .serializer import SCHEMA_VERSION, package_hash_for
from .versioning import CompatibilityMatrix

_PRODUCT_SERIALIZER = ProductSerializer()


class ProductPackager:
    """Stateless. `package` raises `ProductError` only for programmer misuse
    (packaging a failed compilation); all content-level problems are surfaced later
    by `PackageValidator`."""

    def package(
        self,
        definition: ProductDefinition,
        result: CompilationResult,
        *,
        package_version: str = "1.0.0",
        creator: str = "",
        created_at: str = "",
        certified: bool = False,
        signature: SignatureMetadata | None = None,
    ) -> ProductPackage:
        if result.compiled is None:
            raise ProductError(
                "cannot package a failed compilation "
                f"({result.report.summary.error_count} error(s))"
            )
        compiled = result.compiled
        manifest = definition.manifest

        manifest_hash = compiled.manifest_hash
        manifest_id = manifest_id_for(manifest_hash)
        definition_id = compiled.definition_id
        compilation_id = compiled.content_id

        product_manifest_dict = _PRODUCT_SERIALIZER.manifest_to_dict(manifest)
        compiled_dict = compiled_to_dict(compiled)
        report_dict = report_to_dict(result.report)
        package_hash = package_hash_for(
            product_manifest_dict, compiled_dict, report_dict, package_version
        )
        package_id = package_id_for(
            manifest_id, definition_id, compilation_id, package_version, package_hash
        )

        integrity = IntegrityMetadata(
            package_hash=package_hash,
            manifest_hash=manifest_hash,
            compilation_hash=compiled.content_hash,
            dependency_hash=canonical_hash(graph_to_dict(compiled.dependency_graph)),
        )
        compatibility_matrix = CompatibilityMatrix(
            package_version=package_version,
            product_version=manifest.metadata.version,
            manifest_interface_version=manifest.interface_version,
            schema_version=compiled.compatibility.schema_version,
            compiler_version=compiled.compilation.compiler_version,
            requires_execution=compiled.compatibility.requires_execution,
            requires_runtime=compiled.compatibility.requires_runtime,
        )
        sig = signature or SignatureMetadata()
        provenance = ProvenanceRecord(
            creator=creator,
            compiler_version=compiled.compilation.compiler_version,
            binding_snapshot_id=compiled.compilation.snapshot_id,
            manifest_id=manifest_id,
            definition_id=definition_id,
            compilation_id=compilation_id,
            package_id=package_id,
            created_at=created_at,
            signature=sig,
        )
        package_manifest = PackageManifest(
            identity=PackageIdentity(
                manifest_id=manifest_id,
                definition_id=definition_id,
                compilation_id=compilation_id,
                package_id=package_id,
            ),
            package_version=package_version,
            product_version=manifest.metadata.version,
            manifest_interface_version=manifest.interface_version,
            schema_version=SCHEMA_VERSION,
            compiler_version=compiled.compilation.compiler_version,
            integrity=integrity,
            provenance=provenance,
            signature=sig,
            compatibility_matrix=compatibility_matrix,
            created_at=created_at,
            creator=creator,
            certified=certified,
        )
        return ProductPackage(
            product_manifest=manifest,
            compiled_product=compiled,
            compilation_report=result.report,
            package_manifest=package_manifest,
            content_id=package_id,
            content_hash=package_hash,
        )
