"""Typed Product Runtime errors (M7.0B-1).

Rooted in `core.errors.PlatformError` per CODING_STANDARDS §3 (one typed
hierarchy, no bare/catch-all). This bundle raises exactly one condition —
loading a manifest that fails validation — carrying the deterministic
`ProductDiagnostics` so the caller sees *why*, not just *that*, it failed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.errors import ConfigError

if TYPE_CHECKING:
    from .diagnostics import ProductDiagnostics


class ProductError(ConfigError):
    """Root of Product Runtime errors. A `ConfigError` because an invalid manifest
    is an invalid configuration (fail fast, do not run degraded)."""


class ProductValidationError(ProductError):
    """A manifest failed structural validation. Carries the full diagnostics."""

    def __init__(self, diagnostics: ProductDiagnostics) -> None:
        self.diagnostics = diagnostics
        super().__init__(diagnostics.summary())
