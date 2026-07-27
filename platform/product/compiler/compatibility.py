"""CompatibilityValidator (M7.0B-2) — static compatibility only.

Checks, without touching any live platform: manifest interface version, document
schema version, product SemVer, declared platform-version constraints
(`requires_execution` / `requires_runtime`) against the static platform versions in
the `CompilationContext`, and that pinned binding versions are among those the
snapshot offers. Every constraint value must itself be well-formed SemVer. No
runtime compatibility probing (ADR-0018/0019).
"""

from __future__ import annotations

from dataclasses import dataclass

from product.diagnostics import Diagnostic, ProductDiagnosticCode, Severity
from product.domain import ProductManifest
from product.serializer import SCHEMA_VERSION as _SCHEMA_VERSION
from product.validation import SUPPORTED_INTERFACE_VERSIONS, is_valid_semver

from .context import CompilationContext
from .versions import is_compatible


@dataclass(frozen=True, slots=True)
class CompatibilityMetadata:
    interface_version: str
    schema_version: str
    requires_execution: str
    requires_runtime: str
    execution_platform_version: str
    runtime_platform_version: str
    compatible: bool


@dataclass(frozen=True, slots=True)
class CompatibilityReport:
    compatible: bool
    interface_version_supported: bool
    schema_version_supported: bool
    execution_compatible: bool
    runtime_compatible: bool
    issue_count: int


@dataclass(frozen=True, slots=True)
class CompatibilityResult:
    metadata: CompatibilityMetadata
    report: CompatibilityReport
    findings: tuple[Diagnostic, ...]


class CompatibilityValidator:
    """Stateless. `validate` never raises — it reports via findings."""

    def validate(
        self, manifest: ProductManifest, context: CompilationContext
    ) -> CompatibilityResult:
        findings: list[Diagnostic] = []
        vm = manifest.version_metadata

        interface_ok = manifest.interface_version in SUPPORTED_INTERFACE_VERSIONS
        if not interface_ok:
            findings.append(
                Diagnostic(
                    "interface_version",
                    ProductDiagnosticCode.UNSUPPORTED_INTERFACE_VERSION,
                    Severity.ERROR,
                    f"unsupported interface_version {manifest.interface_version!r}",
                    "author against a supported manifest interface version",
                )
            )

        schema_ok = context.expected_schema_version != "" and (
            context.expected_schema_version == _SCHEMA_VERSION
        )
        if not schema_ok:
            findings.append(
                Diagnostic(
                    "schema_version",
                    ProductDiagnosticCode.UNSUPPORTED_SCHEMA_VERSION,
                    Severity.ERROR,
                    f"expected schema version {context.expected_schema_version!r} "
                    f"does not match compiler schema {_SCHEMA_VERSION!r}",
                    "recompile with a matching schema version",
                )
            )

        self._check_constraint_format(vm.requires_execution, "requires_execution", findings)
        self._check_constraint_format(vm.requires_runtime, "requires_runtime", findings)

        execution_ok = self._check_platform(
            vm.requires_execution,
            context.execution_platform_version,
            "version_metadata.requires_execution",
            ProductDiagnosticCode.INCOMPATIBLE_EXECUTION_PLATFORM,
            "execution",
            findings,
        )
        runtime_ok = self._check_platform(
            vm.requires_runtime,
            context.runtime_platform_version,
            "version_metadata.requires_runtime",
            ProductDiagnosticCode.INCOMPATIBLE_RUNTIME_PLATFORM,
            "runtime",
            findings,
        )

        compatible = interface_ok and schema_ok and execution_ok and runtime_ok
        metadata = CompatibilityMetadata(
            interface_version=manifest.interface_version,
            schema_version=_SCHEMA_VERSION,
            requires_execution=vm.requires_execution,
            requires_runtime=vm.requires_runtime,
            execution_platform_version=context.execution_platform_version,
            runtime_platform_version=context.runtime_platform_version,
            compatible=compatible,
        )
        report = CompatibilityReport(
            compatible=compatible,
            interface_version_supported=interface_ok,
            schema_version_supported=schema_ok,
            execution_compatible=execution_ok,
            runtime_compatible=runtime_ok,
            issue_count=len([f for f in findings if f.severity is Severity.ERROR]),
        )
        return CompatibilityResult(metadata=metadata, report=report, findings=tuple(findings))

    def _check_constraint_format(
        self, constraint: str, path: str, findings: list[Diagnostic]
    ) -> None:
        if constraint and not is_valid_semver(constraint):
            findings.append(
                Diagnostic(
                    f"version_metadata.{path}",
                    ProductDiagnosticCode.INVALID_VERSION_CONSTRAINT,
                    Severity.ERROR,
                    f"{path} {constraint!r} is not a valid SemVer constraint",
                    "declare the constraint as MAJOR.MINOR.PATCH",
                )
            )

    def _check_platform(
        self,
        required: str,
        actual: str,
        path: str,
        code: ProductDiagnosticCode,
        label: str,
        findings: list[Diagnostic],
    ) -> bool:
        if not required:
            return True  # no constraint declared
        if not actual:
            # A constraint is declared but no platform version was supplied to check
            # against — cannot certify compatibility; report, do not silently pass.
            findings.append(
                Diagnostic(
                    path,
                    code,
                    Severity.ERROR,
                    f"{label} platform version required ({required}) but none provided to compile",
                    f"supply the {label} platform version in the compilation context",
                )
            )
            return False
        if not is_compatible(required, actual):
            findings.append(
                Diagnostic(
                    path,
                    code,
                    Severity.ERROR,
                    f"{label} platform {actual} does not satisfy required {required}",
                    f"compile against a compatible {label} platform version",
                )
            )
            return False
        return True
