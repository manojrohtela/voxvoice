"""FoundationDiagnostics (M8.2) — a deterministic, read-only finding set.

Mirrors `product.diagnostics` exactly (the established configuration-diagnostics
shape): each finding is addressable (a `path` into the input), coded, severity-typed,
and carries a remediation hint. Findings are ordered deterministically (by path, then
code, then severity) so identical input always yields identical output — reports are
diffable and certifiable. Immutable; no I/O.

Validation produces these; the services decide whether to raise on errors (fail-fast,
Constitution §A2.6). Same pattern as `product`/`skill`/`workflow`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class FoundationDiagnosticCode(StrEnum):
    """The Customer Platform Foundation diagnostic vocabulary (M8.2). One closed set
    so reports are uniform and diffable; grows additively per later Epic 8 milestone."""

    MISSING_REQUIRED_FIELD = "missing_required_field"
    EMPTY_IDENTITY = "empty_identity"
    INVALID_EMAIL = "invalid_email"
    INVALID_SLUG = "invalid_slug"
    INVALID_NAME = "invalid_name"
    DUPLICATE_ID = "duplicate_id"
    DUPLICATE_SLUG = "duplicate_slug"
    DUPLICATE_MEMBERSHIP = "duplicate_membership"
    UNKNOWN_ORGANIZATION = "unknown_organization"
    UNKNOWN_WORKSPACE = "unknown_workspace"
    UNKNOWN_USER = "unknown_user"
    CROSS_ORG_REFERENCE = "cross_org_reference"
    INVALID_ROLE_SCOPE = "invalid_role_scope"
    MISSING_SCOPE_TARGET = "missing_scope_target"
    INVALID_ENVIRONMENT_KIND = "invalid_environment_kind"
    INVALID_FEATURE_FLAG = "invalid_feature_flag"
    INVALID_SETTING = "invalid_setting"
    # M8.10 — agent builder validation (additive).
    DUPLICATE_NAME = "duplicate_name"
    PROMPT_TOO_LARGE = "prompt_too_large"
    INVALID_VARIABLE_REFERENCE = "invalid_variable_reference"
    INVALID_MODEL_CONFIG = "invalid_model_config"
    INVALID_VOICE_CONFIG = "invalid_voice_config"
    # M8.11 — tool framework validation (additive).
    INVALID_TOOL_CONFIG = "invalid_tool_config"
    INVALID_TOOL_CATEGORY = "invalid_tool_category"
    CIRCULAR_TOOL_REFERENCE = "circular_tool_reference"
    # M8.15 — knowledge tool validation (additive).
    UNSUPPORTED_MIME = "unsupported_mime"
    DOCUMENT_TOO_LARGE = "document_too_large"
    DUPLICATE_DOCUMENT = "duplicate_document"


@dataclass(frozen=True, slots=True, order=True)
class Diagnostic:
    """One finding. `order=True` on (path, code, severity) gives deterministic
    sorting for free; `message`/`remediation` are excluded from ordering so wording
    changes never reorder a report."""

    path: str
    code: FoundationDiagnosticCode
    severity: Severity
    message: str = field(compare=False)
    remediation: str = field(default="", compare=False)


@dataclass(frozen=True, slots=True)
class FoundationDiagnostics:
    """An immutable, deterministically-ordered collection of findings."""

    findings: tuple[Diagnostic, ...] = ()

    @classmethod
    def of(cls, findings: Iterable[Diagnostic]) -> FoundationDiagnostics:
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
            return "foundation input valid: 0 findings"
        parts = [f"{len(self.errors)} error(s)", f"{len(self.warnings)} warning(s)"]
        first = self.errors[0] if self.errors else self.findings[0]
        return (
            f"foundation input invalid: {', '.join(parts)}; "
            f"first: {first.path} {first.code.value}"
        )
