"""Skill manifest structural validation (M6.2B-1) — hand-rolled, dependency-free
(mirrors `tool/validation.py`'s "no external SDK" constraint). Fail-fast at
registration time; no execution-time argument validation exists here since
Skills never execute.
"""

from __future__ import annotations

from .domain import SkillManifest

SUPPORTED_INTERFACE_VERSIONS: frozenset[str] = frozenset({"1.0"})


def validate_manifest(manifest: SkillManifest) -> tuple[bool, list[str]]:
    """Structural validation only — well-formed identity, a supported
    `interface_version`, and well-formed capability/dependency entries.
    Never validates whether a declared capability is actually satisfiable
    (that is never Skill Runtime's job — §8 of the M6.2A design)."""
    errors: list[str] = []

    if not manifest.id:
        errors.append("id must be non-empty")
    if not manifest.namespace:
        errors.append("namespace must be non-empty")
    if not manifest.version:
        errors.append("version must be non-empty")
    if manifest.interface_version not in SUPPORTED_INTERFACE_VERSIONS:
        errors.append(
            f"unsupported interface_version {manifest.interface_version!r}; "
            f"supported: {sorted(SUPPORTED_INTERFACE_VERSIONS)}"
        )

    for req in manifest.required_capabilities:
        if not req.tag:
            errors.append(f"capability requirement of kind {req.kind.value!r} has an empty tag")

    for dep in manifest.dependencies:
        if not dep.namespace or not dep.id:
            errors.append(f"dependency {dep!r} must declare a non-empty namespace and id")

    for res in manifest.resources:
        if not res.uri:
            errors.append("resource must declare a non-empty uri")

    return (not errors, errors)
