"""Product manifest structural validation (M7.0B-1) — hand-rolled, dependency-free
(mirrors `skill/validation.py`'s "no external SDK" constraint). Fail-fast at load
time; structural only.

This bundle validates *shape and integrity* — NOT dependency resolution, NOT
compatibility resolution, NOT deployment feasibility, NOT secret resolution (all
later bundles). Every problem is reported as a `Diagnostic` with a manifest `path`,
a code, and a remediation hint; the set is returned deterministically ordered.
"""

from __future__ import annotations

import re

from .diagnostics import Diagnostic, ProductDiagnosticCode, ProductDiagnostics, Severity
from .domain import ProductManifest

SUPPORTED_INTERFACE_VERSIONS: frozenset[str] = frozenset({"1.0"})

# MAJOR.MINOR.PATCH with optional -prerelease and +build (SemVer 2.0.0, simplified).
_SEMVER = re.compile(
    r"^\d+\.\d+\.\d+"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)

# Field names that must never appear inside an authored secret reference — a secret
# reference is a pointer, never a value (ADR-0018 §Decision 3; M7.0A §Security).
_FORBIDDEN_SECRET_FIELDS = frozenset({"value", "secret", "token", "password", "credential"})


def is_valid_semver(version: str) -> bool:
    return bool(_SEMVER.match(version))


