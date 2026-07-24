"""Agent manifest structural validation (M6.5B-1) — hand-rolled,
dependency-free (mirrors `skill/validation.py`, `hooks/validation.py`,
`workflow/validation.py`). Fail-fast at registration time. Never resolves
`AgentCapability` requirements — that is `AgentCapabilityResolver`'s job
(Bundle 2), never this bundle's.
"""

from __future__ import annotations

from .domain import AgentManifest

SUPPORTED_INTERFACE_VERSIONS: frozenset[str] = frozenset({"1.0"})


def validate_manifest(manifest: AgentManifest) -> tuple[bool, list[str]]:
    """Structural validation only — well-formed identity, a supported
    `interface_version`, and structurally well-formed capability
    requirements. Never resolves a capability, never executes anything."""
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

    for cap in manifest.capabilities:
        if not cap.kind:
            errors.append("a capability's kind must be non-empty")
        if not cap.tag:
            errors.append("a capability's tag must be non-empty")

    return (not errors, errors)
