"""PackageIdentity (M7.0B-3) — the deterministic package identity chain.

Identity chain (each derived from the one above, all content-derived, reproducible):

    Manifest ID  →  Definition ID  →  Compilation ID  →  Package ID

`package_id` changes whenever package contents or the package version change, and is
reproducible from identical inputs (no timestamps enter the identity — provenance
timestamps are descriptive, never part of identity). Mirrors the `product.ids`
content-hash pattern.
"""

from __future__ import annotations

from dataclasses import dataclass

from product.ids import content_hash


@dataclass(frozen=True, slots=True)
class PackageIdentity:
    """The full, immutable identity chain of one package."""

    manifest_id: str
    definition_id: str
    compilation_id: str
    package_id: str


def manifest_id_for(manifest_hash: str) -> str:
    """The Manifest ID — derived from the manifest content hash."""
    return "mf_" + content_hash(manifest_hash)[:16]


def package_id_for(
    manifest_id: str,
    definition_id: str,
    compilation_id: str,
    package_version: str,
    package_hash: str,
) -> str:
    """The Package ID — a content identity over the whole chain + version + the
    package content hash, so it changes whenever any of them change and is
    reproducible from identical inputs."""
    return "pk_" + content_hash(
        manifest_id, definition_id, compilation_id, package_version, package_hash
    )[:16]