class ProductValidator:
    """Stateless structural validator. `validate` never raises — it returns a
    `ProductDiagnostics`; the factory decides whether to raise on errors."""

    def validate(self, manifest: ProductManifest) -> ProductDiagnostics:
        findings: list[Diagnostic] = []
        self._validate_shape(manifest, findings)
        self._validate_metadata(manifest, findings)
        self._validate_versions(manifest, findings)
        self._validate_duplicate_ids(manifest, findings)
        self._validate_references(manifest, findings)
        self._validate_secrets(manifest, findings)
        self._validate_config_layers(manifest, findings)
        return ProductDiagnostics.of(findings)

    # ── shape / interface ─────────────────────────────────────────────────────
    def _validate_shape(self, m: ProductManifest, out: list[Diagnostic]) -> None:
        if not m.namespace:
            out.append(
                Diagnostic(
                    "namespace",
                    ProductDiagnosticCode.MISSING_REQUIRED_FIELD,
                    Severity.ERROR,
                    "manifest namespace must be non-empty",
                    "set a namespace (e.g. 'internal' or 'org.<org_id>')",
                )
            )
        if m.interface_version not in SUPPORTED_INTERFACE_VERSIONS:
            out.append(
                Diagnostic(
                    "interface_version",
                    ProductDiagnosticCode.UNSUPPORTED_INTERFACE_VERSION,
                    Severity.ERROR,
                    f"unsupported interface_version {m.interface_version!r}; "
                    f"supported: {sorted(SUPPORTED_INTERFACE_VERSIONS)}",
                    "author against a supported manifest interface version",
                )
            )

    # ── metadata ──────────────────────────────────────────────────────────────
    def _validate_metadata(self, m: ProductManifest, out: list[Diagnostic]) -> None:
        md = m.metadata
        if not md.id:
            out.append(
                Diagnostic(
                    "metadata.id",
                    ProductDiagnosticCode.EMPTY_IDENTITY,
                    Severity.ERROR,
                    "metadata.id must be non-empty",
                    "give the product a stable, unique id",
                )
            )
        if not md.name:
            out.append(
                Diagnostic(
                    "metadata.name",
                    ProductDiagnosticCode.INVALID_METADATA,
                    Severity.WARNING,
                    "metadata.name is empty",
                    "set a human-readable product name",
                )
            )
        if not md.version:
            out.append(
                Diagnostic(
                    "metadata.version",
                    ProductDiagnosticCode.MISSING_REQUIRED_FIELD,
                    Severity.ERROR,
                    "metadata.version must be non-empty",
                    "set a semantic version (e.g. 1.0.0)",
                )
            )

    # ── version formats ───────────────────────────────────────────────────────
    def _validate_versions(self, m: ProductManifest, out: list[Diagnostic]) -> None:
        if m.metadata.version and not is_valid_semver(m.metadata.version):
            out.append(
                Diagnostic(
                    "metadata.version",
                    ProductDiagnosticCode.INVALID_VERSION_FORMAT,
                    Severity.ERROR,
                    f"metadata.version {m.metadata.version!r} is not valid SemVer",
                    "use MAJOR.MINOR.PATCH (SemVer 2.0.0)",
                )
            )
        # Pinned reference versions, where present, must be well-formed SemVer.
        for kind, refs in _ref_groups(m):
            for i, ref in enumerate(refs):
                if ref.version is not None and not is_valid_semver(ref.version):
                    out.append(
                        Diagnostic(
                            f"{kind}[{i}].version",
                            ProductDiagnosticCode.INVALID_VERSION_FORMAT,
                            Severity.ERROR,
                            f"{kind}[{i}] version {ref.version!r} is not valid SemVer",
                            "pin with MAJOR.MINOR.PATCH or omit for unpinned",
                        )
                    )

    # ── duplicate ids ─────────────────────────────────────────────────────────
    def _validate_duplicate_ids(self, m: ProductManifest, out: list[Diagnostic]) -> None:
        # Within each reference group, (namespace, id, version) must be unique.
        for kind, refs in _ref_groups(m):
            seen: set[tuple[str, str, str | None]] = set()
            for i, ref in enumerate(refs):
                key = (ref.namespace, ref.id, ref.version)
                if key in seen:
                    out.append(
                        Diagnostic(
                            f"{kind}[{i}]",
                            ProductDiagnosticCode.DUPLICATE_ID,
                            Severity.ERROR,
                            f"duplicate {kind} reference {ref.namespace}.{ref.id}"
                            f"@{ref.version or 'unpinned'}",
                            "remove the duplicate or pin distinct versions",
                        )
                    )
                seen.add(key)
        _validate_unique(
            [(f"environments[{i}]", e.name) for i, e in enumerate(m.environments)],
            "environment name",
            out,
        )
        _validate_unique(
            [(f"tenants[{i}]", t.tenant_id) for i, t in enumerate(m.tenants)],
            "tenant id",
            out,
        )
        _validate_unique(
            [(f"feature_flags[{i}]", f.key) for i, f in enumerate(m.feature_flags)],
            "feature flag key",
            out,
        )
        _validate_unique(
            [(f"secrets[{i}]", s.ref) for i, s in enumerate(m.secrets)],
            "secret ref",
            out,
        )

    # ── reference well-formedness ─────────────────────────────────────────────
    def _validate_references(self, m: ProductManifest, out: list[Diagnostic]) -> None:
        for kind, refs in _ref_groups(m):
            for i, ref in enumerate(refs):
                if not ref.id:
                    out.append(
                        Diagnostic(
                            f"{kind}[{i}].id",
                            ProductDiagnosticCode.MALFORMED_REFERENCE,
                            Severity.ERROR,
                            f"{kind}[{i}] reference has an empty id",
                            "every reference must declare a non-empty id",
                        )
                    )
                if not ref.namespace:
                    out.append(
                        Diagnostic(
                            f"{kind}[{i}].namespace",
                            ProductDiagnosticCode.MALFORMED_REFERENCE,
                            Severity.ERROR,
                            f"{kind}[{i}] reference has an empty namespace",
                            "every reference must declare a non-empty namespace",
                        )
                    )

    # ── secret integrity (pointer, never value) ───────────────────────────────
    def _validate_secrets(self, m: ProductManifest, out: list[Diagnostic]) -> None:
        for i, s in enumerate(m.secrets):
            if not s.ref or not s.provider or not s.path:
                out.append(
                    Diagnostic(
                        f"secrets[{i}]",
                        ProductDiagnosticCode.MALFORMED_REFERENCE,
                        Severity.ERROR,
                        f"secrets[{i}] must declare non-empty ref, provider, and path",
                        "a secret reference is a pointer (provider + path), never a value",
                    )
                )
            # Integrity: a secret must never carry an inline value in any field.
            for forbidden in _FORBIDDEN_SECRET_FIELDS:
                if forbidden in (s.path or "").lower() and "=" in (s.path or ""):
                    out.append(
                        Diagnostic(
                            f"secrets[{i}].path",
                            ProductDiagnosticCode.SECRET_VALUE_PRESENT,
                            Severity.ERROR,
                            f"secrets[{i}].path appears to inline a secret value",
                            "reference a secret by path only; never embed its value",
                        )
                    )
                    break

    # ── config layers ─────────────────────────────────────────────────────────
    def _validate_config_layers(self, m: ProductManifest, out: list[Diagnostic]) -> None:
        for i, e in enumerate(m.environments):
            if not e.name:
                out.append(
                    Diagnostic(
                        f"environments[{i}].name",
                        ProductDiagnosticCode.EMPTY_ENVIRONMENT_NAME,
                        Severity.ERROR,
                        f"environments[{i}] has an empty name",
                        "every environment profile needs a name",
                    )
                )
        for i, t in enumerate(m.tenants):
            if not t.tenant_id:
                out.append(
                    Diagnostic(
                        f"tenants[{i}].tenant_id",
                        ProductDiagnosticCode.EMPTY_TENANT_ID,
                        Severity.ERROR,
                        f"tenants[{i}] has an empty tenant_id",
                        "every tenant override needs a tenant_id",
                    )
                )
        for i, f in enumerate(m.feature_flags):
            if not f.key:
                out.append(
                    Diagnostic(
                        f"feature_flags[{i}].key",
                        ProductDiagnosticCode.INVALID_FEATURE_FLAG,
                        Severity.ERROR,
                        f"feature_flags[{i}] has an empty key",
                        "every feature flag needs a key",
                    )
                )


def _ref_groups(m: ProductManifest) -> list[tuple[str, tuple]]:
    """The reference collections keyed by their manifest field name (stable
    order, matching the manifest field declaration order)."""
    return [
        ("agents", m.agents),
        ("workflows", m.workflows),
        ("skills", m.skills),
        ("tools", m.tools),
        ("providers", m.providers),
        ("mcp_servers", m.mcp_servers),
        ("knowledge", m.knowledge),
    ]


def _validate_unique(
    entries: list[tuple[str, str]], label: str, out: list[Diagnostic]
) -> None:
    seen: set[str] = set()
    for path, key in entries:
        if key and key in seen:
            out.append(
                Diagnostic(
                    path,
                    ProductDiagnosticCode.DUPLICATE_ID,
                    Severity.ERROR,
                    f"duplicate {label} {key!r}",
                    f"{label} values must be unique within the manifest",
                )
            )
        seen.add(key)


def validate_manifest(manifest: ProductManifest) -> ProductDiagnostics:
    """Module-level convenience mirroring `skill.validate_manifest`."""
    return ProductValidator().validate(manifest)
