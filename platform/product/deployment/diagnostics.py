"""DeploymentDiagnostics (M7.0C) — a categorized view over the shared finding set.

Reuses the deterministic `product.diagnostics.ProductDiagnostics` container (one
diagnostics machinery across Product Runtime) and adds deployment-specific
*category* accessors the milestone calls for — policy violations, compatibility
issues, missing targets, invalid rollback targets, invalid environment overrides.
Immutable; deterministic; machine-readable via the serializer.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from product.diagnostics import Diagnostic, ProductDiagnosticCode, ProductDiagnostics

_POLICY = {ProductDiagnosticCode.DEPLOYMENT_POLICY_VIOLATION}
_COMPATIBILITY = {
    ProductDiagnosticCode.INCOMPATIBLE_DEPLOYMENT_ENVIRONMENT,
    ProductDiagnosticCode.INCOMPATIBLE_EXECUTION_PLATFORM,
    ProductDiagnosticCode.INCOMPATIBLE_RUNTIME_PLATFORM,
}
_MISSING_TARGETS = {
    ProductDiagnosticCode.MISSING_DEPLOYMENT_TARGET,
    ProductDiagnosticCode.MISSING_ROLLBACK_TARGET,
}
_INVALID_ROLLBACK = {
    ProductDiagnosticCode.INVALID_ROLLBACK_POLICY,
    ProductDiagnosticCode.INVALID_ROLLBACK_TARGET,
    ProductDiagnosticCode.UNVERIFIABLE_ROLLBACK_TARGET,
}
_INVALID_ENV_OVERRIDES = {ProductDiagnosticCode.INVALID_ENVIRONMENT_OVERRIDE}


@dataclass(frozen=True, slots=True)
class DeploymentDiagnostics:
    """A deterministic, categorized deployment finding set."""

    diagnostics: ProductDiagnostics = field(default_factory=ProductDiagnostics)

    @classmethod
    def of(cls, findings: Iterable[Diagnostic]) -> DeploymentDiagnostics:
        return cls(ProductDiagnostics.of(findings))

    # ── delegated severity view ────────────────────────────────────────────────
    @property
    def findings(self) -> tuple[Diagnostic, ...]:
        return self.diagnostics.findings

    @property
    def errors(self) -> tuple[Diagnostic, ...]:
        return self.diagnostics.errors

    @property
    def warnings(self) -> tuple[Diagnostic, ...]:
        return self.diagnostics.warnings

    @property
    def has_errors(self) -> bool:
        return self.diagnostics.has_errors

    @property
    def is_valid(self) -> bool:
        return self.diagnostics.is_valid

    # ── deployment categories ──────────────────────────────────────────────────
    def _by(self, codes: set[ProductDiagnosticCode]) -> tuple[Diagnostic, ...]:
        return tuple(f for f in self.findings if f.code in codes)

    @property
    def policy_violations(self) -> tuple[Diagnostic, ...]:
        return self._by(_POLICY)

    @property
    def compatibility_issues(self) -> tuple[Diagnostic, ...]:
        return self._by(_COMPATIBILITY)

    @property
    def missing_targets(self) -> tuple[Diagnostic, ...]:
        return self._by(_MISSING_TARGETS)

    @property
    def invalid_rollback_targets(self) -> tuple[Diagnostic, ...]:
        return self._by(_INVALID_ROLLBACK)

    @property
    def invalid_environment_overrides(self) -> tuple[Diagnostic, ...]:
        return self._by(_INVALID_ENV_OVERRIDES)
