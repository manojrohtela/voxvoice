"""Package validation & integrity verification (M7.0B-3) — verification only.

`IntegrityValidator` recomputes a package's content hashes from its contents and
compares them to the recorded `IntegrityMetadata` — deterministic verification, no
cryptography. `PackageValidator` runs the full structural check (identity-chain
consistency, integrity, package version/schema, required metadata) and returns
deterministic `ProductDiagnostics`. Both are stateless and never mutate the package.
"""

from __future__ import annotations

from dataclasses import dataclass

from product.compiler.serializer import (
    canonical_hash,
    compiled_to_dict,
    graph_to_dict,
    report_to_dict,
)
from product.diagnostics import Diagnostic, ProductDiagnosticCode, ProductDiagnostics, Severity
from product.ids import manifest_hash as product_manifest_hash
from product.serializer import ProductSerializer
from product.validation import is_valid_semver

from .identity import manifest_id_for
from .package import ProductPackage
from .serializer import SCHEMA_VERSION, package_hash_for

_PRODUCT_SERIALIZER = ProductSerializer()


@dataclass(frozen=True, slots=True)
class IntegrityVerification:
    verified: bool
    package_hash_ok: bool
    manifest_hash_ok: bool
    compilation_hash_ok: bool
    dependency_hash_ok: bool
    findings: tuple[Diagnostic, ...]


class IntegrityValidator:
    """Stateless. Recomputes hashes from package contents and compares — verify only."""

    def verify(self, package: ProductPackage) -> IntegrityVerification:
        integ = package.package_manifest.integrity
        findings: list[Diagnostic] = []

        recomputed_manifest = product_manifest_hash(package.product_manifest)
        manifest_ok = recomputed_manifest == integ.manifest_hash
        self._check(manifest_ok, "integrity.manifest_hash", "manifest", findings)

        # The recorded compilation hash must match the embedded compiled product's id.
        compilation_ok = integ.compilation_hash == package.compiled_product.content_hash
        self._check(compilation_ok, "integrity.compilation_hash", "compilation", findings)

        recomputed_dependency = canonical_hash(graph_to_dict(package.dependency_graph))
        dependency_ok = recomputed_dependency == integ.dependency_hash
        self._check(dependency_ok, "integrity.dependency_hash", "dependency", findings)

        recomputed_package = package_hash_for(
            _PRODUCT_SERIALIZER.manifest_to_dict(package.product_manifest),
            compiled_to_dict(package.compiled_product),
            report_to_dict(package.compilation_report),
            package.package_manifest.package_version,
        )
        package_ok = (
            recomputed_package == integ.package_hash
            and recomputed_package == package.content_hash
        )
        self._check(package_ok, "integrity.package_hash", "package", findings)

        return IntegrityVerification(
            verified=manifest_ok and compilation_ok and dependency_ok and package_ok,
            package_hash_ok=package_ok,
            manifest_hash_ok=manifest_ok,
            compilation_hash_ok=compilation_ok,
            dependency_hash_ok=dependency_ok,
            findings=tuple(findings),
        )

    def _check(self, ok: bool, path: str, label: str, out: list[Diagnostic]) -> None:
        if not ok:
            out.append(
                Diagnostic(
                    path,
                    ProductDiagnosticCode.PACKAGE_INTEGRITY_MISMATCH,
                    Severity.ERROR,
                    f"{label} hash does not match the package contents",
                    "rebuild the package; its recorded integrity is inconsistent",
                )
            )


class PackageValidator:
    """Stateless structural + integrity validation. Never raises — returns diagnostics."""

    def __init__(self) -> None:
        self._integrity = IntegrityValidator()

    def validate(self, package: ProductPackage) -> ProductDiagnostics:
        findings: list[Diagnostic] = []
        pm = package.package_manifest
        identity = pm.identity

        # Identity-chain consistency.
        if package.content_id != identity.package_id:
            findings.append(
                _identity_mismatch("content_id", "package content_id != identity.package_id")
            )
        expected_manifest_id = manifest_id_for(pm.integrity.manifest_hash)
        if identity.manifest_id != expected_manifest_id:
            findings.append(
                _identity_mismatch(
                    "identity.manifest_id", "manifest_id not derived from manifest hash"
                )
            )
        if identity.definition_id != package.compiled_product.definition_id:
            findings.append(
                _identity_mismatch(
                    "identity.definition_id", "definition_id != compiled definition_id"
                )
            )
        if identity.compilation_id != package.compiled_product.content_id:
            findings.append(
                _identity_mismatch(
                    "identity.compilation_id", "compilation_id != compiled content_id"
                )
            )

        # Package version + schema.
        if not is_valid_semver(pm.package_version):
            findings.append(
                Diagnostic(
                    "package_manifest.package_version",
                    ProductDiagnosticCode.INVALID_PACKAGE_VERSION,
                    Severity.ERROR,
                    f"package_version {pm.package_version!r} is not valid SemVer",
                    "use MAJOR.MINOR.PATCH",
                )
            )
        if pm.schema_version != SCHEMA_VERSION:
            findings.append(
                Diagnostic(
                    "package_manifest.schema_version",
                    ProductDiagnosticCode.UNSUPPORTED_PACKAGE_SCHEMA,
                    Severity.ERROR,
                    f"unsupported package schema_version {pm.schema_version!r}",
                    f"expected {SCHEMA_VERSION!r}",
                )
            )

        # Required metadata.
        if not package.qualified_name or not pm.product_version:
            findings.append(
                Diagnostic(
                    "package_manifest",
                    ProductDiagnosticCode.MISSING_PACKAGE_METADATA,
                    Severity.ERROR,
                    "package is missing a qualified name or product version",
                    "ensure the product manifest carries id/namespace and a version",
                )
            )

        # Integrity.
        findings.extend(self._integrity.verify(package).findings)

        return ProductDiagnostics.of(findings)


def _identity_mismatch(path: str, message: str) -> Diagnostic:
    return Diagnostic(
        path,
        ProductDiagnosticCode.PACKAGE_IDENTITY_MISMATCH,
        Severity.ERROR,
        message,
        "rebuild the package; its identity chain is inconsistent",
    )
