"""HookRegistry (M6.3B) — registration, discovery, lifecycle, lookup. No
execution, no ordering *decision* beyond assigning the monotonic `sequence`
counter at registration (the actual sort lives in `ordering.py`, applied
here), no capability resolution. Mirrors `tool.registry.ToolRegistry`'s own
"adapter registered alongside its manifest" convention: a `Hook` callable is
stored next to its `HookDescriptor`, never invoked by the registry itself —
that is `HookEngine`'s job.
"""

from __future__ import annotations

from collections.abc import Callable

from core.errors import ConfigError

from .domain import HookDescriptor, HookLifecycleState, HookManifest, HookPoint, HookRuntimeScope
from .extensions import Hook
from .lifecycle import HookTrigger, advance_hook
from .ordering import ordered_hooks
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


class HookRegistry:
    """Maps `(namespace, id, version)` -> its `HookDescriptor` + `Hook`. The
    only place hook callables are named; an unknown key fails fast with
    `ConfigError`."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        import time

        self._entries: dict[_Key, HookDescriptor] = {}
        self._hooks: dict[_Key, Hook] = {}
        self._clock = clock or time.time
        self._next_sequence = 0

    # ── registration ──────────────────────────────────────────────────────────
    def register(self, manifest: HookManifest, hook: Hook) -> HookDescriptor:
        ok, errors = validate_manifest(manifest)
        if not ok:
            raise ConfigError(f"invalid hook manifest {manifest.qualified_name!r}: "
                              f"{'; '.join(errors)}")

        key = self._key(manifest.namespace, manifest.id, manifest.version)
        if key in self._entries:
            raise ConfigError(
                f"hook {manifest.qualified_name}@{manifest.version!r} already registered"
            )

        sequence = self._next_sequence
        self._next_sequence += 1

        descriptor = HookDescriptor(
            manifest=manifest, registered_at=self._clock(), sequence=sequence,
        )
        descriptor = advance_hook(descriptor, HookTrigger.REGISTER, clock=self._clock)
        self._entries[key] = descriptor
        self._hooks[key] = hook
        return descriptor

    def deregister(self, namespace: str, id_: str, version: str) -> None:
        key = self._key(namespace, id_, version)
        self._entries.pop(key, None)
        self._hooks.pop(key, None)

    # ── lifecycle (deterministic, caller-driven) ─────────────────────────────
    def activate(self, namespace: str, id_: str, version: str) -> HookDescriptor:
        return self._advance(namespace, id_, version, HookTrigger.ACTIVATE)

    def deprecate(self, namespace: str, id_: str, version: str) -> HookDescriptor:
        return self._advance(namespace, id_, version, HookTrigger.DEPRECATE)

    def retire(self, namespace: str, id_: str, version: str) -> HookDescriptor:
        return self._advance(namespace, id_, version, HookTrigger.RETIRE)

    def _advance(
        self, namespace: str, id_: str, version: str, trigger: HookTrigger
    ) -> HookDescriptor:
        key = self._key(namespace, id_, version)
        descriptor = self._entry(key)
        updated = advance_hook(descriptor, trigger, clock=self._clock)
        self._entries[key] = updated
        return updated

    # ── lookup ────────────────────────────────────────────────────────────────
    def get_descriptor(
        self, namespace: str, id_: str, version: str | None = None
    ) -> HookDescriptor:
        if version is not None:
            return self._entry(self._key(namespace, id_, version))
        versions = self.versions_for(namespace, id_)
        if not versions:
            raise ConfigError(f"unknown hook {namespace}.{id_}")
        active = [
            v for v in versions
            if self._entries[self._key(namespace, id_, v)].state is HookLifecycleState.ACTIVE
        ]
        chosen = active[-1] if active else versions[-1]
        return self._entry(self._key(namespace, id_, chosen))

    def get_hook(self, namespace: str, id_: str, version: str) -> Hook:
        key = self._key(namespace, id_, version)
        hook = self._hooks.get(key)
        if hook is None:
            raise ConfigError(f"unknown hook {namespace}.{id_}@{version!r}")
        return hook

    def hooks_for(self, descriptors: tuple[HookDescriptor, ...]) -> dict[str, Hook]:
        """Convenience: the qualified_name -> registered `Hook` mapping for a
        set of already-selected descriptors (never invokes any of them)."""
        return {
            d.qualified_name: self.get_hook(d.manifest.namespace, d.manifest.id, d.manifest.version)
            for d in descriptors
        }

    def versions_for(self, namespace: str, id_: str) -> tuple[str, ...]:
        versions = [k[2] for k in self._entries if k[0] == namespace and k[1] == id_]
        return tuple(sorted(versions, key=_version_key))

    def names(self) -> tuple[str, ...]:
        return tuple(sorted({f"{k[0]}.{k[1]}" for k in self._entries}))

    def list(self, *, include_deprecated: bool = False) -> tuple[HookDescriptor, ...]:
        wanted = {HookLifecycleState.ACTIVE} | (
            {HookLifecycleState.DEPRECATED} if include_deprecated else set()
        )
        return tuple(
            self._entries[k] for k in sorted(self._entries) if self._entries[k].state in wanted
        )

    def list_for(
        self, point: HookPoint, scope: HookRuntimeScope, *, include_deprecated: bool = False,
    ) -> tuple[HookDescriptor, ...]:
        """Every matching, already-ordered hook attached to `point` and
        matching `scope` (or declaring `HookRuntimeScope.ANY`) — the one
        query shape every runtime call-site actually needs."""
        candidates = [
            d for d in self.list(include_deprecated=include_deprecated)
            if point in d.manifest.points
            and (scope in d.manifest.scope or HookRuntimeScope.ANY in d.manifest.scope)
        ]
        return ordered_hooks(candidates)

    # ── internals ────────────────────────────────────────────────────────────
    @staticmethod
    def _key(namespace: str, id_: str, version: str) -> _Key:
        return (namespace, id_, version)

    def _entry(self, key: _Key) -> HookDescriptor:
        entry = self._entries.get(key)
        if entry is None:
            raise ConfigError(f"unknown hook {key[0]}.{key[1]}@{key[2]!r}")
        return entry
