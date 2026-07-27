"""VersionManager & CompatibilityMatrix (M7.0B-3) — static versioning only.

`CompatibilityMatrix` is the static compatibility surface of a package: the versions
it requires across Execution Platform / Runtime Platform / Manifest Schema / Compiler
/ its own package & product versions, plus a pure `evaluate(...)` that reports
compatibility against supplied actual versions. `VersionManager` classifies
transitions (upgrade/downgrade/same/invalid) and decides rollback admissibility, and
produces deterministic reports. No runtime probing — everything is a pure function of
declared versions (reuses `compiler.versions`).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from product.compiler.versions import is_compatible, version_sort_key
from product.validation import is_valid_semver


class TransitionKind(StrEnum):
    UPGRADE = "upgrade"
    DOWNGRADE = "downgrade"
    SAME = "same"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class CompatibilityMatrix:
    """The declared static compatibility surface of a package."""

    package_version: str
    product_version: str
    manifest_interface_version: str
    schema_version: str
    compiler_version: str
    requires_execution: str
    requires_runtime: str

    def evaluate(
        self, *, execution_platform_version: str, runtime_platform_version: str
    ) -> CompatibilityEvaluation:
        """Static compatibility against supplied actual platform versions."""
        execution_ok = is_compatible(self.requires_execution, execution_platform_version)
        runtime_ok = is_compatible(self.requires_runtime, runtime_platform_version)
        return CompatibilityEvaluation(
            execution_compatible=execution_ok,
            runtime_compatible=runtime_ok,
            compatible=execution_ok and runtime_ok,
            execution_platform_version=execution_platform_version,
            runtime_platform_version=runtime_platform_version,
        )


@dataclass(frozen=True, slots=True)
class CompatibilityEvaluation:
    compatible: bool
    execution_compatible: bool
    runtime_compatible: bool
    execution_platform_version: str
    runtime_platform_version: str


@dataclass(frozen=True, slots=True)
class VersionCompatibilityReport:
    from_version: str
    to_version: str
    transition: TransitionKind
    rollback_allowed: bool
    valid_versions: bool


class VersionManager:
    """Stateless. Pure version reasoning over declared SemVer strings."""

    def classify_transition(self, from_version: str, to_version: str) -> TransitionKind:
        if not (is_valid_semver(from_version) and is_valid_semver(to_version)):
            return TransitionKind.INVALID
        if from_version == to_version:
            return TransitionKind.SAME
        return (
            TransitionKind.UPGRADE
            if version_sort_key(to_version) > version_sort_key(from_version)
            else TransitionKind.DOWNGRADE
        )

    def is_rollback_allowed(self, from_version: str, to_version: str) -> bool:
        """A rollback is a downgrade that stays within the same major version — a
        cross-major downgrade is not a safe rollback (static rule)."""
        if self.classify_transition(from_version, to_version) is not TransitionKind.DOWNGRADE:
            return False
        return from_version.split(".")[0] == to_version.split(".")[0]

    def report(self, from_version: str, to_version: str) -> VersionCompatibilityReport:
        transition = self.classify_transition(from_version, to_version)
        return VersionCompatibilityReport(
            from_version=from_version,
            to_version=to_version,
            transition=transition,
            rollback_allowed=self.is_rollback_allowed(from_version, to_version),
            valid_versions=transition is not TransitionKind.INVALID,
        )
