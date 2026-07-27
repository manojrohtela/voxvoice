"""ProductDiagnostics (M7.0B-1) — a deterministic, read-only finding set.

Unlike the runtime aggregators elsewhere (e.g. `workflow.diagnostics`), Product
diagnostics are *configuration* diagnostics: a set of structured findings produced
by validation, each addressable (a `path` into the manifest), coded, and carrying a
remediation hint. Findings are ordered deterministically (by path, then code, then
severity) so identical input always yields identical output — the reports are
diffable and certifiable. No runtime diagnostics here (§Diagnostics of the M7.0A
design). Immutable; no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class ProductDiagnosticCode(StrEnum):
    """The Product Runtime diagnostic vocabulary. Grows additively per bundle;
    every finding — from manifest validation (M7.0B-1) through compilation
    (M7.0B-2) — draws from this one closed set so reports are uniform and
    diffable. Deployment/packaging/registry codes belong to later bundles."""

    # ── manifest definition & validation (M7.0B-1) ────────────────────────────
    MISSING_REQUIRED_FIELD = "missing_required_field"
    EMPTY_IDENTITY = "empty_identity"
    UNSUPPORTED_INTERFACE_VERSION = "unsupported_interface_version"
    INVALID_VERSION_FORMAT = "invalid_version_format"
    DUPLICATE_ID = "duplicate_id"
    INVALID_METADATA = "invalid_metadata"
    MALFORMED_REFERENCE = "malformed_reference"
    SECRET_VALUE_PRESENT = "secret_value_present"
    INVALID_FEATURE_FLAG = "invalid_feature_flag"
    EMPTY_ENVIRONMENT_NAME = "empty_environment_name"
    EMPTY_TENANT_ID = "empty_tenant_id"

    # ── compilation & binding resolution (M7.0B-2) ────────────────────────────
    MISSING_BINDING = "missing_binding"
    DUPLICATE_BINDING = "duplicate_binding"
    MISSING_REFERENCE = "missing_reference"
    CIRCULAR_DEPENDENCY = "circular_dependency"
    UNREACHABLE_NODE = "unreachable_node"
    INVALID_DEPENDENCY_ORDER = "invalid_dependency_order"
    CONFIGURATION_CONFLICT = "configuration_conflict"
    ENVIRONMENT_NOT_FOUND = "environment_not_found"
    TENANT_NOT_FOUND = "tenant_not_found"
    INCOMPATIBLE_EXECUTION_PLATFORM = "incompatible_execution_platform"
    INCOMPATIBLE_RUNTIME_PLATFORM = "incompatible_runtime_platform"
    UNSUPPORTED_SCHEMA_VERSION = "unsupported_schema_version"
    INVALID_VERSION_CONSTRAINT = "invalid_version_constraint"
    OWNERSHIP_VIOLATION = "ownership_violation"

    # ── packaging, registry & versioning (M7.0B-3) ────────────────────────────
    PACKAGE_INTEGRITY_MISMATCH = "package_integrity_mismatch"
    PACKAGE_IDENTITY_MISMATCH = "package_identity_mismatch"
    INVALID_PACKAGE_VERSION = "invalid_package_version"
    MISSING_PACKAGE_METADATA = "missing_package_metadata"
    UNSUPPORTED_PACKAGE_SCHEMA = "unsupported_package_schema"
    DUPLICATE_PACKAGE = "duplicate_package"
    INCOMPATIBLE_ROLLBACK = "incompatible_rollback"
    UNCERTIFIED_PACKAGE = "uncertified_package"

    # ── deployment planning (M7.0C) ───────────────────────────────────────────
    INVALID_DEPLOYMENT_PROFILE = "invalid_deployment_profile"
    INVALID_TENANCY_TARGET = "invalid_tenancy_target"
    MISSING_DEPLOYMENT_TARGET = "missing_deployment_target"
    INCOMPATIBLE_DEPLOYMENT_ENVIRONMENT = "incompatible_deployment_environment"
    INVALID_ROLLBACK_POLICY = "invalid_rollback_policy"
    INVALID_ROLLBACK_TARGET = "invalid_rollback_target"
    MISSING_ROLLBACK_TARGET = "missing_rollback_target"
    UNVERIFIABLE_ROLLBACK_TARGET = "unverifiable_rollback_target"
    INVALID_RESOURCE_PROFILE = "invalid_resource_profile"
    UNKNOWN_FEATURE_FLAG = "unknown_feature_flag"
    INCONSISTENT_TENANT_OVERRIDE = "inconsistent_tenant_override"
    INVALID_ENVIRONMENT_OVERRIDE = "invalid_environment_override"
    DEPLOYMENT_POLICY_VIOLATION = "deployment_policy_violation"


@dataclass(frozen=True, slots=True, order=True)
class Diagnostic:
    """One finding. `order=True` on (path, code, severity) gives deterministic
    sorting for free; `message`/`remediation` are excluded from ordering so
    wording changes never reorder a report."""

    path: str
    code: ProductDiagnosticCode
    severity: Severity
    message: str = field(compare=False)
    remediation: str = field(default="", compare=False)


@dataclass(frozen=True, slots=True)
class ProductDiagnostics:
    """An immutable, deterministically-ordered collection of findings."""

    findings: tuple[Diagnostic, ...] = ()

    @classmethod
    def of(cls, findings: Iterable[Diagnostic]) -> ProductDiagnostics:
        """Build a diagnostics set with findings sorted into canonical order."""
        return cls(tuple(sorted(findings)))

    @property
    def errors(self) -> tuple[Diagnostic, ...]:
        return tuple(f for f in self.findings if f.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[Diagnostic, ...]:
        return tuple(f for f in self.findings if f.severity is Severity.WARNING)

    @property
    def has_errors(self) -> bool:
        return any(f.severity is Severity.ERROR for f in self.findings)

    @property
    def is_valid(self) -> bool:
        return not self.has_errors

    def summary(self) -> str:
        """A one-line, deterministic summary suitable for an exception message."""
        if not self.findings:
            return "product manifest valid: 0 findings"
        parts = [f"{len(self.errors)} error(s)", f"{len(self.warnings)} warning(s)"]
        first = self.errors[0] if self.errors else self.findings[0]
        return (
            f"product manifest invalid: {', '.join(parts)}; "
            f"first: {first.path} {first.code.value}"
        )
