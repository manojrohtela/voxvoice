"""PackageSerializer (M7.0B-3) — deterministic, byte-identical package serialization.

Renders a `ProductPackage` (and its content payload) to plain dicts in canonical
order, and derives the package content hash over the canonical content payload. The
content payload deliberately EXCLUDES identity, integrity, and provenance
(timestamps/creator/signature) so the package hash — and therefore the package
identity — is reproducible from the product content alone. Stateless; no I/O.
"""

from __future__ import annotations

from typing import Any

from product.compiler.serializer import (
    canonical_hash,
    compiled_to_dict,
    report_to_dict,
)
from product.serializer import ProductSerializer

from .package import PackageManifest, ProductPackage

SCHEMA_VERSION = "1"

_PRODUCT_SERIALIZER = ProductSerializer()


def content_payload(
    product_manifest_dict: dict[str, Any],
    compiled_dict: dict[str, Any],
    report_dict: dict[str, Any],
    package_version: str,
) -> dict[str, Any]:
    """The canonical content payload the package hash is computed over — product
    content + package version only, never identity/integrity/provenance."""
    return {
        "schema_version": SCHEMA_VERSION,
        "package_version": package_version,
        "product_manifest": product_manifest_dict,
        "compiled_product": compiled_dict,
        "compilation_report": report_dict,
    }


def package_hash_for(
    product_manifest_dict: dict[str, Any],
    compiled_dict: dict[str, Any],
    report_dict: dict[str, Any],
    package_version: str,
) -> str:
    return canonical_hash(
        content_payload(product_manifest_dict, compiled_dict, report_dict, package_version)
    )


def _identity_to_dict(pm: PackageManifest) -> dict[str, Any]:
    i = pm.identity
    return {
        "manifest_id": i.manifest_id,
        "definition_id": i.definition_id,
        "compilation_id": i.compilation_id,
        "package_id": i.package_id,
    }


def _package_manifest_to_dict(pm: PackageManifest) -> dict[str, Any]:
    integ = pm.integrity
    prov = pm.provenance
    sig = pm.signature
    cm = pm.compatibility_matrix
    return {
        "identity": _identity_to_dict(pm),
        "package_version": pm.package_version,
        "product_version": pm.product_version,
        "manifest_interface_version": pm.manifest_interface_version,
        "schema_version": pm.schema_version,
        "compiler_version": pm.compiler_version,
        "created_at": pm.created_at,
        "creator": pm.creator,
        "certified": pm.certified,
        "integrity": {
            "package_hash": integ.package_hash,
            "manifest_hash": integ.manifest_hash,
            "compilation_hash": integ.compilation_hash,
            "dependency_hash": integ.dependency_hash,
        },
        "provenance": {
            "creator": prov.creator,
            "compiler_version": prov.compiler_version,
            "binding_snapshot_id": prov.binding_snapshot_id,
            "manifest_id": prov.manifest_id,
            "definition_id": prov.definition_id,
            "compilation_id": prov.compilation_id,
            "package_id": prov.package_id,
            "created_at": prov.created_at,
        },
        "signature": {
            "signed": sig.signed,
            "algorithm": sig.algorithm,
            "key_id": sig.key_id,
            "signature": sig.signature,
            "signed_at": sig.signed_at,
        },
        "compatibility_matrix": {
            "package_version": cm.package_version,
            "product_version": cm.product_version,
            "manifest_interface_version": cm.manifest_interface_version,
            "schema_version": cm.schema_version,
            "compiler_version": cm.compiler_version,
            "requires_execution": cm.requires_execution,
            "requires_runtime": cm.requires_runtime,
        },
    }


class PackageSerializer:
    """Stateless, deterministic (de)serializer surface for packages."""

    def product_manifest_dict(self, package: ProductPackage) -> dict[str, Any]:
        return _PRODUCT_SERIALIZER.manifest_to_dict(package.product_manifest)

    def package_to_dict(self, package: ProductPackage) -> dict[str, Any]:
        """The full machine-readable form of a package (identity + integrity +
        provenance + content). Deterministic and byte-identical for identical
        packages."""
        return {
            "schema_version": SCHEMA_VERSION,
            "content_id": package.content_id,
            "content_hash": package.content_hash,
            "package_manifest": _package_manifest_to_dict(package.package_manifest),
            "product_manifest": self.product_manifest_dict(package),
            "compiled_product": compiled_to_dict(package.compiled_product),
            "compilation_report": report_to_dict(package.compilation_report),
        }
