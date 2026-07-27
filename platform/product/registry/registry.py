"""ProductRegistry (M7.0B-3) — an immutable, read-only catalog of packages.

The registry stores immutable package **metadata** (via `RegistryEntry`) and, through
an injected `PackageStore` abstraction, the packages themselves. It answers lookups
— latest / exact / compatible / rollback / history / certification / provenance — and
produces content-addressed `RegistrySnapshot`s. It **never deploys and never executes
a package**; registration is the only mutation, and every stored record is immutable.

Deterministic throughout: entries are kept in canonical order, version comparisons use
`compiler.versions`, and snapshots are content-addressed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from product.compiler.serializer import canonical_hash
from product.compiler.versions import version_sort_key
from product.errors import ProductError

from .integrity import IntegrityMetadata, SignatureMetadata
from .package import ProductPackage, ProductPackageMetadata
from .provenance import ProvenanceRecord
from .versioning import CompatibilityMatrix, VersionManager


# ── package storage abstraction ───────────────────────────────────────────────────
@runtime_checkable
class PackageStore(Protocol):
    """Where full packages live. The registry indexes metadata; the store holds the
    artifacts. Local default is in-memory; a future distributed/OCI store implements
    this same Protocol (see `extensions`)."""

    def put(self, package: ProductPackage) -> None: ...

    def get(self, package_id: str) -> ProductPackage | None: ...

    def has(self, package_id: str) -> bool: ...


class InMemoryPackageStore:
    """The local, deterministic default `PackageStore`."""

    def __init__(self) -> None:
        self._packages: dict[str, ProductPackage] = {}

    def put(self, package: ProductPackage) -> None:
        self._packages[package.package_id] = package

    def get(self, package_id: str) -> ProductPackage | None:
        return self._packages.get(package_id)

    def has(self, package_id: str) -> bool:
        return package_id in self._packages


# ── immutable registry records ────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class RegistryEntry:
    """Immutable metadata for one registered package — never the package itself."""

    metadata: ProductPackageMetadata
    provenance: ProvenanceRecord
    integrity: IntegrityMetadata
    signature: SignatureMetadata
    compatibility_matrix: CompatibilityMatrix

    @property
    def package_id(self) -> str:
        return self.metadata.package_id

    @property
    def qualified_name(self) -> str:
        return self.metadata.qualified_name

    @property
    def package_version(self) -> str:
        return self.metadata.package_version

    @classmethod
    def from_package(cls, package: ProductPackage) -> RegistryEntry:
        pm = package.package_manifest
        return cls(
            metadata=package.metadata(),
            provenance=pm.provenance,
            integrity=pm.integrity,
            signature=pm.signature,
            compatibility_matrix=pm.compatibility_matrix,
        )


@dataclass(frozen=True, slots=True)
class RegistryIndex:
    """An immutable index: qualified name → its entries in ascending version order."""

    by_name: dict[str, tuple[RegistryEntry, ...]] = field(default_factory=dict)

    @classmethod
    def of(cls, entries: Iterable[RegistryEntry]) -> RegistryIndex:
        grouped: dict[str, list[RegistryEntry]] = {}
        for e in entries:
            grouped.setdefault(e.qualified_name, []).append(e)
        by_name = {
            name: tuple(sorted(items, key=lambda e: version_sort_key(e.package_version)))
            for name, items in sorted(grouped.items())
        }
        return cls(by_name=by_name)

    def versions(self, qualified_name: str) -> tuple[RegistryEntry, ...]:
        return self.by_name.get(qualified_name, ())


class RegistryQueryKind(StrEnum):
    LATEST = "latest"
    EXACT = "exact"
    COMPATIBLE = "compatible"
    ROLLBACK = "rollback"
    HISTORY = "history"
    CERTIFICATION_HISTORY = "certification_history"


@dataclass(frozen=True, slots=True)
class RegistryQuery:
    """A declarative, immutable query executed by the registry (read-only)."""

    kind: RegistryQueryKind
    qualified_name: str
    version: str | None = None
    current_version: str | None = None
    execution_platform_version: str = ""
    runtime_platform_version: str = ""


@dataclass(frozen=True, slots=True)
class RegistryReport:
    """A deterministic, machine-readable query result."""

    query: RegistryQuery
    entries: tuple[RegistryEntry, ...]

    @property
    def count(self) -> int:
        return len(self.entries)

    @property
    def match(self) -> RegistryEntry | None:
        return self.entries[0] if self.entries else None


@dataclass(frozen=True, slots=True)
class RegistrySnapshot:
    """An immutable, content-addressed point-in-time view of the registry. Validation
    and lookups operate against a snapshot, never a mutating registry."""

    entries: tuple[RegistryEntry, ...] = ()
    snapshot_id: str = ""

    @classmethod
    def of(cls, entries: Iterable[RegistryEntry]) -> RegistrySnapshot:
        ordered = tuple(
            sorted(entries, key=lambda e: (e.qualified_name, version_sort_key(e.package_version)))
        )
        payload = [
            {"package_id": e.package_id, "name": e.qualified_name, "version": e.package_version}
            for e in ordered
        ]
        return cls(entries=ordered, snapshot_id="rgs_" + canonical_hash(payload)[:16])

    def index(self) -> RegistryIndex:
        return RegistryIndex.of(self.entries)


# ── the registry ──────────────────────────────────────────────────────────────────
class ProductRegistry:
    """Append-only catalog of immutable package metadata + an injected package store.
    Read-only queries; never deploys or executes."""

    def __init__(self, store: PackageStore | None = None) -> None:
        self._store: PackageStore = store or InMemoryPackageStore()
        self._entries: dict[str, RegistryEntry] = {}
        self._versions = VersionManager()

    # ── registration (the only mutation) ──────────────────────────────────────
    def register(self, package: ProductPackage) -> RegistryEntry:
        """Store a package's metadata + artifact. Raises `ProductError` on a
        duplicate package id (packages are immutable; re-registration is a bug)."""
        if package.package_id in self._entries:
            raise ProductError(f"duplicate package {package.package_id} already registered")
        entry = RegistryEntry.from_package(package)
        self._entries[package.package_id] = entry
        self._store.put(package)
        return entry

    # ── read-only lookups ──────────────────────────────────────────────────────
    def index(self) -> RegistryIndex:
        return RegistryIndex.of(self._entries.values())

    def snapshot(self) -> RegistrySnapshot:
        return RegistrySnapshot.of(self._entries.values())

    def get_package(self, package_id: str) -> ProductPackage | None:
        return self._store.get(package_id)

    def provenance(self, package_id: str) -> ProvenanceRecord | None:
        entry = self._entries.get(package_id)
        return entry.provenance if entry else None

    def latest(self, qualified_name: str) -> RegistryEntry | None:
        versions = self.index().versions(qualified_name)
        return versions[-1] if versions else None

    def exact(self, qualified_name: str, package_version: str) -> RegistryEntry | None:
        for e in self.index().versions(qualified_name):
            if e.package_version == package_version:
                return e
        return None

    def history(self, qualified_name: str) -> tuple[RegistryEntry, ...]:
        return self.index().versions(qualified_name)

    def certification_history(self, qualified_name: str) -> tuple[RegistryEntry, ...]:
        return tuple(e for e in self.index().versions(qualified_name) if e.metadata.certified)

    def compatible(
        self, qualified_name: str, *, execution_platform_version: str, runtime_platform_version: str
    ) -> tuple[RegistryEntry, ...]:
        out: list[RegistryEntry] = []
        for e in self.index().versions(qualified_name):
            ev = e.compatibility_matrix.evaluate(
                execution_platform_version=execution_platform_version,
                runtime_platform_version=runtime_platform_version,
            )
            if ev.compatible:
                out.append(e)
        return tuple(out)

    def rollback_target(
        self, qualified_name: str, current_version: str
    ) -> RegistryEntry | None:
        """The highest registered version that is a valid rollback from
        `current_version` (a same-major downgrade). Deterministic."""
        candidates = [
            e
            for e in self.index().versions(qualified_name)
            if self._versions.is_rollback_allowed(current_version, e.package_version)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda e: version_sort_key(e.package_version))

    # ── unified query surface ──────────────────────────────────────────────────
    def execute(self, query: RegistryQuery) -> RegistryReport:
        name = query.qualified_name
        if query.kind is RegistryQueryKind.LATEST:
            entries = _some(self.latest(name))
        elif query.kind is RegistryQueryKind.EXACT:
            entries = _some(self.exact(name, query.version or ""))
        elif query.kind is RegistryQueryKind.HISTORY:
            entries = self.history(name)
        elif query.kind is RegistryQueryKind.CERTIFICATION_HISTORY:
            entries = self.certification_history(name)
        elif query.kind is RegistryQueryKind.ROLLBACK:
            entries = _some(self.rollback_target(name, query.current_version or ""))
        elif query.kind is RegistryQueryKind.COMPATIBLE:
            entries = self.compatible(
                name,
                execution_platform_version=query.execution_platform_version,
                runtime_platform_version=query.runtime_platform_version,
            )
        else:  # pragma: no cover - exhaustive over the enum
            entries = ()
        return RegistryReport(query=query, entries=tuple(entries))


def _some(entry: RegistryEntry | None) -> tuple[RegistryEntry, ...]:
    return (entry,) if entry is not None else ()
