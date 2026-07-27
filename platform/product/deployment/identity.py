"""DeploymentIdentity (M7.0C) — extends the package identity chain by one link.

    Manifest ID → Definition ID → Compilation ID → Package ID → Deployment ID

`deployment_id` is derived from the package id plus the canonical hash of the
deployment descriptor, so it changes whenever the deployment configuration changes
and is reproducible from identical inputs (the descriptor is pure config — no
timestamps enter it). Mirrors the `product.registry.identity` pattern.
"""

from __future__ import annotations

from dataclasses import dataclass

from product.ids import content_hash


@dataclass(frozen=True, slots=True)
class DeploymentIdentity:
    """The full, immutable identity chain of one deployment plan."""

    manifest_id: str
    definition_id: str
    compilation_id: str
    package_id: str
    deployment_id: str


def deployment_id_for(package_id: str, descriptor_hash: str) -> str:
    """The Deployment ID — a content identity over the package id + the descriptor's
    canonical hash. Changes whenever deployment configuration changes; reproducible."""
    return "dp_" + content_hash(package_id, descriptor_hash)[:16]
