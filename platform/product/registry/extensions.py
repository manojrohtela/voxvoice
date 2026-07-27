"""Registry extension points (M7.0B-3) — interfaces only, no implementation.

The M7.0A design calls for the registry to *support* distributed registries,
OCI-compatible registries, package caching, and artifact mirrors. Per this milestone
("Design extension points... Do not implement them") these are declared as protocols
a future bundle implements. The local `InMemoryPackageStore` is the only concrete
`PackageStore`; everything below is a seam.

A `SignatureProvider` seam is also declared here (the future home of the crypto this
bundle deliberately omits) — `SignatureMetadata` is its data contract.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .integrity import SignatureMetadata
from .package import ProductPackage
from .registry import PackageStore, RegistrySnapshot


@runtime_checkable
class DistributedRegistry(Protocol):
    """A registry backed by a remote/shared store. Reads produce the same immutable
    `RegistrySnapshot` contract as the local registry. Declared only."""

    def snapshot(self) -> RegistrySnapshot: ...


@runtime_checkable
class OCIRegistry(PackageStore, Protocol):
    """A `PackageStore` backed by an OCI-compatible artifact registry. Declared only."""

    def reference_for(self, package_id: str) -> str: ...


@runtime_checkable
class PackageCache(Protocol):
    """A content-addressed cache of packages by package id. Declared only."""

    def get(self, package_id: str) -> ProductPackage | None: ...

    def put(self, package: ProductPackage) -> None: ...


@runtime_checkable
class ArtifactMirror(Protocol):
    """A read-through mirror of packages across locations. Declared only."""

    def pull(self, package_id: str) -> ProductPackage | None: ...


@runtime_checkable
class SignatureProvider(Protocol):
    """The future signing seam. Produces `SignatureMetadata` for a package content
    hash; verification is its inverse. No implementation ships in this bundle."""

    def sign(self, package_hash: str) -> SignatureMetadata: ...

    def verify(self, package_hash: str, signature: SignatureMetadata) -> bool: ...
