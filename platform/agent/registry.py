"""AgentRegistry (M6.5B-1) — registration, discovery, lifecycle, lookup.
No planning, no reasoning, no workflow delegation, no execution, no replay,
no observability (all explicit later-bundle scope). Mirrors
`workflow.registry.WorkflowRegistry`'s/`skill.registry.SkillRegistry`'s
"intentionally dumb" discipline exactly: registration/discovery/namespacing/
version-resolution/lifecycle only.
"""

from __future__ import annotations

from collections.abc import Callable

from core.errors import ConfigError

from .domain import AgentDescriptor, AgentLifecycleState, AgentManifest
from .lifecycle import AgentTrigger, advance_agent
from .validation import validate_manifest

_Key = tuple[str, str, str]


def _version_key(version: str) -> tuple:
    """A deterministic sort key: numeric dotted segments sort numerically,
    anything else falls back to lexicographic — no external semver
    dependency (duplicated from `workflow.registry`'s own helper)."""
    key: list[tuple[int, object]] = []
    for part in version.split("."):
        key.append((0, int(part)) if part.isdigit() else (1, part))
    return tuple(key)


class AgentRegistry:
    """Maps `(namespace, id, version)` -> its `AgentDescriptor`. The only
    place agent identity is namespaced; an unknown key fails fast with
    `ConfigError`."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        import time

        self._entries: dict[_Key, AgentDescriptor] = {}
        self._clock = clock or time.time

    # ── registration ──────────────────────────────────────────────────────────
    def register(self, manifest: AgentManifest) -> AgentDescriptor:
        ok, errors = validate_manifest(manifest)
        if not ok:
            raise ConfigError(
                f"invalid agent manifest {manifest.qualified_name!r}: {'; '.join(errors)}"
            )

        key = self._key(manifest.namespace, manifest.id, manifest.version)
        if key in self._entries:
            raise ConfigError(
                f"agent {manifest.qualified_name}@{manifest.version!r} already registered"
            )

        descriptor = AgentDescriptor(manifest=manifest, registered_at=self._clock())
        descriptor = advance_agent(descriptor, AgentTrigger.REGISTER, clock=self._clock)
        self._entries[key] = descriptor
        return descriptor

    def deregister(self, namespace: str, id_: str, version: str) -> None:
        self._entries.pop(self._key(namespace, id_, version), None)

    # ── lifecycle (deterministic, caller-driven) ─────────────────────────────
    def activate(self, namespace: str, id_: str, version: str) -> AgentDescriptor:
        return self._advance(namespace, id_, version, AgentTrigger.ACTIVATE)

    def deprecate(self, namespace: str, id_: str, version: str) -> AgentDescriptor:
        return self._advance(namespace, id_, version, AgentTrigger.DEPRECATE)

    def retire(self, namespace: str, id_: str, version: str) -> AgentDescriptor:
        return self._advance(namespace, id_, version, AgentTrigger.RETIRE)

    def _advance(
        self, namespace: str, id_: str, version: str, trigger: AgentTrigger
    ) -> AgentDescriptor:
        key = self._key(namespace, id_, version)
        descriptor = self._entry(key)
        updated = advance_agent(descriptor, trigger, clock=self._clock)
        self._entries[key] = updated
        return updated

    # ── lookup ────────────────────────────────────────────────────────────────
    def get_descriptor(
        self, namespace: str, id_: str, version: str | None = None
    ) -> AgentDescriptor:
        if version is not None:
            return self._entry(self._key(namespace, id_, version))
        versions = self.versions_for(namespace, id_)
        if not versions:
            raise ConfigError(f"unknown agent {namespace}.{id_}")
        active = [
            v for v in versions
            if self._entries[self._key(namespace, id_, v)].state is AgentLifecycleState.ACTIVE
        ]
        chosen = active[-1] if active else versions[-1]
        return self._entry(self._key(namespace, id_, chosen))

    def versions_for(self, namespace: str, id_: str) -> tuple[str, ...]:
        versions = [k[2] for k in self._entries if k[0] == namespace and k[1] == id_]
        return tuple(sorted(versions, key=_version_key))

    def names(self) -> tuple[str, ...]:
        return tuple(sorted({f"{k[0]}.{k[1]}" for k in self._entries}))

    def list(self, *, include_deprecated: bool = False) -> tuple[AgentDescriptor, ...]:
        wanted = {AgentLifecycleState.ACTIVE} | (
            {AgentLifecycleState.DEPRECATED} if include_deprecated else set()
        )
        return tuple(
            self._entries[k] for k in sorted(self._entries) if self._entries[k].state in wanted
        )

    def list_scoped(
        self, *, namespace: str | None = None, include_deprecated: bool = False,
    ) -> tuple[AgentDescriptor, ...]:
        out = self.list(include_deprecated=include_deprecated)
        if namespace is not None:
            out = tuple(d for d in out if d.manifest.namespace == namespace)
        return out

    # ── internals ────────────────────────────────────────────────────────────
    @staticmethod
    def _key(namespace: str, id_: str, version: str) -> _Key:
        return (namespace, id_, version)

    def _entry(self, key: _Key) -> AgentDescriptor:
        entry = self._entries.get(key)
        if entry is None:
            raise ConfigError(f"unknown agent {key[0]}.{key[1]}@{key[2]!r}")
        return entry
