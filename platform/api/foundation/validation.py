"""Stateless structural validation for foundation inputs (M8.2).

Hand-rolled and dependency-free (mirrors `product.validation`). `validate_*` methods
never raise — they return `FoundationDiagnostics`; services decide whether to raise
(fail-fast) on error-severity findings. Structural only: format/shape/scope-consistency.
Uniqueness and existence checks that need the repositories live in the services (they
are not structural properties of a single input).

Deterministic: identical input always yields the identical, canonically-ordered finding
set.
"""

from __future__ import annotations

import re

from .config import FeatureFlag
from .diagnostics import Diagnostic, FoundationDiagnosticCode, FoundationDiagnostics, Severity
from .domain import Role, RoleScope

# A pragmatic, dependency-free email shape check (not full RFC 5322 — that is a
# lint, not a gate; the real check is a verification email later).
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Slugs: lowercase alphanumerics and single hyphens, 1-63 chars.
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL.match(email))


def is_valid_slug(slug: str) -> bool:
    return bool(_SLUG.match(slug)) and len(slug) <= 63


class FoundationValidator:
    """Stateless structural validator. Each method returns a diagnostics set."""

    def validate_user(self, *, email: str, display_name: str) -> FoundationDiagnostics:
        out: list[Diagnostic] = []
        if not email:
            out.append(
                Diagnostic(
                    "email",
                    FoundationDiagnosticCode.MISSING_REQUIRED_FIELD,
                    Severity.ERROR,
                    "email must be non-empty",
                    "supply the user's email address",
                )
            )
        elif not is_valid_email(email):
            out.append(
                Diagnostic(
                    "email",
                    FoundationDiagnosticCode.INVALID_EMAIL,
                    Severity.ERROR,
                    f"email {email!r} is not a valid address",
                    "use a well-formed email (name@domain.tld)",
                )
            )
        if not display_name:
            out.append(
                Diagnostic(
                    "display_name",
                    FoundationDiagnosticCode.INVALID_NAME,
                    Severity.WARNING,
                    "display_name is empty",
                    "set a human-readable display name",
                )
            )
        return FoundationDiagnostics.of(out)

    def validate_organization(self, *, name: str) -> FoundationDiagnostics:
        out: list[Diagnostic] = []
        if not name.strip():
            out.append(
                Diagnostic(
                    "name",
                    FoundationDiagnosticCode.MISSING_REQUIRED_FIELD,
                    Severity.ERROR,
                    "organization name must be non-empty",
                    "give the organization a name",
                )
            )
        return FoundationDiagnostics.of(out)

    def validate_workspace(self, *, name: str, slug: str) -> FoundationDiagnostics:
        return self._name_and_slug("workspace", name, slug)

    def validate_project(self, *, name: str, slug: str) -> FoundationDiagnostics:
        return self._name_and_slug("project", name, slug)

    def validate_role_binding(
        self, *, role: Role, scope: RoleScope, scope_id: str
    ) -> FoundationDiagnostics:
        out: list[Diagnostic] = []
        if not scope_id:
            out.append(
                Diagnostic(
                    "scope_id",
                    FoundationDiagnosticCode.MISSING_SCOPE_TARGET,
                    Severity.ERROR,
                    "a role binding must name a scope target (org id or workspace id)",
                    "set scope_id to the org id (ORGANIZATION) or workspace id (WORKSPACE)",
                )
            )
        # A workspace-only role must not be granted org-wide, and vice-versa is allowed
        # (org owners legitimately span workspaces). We only flag the nonsensical case
        # of a role that carries no permissions at all.
        from .rbac import permissions_for  # local import avoids a module cycle

        if not permissions_for(role):
            out.append(
                Diagnostic(
                    "role",
                    FoundationDiagnosticCode.INVALID_ROLE_SCOPE,
                    Severity.ERROR,
                    f"role {role.value!r} grants no permissions",
                    "grant a role that is present in ROLE_PERMISSIONS",
                )
            )
        return FoundationDiagnostics.of(out)

    def validate_feature_flag(self, flag: FeatureFlag) -> FoundationDiagnostics:
        out: list[Diagnostic] = []
        if not flag.key:
            out.append(
                Diagnostic(
                    "key",
                    FoundationDiagnosticCode.INVALID_FEATURE_FLAG,
                    Severity.ERROR,
                    "feature flag key must be non-empty",
                    "give the flag a stable key",
                )
            )
        if not flag.scope_id:
            out.append(
                Diagnostic(
                    "scope_id",
                    FoundationDiagnosticCode.MISSING_SCOPE_TARGET,
                    Severity.ERROR,
                    "feature flag must name its scope target",
                    "set scope_id to the org or workspace id",
                )
            )
        return FoundationDiagnostics.of(out)

    def _name_and_slug(self, kind: str, name: str, slug: str) -> FoundationDiagnostics:
        out: list[Diagnostic] = []
        if not name.strip():
            out.append(
                Diagnostic(
                    "name",
                    FoundationDiagnosticCode.MISSING_REQUIRED_FIELD,
                    Severity.ERROR,
                    f"{kind} name must be non-empty",
                    f"give the {kind} a name",
                )
            )
        if not slug:
            out.append(
                Diagnostic(
                    "slug",
                    FoundationDiagnosticCode.MISSING_REQUIRED_FIELD,
                    Severity.ERROR,
                    f"{kind} slug must be non-empty",
                    "supply a url-safe slug",
                )
            )
        elif not is_valid_slug(slug):
            out.append(
                Diagnostic(
                    "slug",
                    FoundationDiagnosticCode.INVALID_SLUG,
                    Severity.ERROR,
                    f"{kind} slug {slug!r} is not url-safe",
                    "use lowercase alphanumerics and single hyphens (max 63 chars)",
                )
            )
        return FoundationDiagnostics.of(out)
