"""Hook manifest structural validation (M6.3B) — hand-rolled, dependency-free
(mirrors `skill/validation.py`). Fail-fast at registration time. `category`
is deliberately never inspected beyond being a well-formed enum member —
validation never gates on it affecting behavior, since it is descriptive
metadata only.
"""

from __future__ import annotations

from .domain import HookManifest

SUPPORTED_INTERFACE_VERSIONS: frozenset[str] = frozenset({"1.0"})


def validate_manifest(manifest: HookManifest) -> tuple[bool, list[str]]:
    """Structural validation only — well-formed identity, a supported
    `interface_version`, at least one declared `HookPoint`, and well-formed
    capability-requirement entries. Never validates whether a required
    context field is actually present at run time (capability resolution is
    out of scope, § Constraints)."""
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
    if not manifest.points:
        errors.append("a hook must declare at least one HookPoint")
    if not manifest.scope:
        errors.append("a hook must declare at least one HookRuntimeScope")

    for req in manifest.required_context:
        if not req.field:
            errors.append("required_context entry has an empty field name")

    return (not errors, errors)
