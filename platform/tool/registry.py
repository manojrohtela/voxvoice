"""ToolRegistry (M6.1B-1) — registration, discovery, lifecycle, lookup. No execution.

Pure bookkeeping (§6 of the M6.1A design): registering a `ToolAdapter` under a
`ToolManifest`, looking one up by (namespace, name[, version]) or by declared
capability, and tracking each entry's descriptor lifecycle state. It owns **no
execution logic** — that is `ToolRuntime`'s job (via `execution.py`). Namespaced by
origin (`internal.*` / `mcp.<server>.*` / `org.<org_id>.*`); identity is
`(namespace, name, version)`.
"""

from __future__ import annotations

from collections.abc import Callable

from core.errors import ConfigError

from .domain import DescriptorState, ToolDescriptor, ToolManifest
from .lifecycle import DescriptorTrigger, advance_descriptor
from .ports import ToolAdapter


def _version_key(version: str) -> tuple:
    """A deterministic sort key: numeric dotted segments sort numerically, anything
    else falls back to lexicographic — no external semver dependency."""
    key: list[tuple[int, object]] = []
    for part in version.split("."):
        key.append((0, int(part)) if part.isdigit() else (1, part))
    return tuple(key)


class ToolRegistry:
    """Maps `(namespace, name, version)` → its `ToolAdapter` + `ToolDescriptor`. The
    only place adapters are named; an unknown key fails fast with `ConfigError`."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        import time

        self._adapters: dict[tuple[str, str, str], ToolAdapter] = {}
        self._entries: dict[tuple[str, str, str], ToolDescriptor] = {}
        self._clock = clock or time.time

    # ── registration ──────────────────────────────────────────────────────────
    def register(self, manifest: ToolManifest, adapter: ToolAdapter) -> ToolDescriptor:
        key = self._key(manifest.namespace, manifest.name, manifest.version)
        if key in self._entries:
            raise ConfigError(
                f"tool {manifest.qualified_name}@{manifest.version!r} already registered"
            )
        descriptor = ToolDescriptor(manifest=manifest, registered_at=self._clock())
        descriptor = advance_descriptor(descriptor, DescriptorTrigger.REGISTER, clock=self._clock)
        self._adapters[key] = adapter
        self._entries[key] = descriptor
        return descriptor

    def deregister(self, namespace: str, name: str, version: str) -> None:
        key = self._key(namespace, name, version)
        self._adapters.pop(key, None)
        self._entries.pop(key, None)

    # ── descriptor lifecycle (deterministic, caller-driven) ──────────────────
    def activate(self, namespace: str, name: str, version: str) -> ToolDescriptor:
        return self._advance(namespace, name, version, DescriptorTrigger.ACTIVATE)

    def deprecate(self, namespace: str, name: str, version: str) -> ToolDescriptor:
        return self._advance(namespace, name, version, DescriptorTrigger.DEPRECATE)

    def retire(self, namespace: str, name: str, version: str) -> ToolDescriptor:
        return self._advance(namespace, name, version, DescriptorTrigger.RETIRE)

    def _advance(
        self, namespace: str, name: str, version: str, trigger: DescriptorTrigger
    ) -> ToolDescriptor:
        key = self._key(namespace, name, version)
        descriptor = self._entry(key)
        updated = advance_descriptor(descriptor, trigger, clock=self._clock)
        self._entries[key] = updated
        return updated

    # ── lookup ────────────────────────────────────────────────────────────────
    def get_adapter(self, namespace: str, name: str, version: str) -> ToolAdapter:
        key = self._key(namespace, name, version)
        adapter = self._adapters.get(key)
        if adapter is None:
            raise ConfigError(f"unknown tool {namespace}.{name}@{version!r}")
        return adapter

    def get_descriptor(
        self, namespace: str, name: str, version: str | None = None
    ) -> ToolDescriptor:
        """Resolve a descriptor. With no `version`, resolves the highest ACTIVE
        version; falls back to the highest registered version of any state if none
        is ACTIVE (so a caller can still discover a DEPRECATED/RETIRED tool by exact
        version, but never silently invokes one via unpinned resolution)."""
        if version is not None:
            return self._entry(self._key(namespace, name, version))
        versions = self.versions_for(namespace, name)
        if not versions:
            raise ConfigError(f"unknown tool {namespace}.{name}")
        active = [
            v for v in versions
            if self._entries[self._key(namespace, name, v)].state is DescriptorState.ACTIVE
        ]
        chosen = active[-1] if active else versions[-1]
        return self._entry(self._key(namespace, name, chosen))

    def versions_for(self, namespace: str, name: str) -> tuple[str, ...]:
        versions = [k[2] for k in self._entries if k[0] == namespace and k[1] == name]
        return tuple(sorted(versions, key=_version_key))

    def names(self) -> tuple[str, ...]:
        """Distinct `namespace.name` qualified names, sorted."""
        return tuple(sorted({f"{k[0]}.{k[1]}" for k in self._entries}))

    def find(self, capability: str) -> tuple[ToolDescriptor, ...]:
        """All ACTIVE descriptors declaring `capability` — sorted by qualified name
        then version (deterministic; no scoring, no preference ordering)."""
        return tuple(
            self._entries[k] for k in sorted(self._entries)
            if self._entries[k].state is DescriptorState.ACTIVE
            and capability in self._entries[k].manifest.capabilities
        )

    def list(self, *, include_deprecated: bool = False) -> tuple[ToolDescriptor, ...]:
        wanted = {DescriptorState.ACTIVE} | (
            {DescriptorState.DEPRECATED} if include_deprecated else set()
        )
        return tuple(
            self._entries[k] for k in sorted(self._entries) if self._entries[k].state in wanted
        )

    def list_scoped(
        self, *, namespace: str | None = None, capability: str | None = None,
        include_deprecated: bool = False,
    ) -> tuple[ToolDescriptor, ...]:
        out = self.list(include_deprecated=include_deprecated)
        if namespace is not None:
            out = tuple(d for d in out if d.manifest.namespace == namespace)
        if capability is not None:
            out = tuple(d for d in out if capability in d.manifest.capabilities)
        return out

    # ── internals ────────────────────────────────────────────────────────────
    @staticmethod
    def _key(namespace: str, name: str, version: str) -> tuple[str, str, str]:
        return (namespace, name, version)

    def _entry(self, key: tuple[str, str, str]) -> ToolDescriptor:
        entry = self._entries.get(key)
        if entry is None:
            raise ConfigError(f"unknown tool {key[0]}.{key[1]}@{key[2]!r}")
        return entry
