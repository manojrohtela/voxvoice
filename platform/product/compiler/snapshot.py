"""BindingSnapshot (M7.0B-2; renamed M7.0B-3 per ADR-0020) — the immutable
binding-resolution catalog (ADR-0019).

Binding resolution resolves against this injected, deterministic value object —
never a live Epic 6 registry. A snapshot lists, per `(kind, namespace, id)`, the
versions available at a point in time. A future composition-root bundle may build
one *from* the frozen Execution Platform registries; the compiler only ever sees
this data structure, so compilation stays pure, reproducible, and freeze-safe.

Renamed from `RegistrySnapshot`/`RegistryEntry` (ADR-0020): the "Registry" noun is
reserved for the Product Registry of packages (`product.registry`); this catalogues
*bindings*, hence `BindingSnapshot`/`BindingSnapshotEntry`. Behaviour is unchanged.

`snapshot_id` is a content hash over the (canonicalized) entries — it participates
in the compilation identity so a compiled product is auditably tied to the exact
catalog it was compiled against.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass

from .bindings import BindingKind
from .versions import highest_version


@dataclass(frozen=True, slots=True, order=True)
class BindingSnapshotEntry:
    """One available reference and its versions (stored sorted for determinism)."""

    kind: BindingKind
    namespace: str
    id: str
    versions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BindingSnapshot:
    """An immutable, content-addressed catalog of resolvable references."""

    entries: tuple[BindingSnapshotEntry, ...] = ()
    snapshot_id: str = ""

    @classmethod
    def of(cls, entries: Iterable[BindingSnapshotEntry]) -> BindingSnapshot:
        """Build a snapshot with entries in canonical order and a derived id.
        Duplicate `(kind, namespace, id)` entries are merged (versions unioned) so
        the catalog is well-formed regardless of input shape."""
        merged: dict[tuple[str, str, str], set[str]] = {}
        for e in entries:
            merged.setdefault((e.kind.value, e.namespace, e.id), set()).update(e.versions)
        canonical = tuple(
            BindingSnapshotEntry(
                kind=BindingKind(kind), namespace=ns, id=id_, versions=tuple(sorted(versions))
            )
            for (kind, ns, id_), versions in sorted(merged.items())
        )
        return cls(entries=canonical, snapshot_id=_snapshot_id(canonical))

    def available_versions(
        self, kind: BindingKind, namespace: str, id: str
    ) -> tuple[str, ...]:
        for e in self.entries:
            if e.kind is kind and e.namespace == namespace and e.id == id:
                return e.versions
        return ()

    def resolve_version(
        self, kind: BindingKind, namespace: str, id: str, requested: str | None
    ) -> str | None:
        """Resolve a reference to a concrete version. A pinned `requested` resolves
        iff it is available; an unpinned (`None`) request resolves to the highest
        available version. Returns None when nothing satisfies it (a missing
        binding)."""
        versions = self.available_versions(kind, namespace, id)
        if requested is None:
            return highest_version(versions)
        return requested if requested in versions else None


def _snapshot_id(entries: tuple[BindingSnapshotEntry, ...]) -> str:
    payload = [
        {"kind": e.kind.value, "namespace": e.namespace, "id": e.id, "versions": list(e.versions)}
        for e in entries
    ]
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "rs_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
