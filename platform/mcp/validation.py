"""Structural manifest validation (M6.8B-1) — pure, deterministic checks only.

Mirrors `skill.validation`/`tool.validation`: returns `(ok, errors)` and never
raises; the registry raises `ConfigError` on a failed manifest. No network, no
capability discovery (that is negotiation, done live against a transport).
"""

from __future__ import annotations

from .config import SUPPORTED_INTERFACE_VERSIONS
from .domain import MCPServerManifest, MCPTransportKind

_KNOWN_AUTH_MODES = frozenset({"none", "oauth2", "token", "mtls"})
_KNOWN_TRANSPORT_KINDS = frozenset(k.value for k in MCPTransportKind)


def validate_manifest(manifest: MCPServerManifest) -> tuple[bool, tuple[str, ...]]:
    """Structural validation of a server manifest. Deterministic; no I/O."""
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
    if manifest.transport.kind.value not in _KNOWN_TRANSPORT_KINDS:
        errors.append(f"unknown transport kind {manifest.transport.kind!r}")
    if manifest.auth_mode not in _KNOWN_AUTH_MODES:
        errors.append(
            f"unknown auth_mode {manifest.auth_mode!r}; known: {sorted(_KNOWN_AUTH_MODES)}"
        )
    return (not errors, tuple(errors))
