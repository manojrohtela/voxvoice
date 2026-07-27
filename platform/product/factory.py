"""ProductFactory — the authoring entry point (M7.0B-1).

The one place a `ProductDefinition` is constructed. Responsibilities, and nothing
more (per the M7.0A design): load a manifest (from an authored mapping or a
`ProductManifest`), parse it, validate it, canonicalize it, and construct the
immutable `ProductDefinition` with its deterministic content identity. It does NOT
compile, resolve references/bindings, package, deploy, or register — those are
later Epic 7 bundles.

Fail-fast: a manifest with validation *errors* raises `ProductValidationError`
carrying the full diagnostics (warnings never block). Callers that want the
diagnostics without raising use `ProductValidator().validate(...)` directly, or
`ProductFactory.diagnose(...)`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .diagnostics import ProductDiagnostics
from .domain import ProductDefinition, ProductManifest
from .errors import ProductValidationError
from .ids import definition_id_for, manifest_hash
from .serializer import ProductSerializer
from .validation import ProductValidator


class ProductFactory:
    @staticmethod
    def from_manifest(manifest: ProductManifest) -> ProductDefinition:
        """Validate + canonicalize + construct. Raises `ProductValidationError`
        if the manifest has any error-severity finding."""
        diagnostics = ProductValidator().validate(manifest)
        if diagnostics.has_errors:
            raise ProductValidationError(diagnostics)
        return ProductDefinition(
            manifest=manifest,
            content_id=definition_id_for(manifest),
            content_hash=manifest_hash(manifest),
        )

    @staticmethod
    def from_mapping(data: Mapping[str, Any]) -> ProductDefinition:
        """Parse an authored mapping into a manifest, then build the definition."""
        manifest = ProductSerializer().manifest_from_dict(data)
        return ProductFactory.from_manifest(manifest)

    @staticmethod
    def parse(data: Mapping[str, Any]) -> ProductManifest:
        """Parse an authored mapping into a `ProductManifest` without validating —
        the seam a caller uses when it wants to inspect or diagnose before
        committing to construction."""
        return ProductSerializer().manifest_from_dict(data)

    @staticmethod
    def diagnose(manifest: ProductManifest) -> ProductDiagnostics:
        """Validate without constructing or raising — returns the diagnostics."""
        return ProductValidator().validate(manifest)
