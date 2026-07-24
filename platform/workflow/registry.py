"""WorkflowRegistry (M6.4B-1) — registration, discovery, lifecycle, lookup.
No execution, no checkpoints, no recovery, no replay, no observability (all
explicit Bundle 2 scope). Mirrors `skill.registry.SkillRegistry`'s
"intentionally dumb" discipline exactly: registration/discovery/namespacing/
version-resolution/lifecycle only.

`graph_hash` is always registry-computed at registration time, never trusted
from a caller-supplied value — the registry replaces the manifest's embedded
`WorkflowGraph` with one carrying the freshly-computed hash before storing
the descriptor.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from core.errors import ConfigError

from .domain import WorkflowDescriptor, WorkflowLifecycleState, WorkflowManifest
from .graph import compute_graph_hash
from .lifecycle import WorkflowTrigger, advance_workflow
from .validation import validate_manifest

_Key = tuple[str, str, str]


def _version_key(version: str) -> tuple:
    """A deterministic sort key: numeric dotted segments sort numerically,
    anything else falls back to lexicographic — no external semver
    dependency (duplicated from `tool.registry`'s own helper)."""
    key: list[tuple[int, object]] = []
    for part in version.split("."):
        key.append((0, int(part)) if part.isdigit() else (1, part))
    return tuple(key)


class WorkflowRegistry:
    """Maps `(namespace, id, version)` -> its `WorkflowDescriptor`. The only
    place workflow identity is namespaced; an unknown key fails fast with
    `ConfigError`."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        import time

        self._entries: dict[_Key, WorkflowDescriptor] = {}
        self._clock = clock or time.time

    # ── registration ──────────────────────────────────────────────────────────
    def register(self, manifest: WorkflowManifest) -> WorkflowDescriptor:
        ok, errors = validate_manifest(manifest)
        if not ok:
            raise ConfigError(
                f"invalid workflow manifest {manifest.qualified_name!r}: {'; '.join(errors)}"
            )

        key = self._key(manifest.namespace, manifest.id, manifest.version)
        if key in self._entries:
            raise ConfigError(
                f"workflow {manifest.qualified_name}@{manifest.version!r} already registered"
            )

        graph_hash = compute_graph_hash(manifest.graph)
        hashed_manifest = replace(manifest, graph=manifest.graph.with_hash(graph_hash))

        descriptor = WorkflowDescriptor(manifest=hashed_manifest, registered_at=self._clock())
        descriptor = advance_workflow(descriptor, WorkflowTrigger.REGISTER, clock=self._clock)
        self._entries[key] = descriptor
        return descriptor

    def deregister(self, namespace: str, id_: str, version: str) -> None:
        self._entries.pop(self._key(namespace, id_, version), None)

    # ── lifecycle (deterministic, caller-driven) ─────────────────────────────
    def activate(self, namespace: str, id_: str, version: str) -> WorkflowDescriptor:
        return self._advance(namespace, id_, version, WorkflowTrigger.ACTIVATE)

    def deprecate(self, namespace: str, id_: str, version: str) -> WorkflowDescriptor:
        return self._advance(namespace, id_, version, WorkflowTrigger.DEPRECATE)

    def retire(self, namespace: str, id_: str, version: str) -> WorkflowDescriptor:
        return self._advance(namespace, id_, version, WorkflowTrigger.RETIRE)

    def _advance(
        self, namespace: str, id_: str, version: str, trigger: WorkflowTrigger
    ) -> WorkflowDescriptor:
        key = self._key(namespace, id_, version)
        descriptor = self._entry(key)
        updated = advance_workflow(descriptor, trigger, clock=self._clock)
        self._entries[key] = updated
        return updated

    # ── lookup ────────────────────────────────────────────────────────────────
    def get_descriptor(
        self, namespace: str, id_: str, version: str | None = None
    ) -> WorkflowDescriptor:
        if version is not None:
            return self._entry(self._key(namespace, id_, version))
        versions = self.versions_for(namespace, id_)
        if not versions:
            raise ConfigError(f"unknown workflow {namespace}.{id_}")
        active = [
            v for v in versions
            if self._entries[self._key(namespace, id_, v)].state is WorkflowLifecycleState.ACTIVE
        ]
        chosen = active[-1] if active else versions[-1]
        return self._entry(self._key(namespace, id_, chosen))

    def versions_for(self, namespace: str, id_: str) -> tuple[str, ...]:
        versions = [k[2] for k in self._entries if k[0] == namespace and k[1] == id_]
        return tuple(sorted(versions, key=_version_key))

    def names(self) -> tuple[str, ...]:
        return tuple(sorted({f"{k[0]}.{k[1]}" for k in self._entries}))

    def list(self, *, include_deprecated: bool = False) -> tuple[WorkflowDescriptor, ...]:
        wanted = {WorkflowLifecycleState.ACTIVE} | (
            {WorkflowLifecycleState.DEPRECATED} if include_deprecated else set()
        )
        return tuple(
            self._entries[k] for k in sorted(self._entries) if self._entries[k].state in wanted
        )

    def list_scoped(
        self, *, namespace: str | None = None, include_deprecated: bool = False,
    ) -> tuple[WorkflowDescriptor, ...]:
        out = self.list(include_deprecated=include_deprecated)
        if namespace is not None:
            out = tuple(d for d in out if d.manifest.namespace == namespace)
        return out

    # ── internals ────────────────────────────────────────────────────────────
    @staticmethod
    def _key(namespace: str, id_: str, version: str) -> _Key:
        return (namespace, id_, version)

    def _entry(self, key: _Key) -> WorkflowDescriptor:
        entry = self._entries.get(key)
        if entry is None:
            raise ConfigError(f"unknown workflow {key[0]}.{key[1]}@{key[2]!r}")
        return entry
