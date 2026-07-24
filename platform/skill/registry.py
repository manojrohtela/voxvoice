"""SkillRegistry (M6.2B-1) — registration, discovery, lifecycle, lookup, and
dependency-graph validation. No execution, no planning, no capability
resolution (§7 of the M6.2A design: "intentionally dumb," mirroring
`tool.registry.ToolRegistry` exactly).

Namespaced by origin (`internal.*` / `org.<org_id>.*` / `market.<vendor>.*`);
identity is `(namespace, id, version)`. The registry is the **durable-for-
process catalog** — it never expires on its own (mirrors `ToolRegistry`
itself; only a future *replay* record, Bundle 2, would ever be temporary —
see the M6.2A design's Risks §18).
"""

from __future__ import annotations

from collections.abc import Callable

from core.errors import ConfigError

from .domain import SkillDescriptor, SkillLifecycleState, SkillManifest
from .lifecycle import SkillTrigger, advance_skill
from .validation import validate_manifest

_Key = tuple[str, str, str]


def _version_key(version: str) -> tuple:
    """A deterministic sort key: numeric dotted segments sort numerically,
    anything else falls back to lexicographic — no external semver dependency
    (duplicated from `tool.registry`'s own helper; same rationale)."""
    key: list[tuple[int, object]] = []
    for part in version.split("."):
        key.append((0, int(part)) if part.isdigit() else (1, part))
    return tuple(key)


class SkillRegistry:
    """Maps `(namespace, id, version)` -> its `SkillDescriptor`. The only
    place skill identity is namespaced; an unknown key fails fast with
    `ConfigError`."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        import time

        self._entries: dict[_Key, SkillDescriptor] = {}
        self._clock = clock or time.time

    # ── registration (+ dependency-graph validation) ─────────────────────────
    def register(self, manifest: SkillManifest) -> SkillDescriptor:
        ok, errors = validate_manifest(manifest)
        if not ok:
            raise ConfigError(f"invalid skill manifest {manifest.qualified_name!r}: "
                              f"{'; '.join(errors)}")

        key = self._key(manifest.namespace, manifest.id, manifest.version)
        if key in self._entries:
            raise ConfigError(
                f"skill {manifest.qualified_name}@{manifest.version!r} already registered"
            )

        closure = self._resolve_closure(manifest)

        descriptor = SkillDescriptor(
            manifest=manifest, registered_at=self._clock(), dependency_closure=closure,
        )
        descriptor = advance_skill(descriptor, SkillTrigger.REGISTER, clock=self._clock)
        self._entries[key] = descriptor
        return descriptor

    def deregister(self, namespace: str, id_: str, version: str) -> None:
        self._entries.pop(self._key(namespace, id_, version), None)

    def _resolve_closure(self, manifest: SkillManifest) -> tuple[_Key, ...]:
        """Resolve every declared `SkillDependency` against already-registered
        skills, union their own cached closures, and reject a cycle back to
        this manifest's own identity. Dependencies must already exist at
        registration time, which structurally forecloses most cycles by
        construction; the explicit check here is defense-in-depth, not
        decoration (§7/§9 of the M6.2A design)."""
        own_key = self._key(manifest.namespace, manifest.id, manifest.version)
        closure: set[_Key] = set()
        for dep in manifest.dependencies:
            dep_descriptor = self.get_descriptor(dep.namespace, dep.id, dep.version)
            dep_key = self._key(dep_descriptor.manifest.namespace, dep_descriptor.manifest.id,
                                dep_descriptor.manifest.version)
            if own_key in dep_descriptor.dependency_closure or dep_key == own_key:
                raise ConfigError(
                    f"dependency cycle detected registering {manifest.qualified_name!r}: "
                    f"{manifest.qualified_name} -> {dep_descriptor.qualified_name} -> "
                    f"... -> {manifest.qualified_name}"
                )
            closure.add(dep_key)
            closure.update(dep_descriptor.dependency_closure)
        return tuple(sorted(closure))

    # ── lifecycle (deterministic, caller-driven) ─────────────────────────────
    def activate(self, namespace: str, id_: str, version: str) -> SkillDescriptor:
        return self._advance(namespace, id_, version, SkillTrigger.ACTIVATE)

    def deprecate(self, namespace: str, id_: str, version: str) -> SkillDescriptor:
        return self._advance(namespace, id_, version, SkillTrigger.DEPRECATE)

    def retire(self, namespace: str, id_: str, version: str) -> SkillDescriptor:
        return self._advance(namespace, id_, version, SkillTrigger.RETIRE)

    def _advance(
        self, namespace: str, id_: str, version: str, trigger: SkillTrigger
    ) -> SkillDescriptor:
        key = self._key(namespace, id_, version)
        descriptor = self._entry(key)
        updated = advance_skill(descriptor, trigger, clock=self._clock)
        self._entries[key] = updated
        return updated

    # ── lookup ────────────────────────────────────────────────────────────────
    def get_descriptor(
        self, namespace: str, id_: str, version: str | None = None
    ) -> SkillDescriptor:
        """Resolve a descriptor. With no `version`, resolves the highest
        `ACTIVE` version; falls back to the highest registered version of any
        state if none is `ACTIVE` (never silently invokes/references a
        withdrawn skill via unpinned resolution, but an exact-version lookup
        can still discover one)."""
        if version is not None:
            return self._entry(self._key(namespace, id_, version))
        versions = self.versions_for(namespace, id_)
        if not versions:
            raise ConfigError(f"unknown skill {namespace}.{id_}")
        active = [
            v for v in versions
            if self._entries[self._key(namespace, id_, v)].state is SkillLifecycleState.ACTIVE
        ]
        chosen = active[-1] if active else versions[-1]
        return self._entry(self._key(namespace, id_, chosen))

    def versions_for(self, namespace: str, id_: str) -> tuple[str, ...]:
        versions = [k[2] for k in self._entries if k[0] == namespace and k[1] == id_]
        return tuple(sorted(versions, key=_version_key))

    def names(self) -> tuple[str, ...]:
        """Distinct `namespace.id` qualified names, sorted."""
        return tuple(sorted({f"{k[0]}.{k[1]}" for k in self._entries}))

    def list(self, *, include_deprecated: bool = False) -> tuple[SkillDescriptor, ...]:
        wanted = {SkillLifecycleState.ACTIVE} | (
            {SkillLifecycleState.DEPRECATED} if include_deprecated else set()
        )
        return tuple(
            self._entries[k] for k in sorted(self._entries) if self._entries[k].state in wanted
        )

    def list_scoped(
        self, *, namespace: str | None = None, capability: str | None = None,
        include_deprecated: bool = False,
    ) -> tuple[SkillDescriptor, ...]:
        out = self.list(include_deprecated=include_deprecated)
        if namespace is not None:
            out = tuple(d for d in out if d.manifest.namespace == namespace)
        if capability is not None:
            out = tuple(
                d for d in out
                if capability in {req.tag for req in d.manifest.required_capabilities}
            )
        return out

    # ── internals ────────────────────────────────────────────────────────────
    @staticmethod
    def _key(namespace: str, id_: str, version: str) -> _Key:
        return (namespace, id_, version)

    def _entry(self, key: _Key) -> SkillDescriptor:
        entry = self._entries.get(key)
        if entry is None:
            raise ConfigError(f"unknown skill {key[0]}.{key[1]}@{key[2]!r}")
        return entry
