"""MCPServerRegistry (M6.8B-1) — declared-server registration, discovery, and
deterministic lookup. No network, no connections, no execution (mirrors
`ToolRegistry`/`SkillRegistry`: "intentionally dumb").

Identity is `(namespace, id, version)`. The registry is the durable-for-process
catalog of *declared* servers; the *discovered* capability snapshots live in the
`MCPCapabilityRegistry`. An unknown key fails fast with `ConfigError`.
"""

from __future__ import annotations

from collections.abc import Callable

from core.errors import ConfigError

from .domain import MCPServer, MCPServerDescriptor, MCPServerIdentity, MCPServerManifest
from .ids import descriptor_id_for, server_id_for
from .validation import validate_manifest

_Key = tuple[str, str, str]


def _version_key(version: str) -> tuple:
    """Deterministic version sort: numeric dotted segments numerically, else
    lexicographically (duplicated from `tool.registry`; no semver dependency)."""
    key: list[tuple[int, object]] = []
    for part in version.split("."):
        key.append((0, int(part)) if part.isdigit() else (1, part))
    return tuple(key)


class MCPServerRegistry:
    """Maps `(namespace, id, version)` -> `MCPServerDescriptor`. Immutable
    registration; deterministic lookup."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        import time

        self._entries: dict[_Key, MCPServerDescriptor] = {}
        self._clock = clock or time.time

    def register(self, manifest: MCPServerManifest) -> MCPServerDescriptor:
        ok, errors = validate_manifest(manifest)
        if not ok:
            raise ConfigError(
                f"invalid MCP server manifest {manifest.qualified_name!r}: {'; '.join(errors)}"
            )
        key = self._key(manifest.namespace, manifest.id, manifest.version)
        if key in self._entries:
            raise ConfigError(
                f"MCP server {manifest.qualified_name}@{manifest.version!r} already registered"
            )
        identity = MCPServerIdentity(
            server_id=server_id_for(manifest.namespace, manifest.id, manifest.version),
            namespace=manifest.namespace, id=manifest.id, version=manifest.version,
            display_name=manifest.display_name or manifest.id,
        )
        descriptor = MCPServerDescriptor(
            descriptor_id=descriptor_id_for(manifest.namespace, manifest.id, manifest.version),
            server=MCPServer(identity=identity, manifest=manifest),
            registered_at=self._clock(),
        )
        self._entries[key] = descriptor
        return descriptor

    def deregister(self, namespace: str, id_: str, version: str) -> None:
        self._entries.pop(self._key(namespace, id_, version), None)

    def get_descriptor(
        self, namespace: str, id_: str, version: str | None = None
    ) -> MCPServerDescriptor:
        if version is not None:
            return self._entry(self._key(namespace, id_, version))
        versions = self.versions_for(namespace, id_)
        if not versions:
            raise ConfigError(f"unknown MCP server {namespace}.{id_}")
        return self._entry(self._key(namespace, id_, versions[-1]))

    def get(self, namespace: str, id_: str, version: str | None = None) -> MCPServer:
        return self.get_descriptor(namespace, id_, version).server

    def versions_for(self, namespace: str, id_: str) -> tuple[str, ...]:
        versions = [k[2] for k in self._entries if k[0] == namespace and k[1] == id_]
        return tuple(sorted(versions, key=_version_key))

    def names(self) -> tuple[str, ...]:
        return tuple(sorted({f"{k[0]}.{k[1]}" for k in self._entries}))

    def list(self) -> tuple[MCPServerDescriptor, ...]:
        return tuple(
            self._entries[k]
            for k in sorted(self._entries, key=lambda k: (k[0], k[1], _version_key(k[2])))
        )

    @staticmethod
    def _key(namespace: str, id_: str, version: str) -> _Key:
        return (namespace, id_, version)

    def _entry(self, key: _Key) -> MCPServerDescriptor:
        try:
            return self._entries[key]
        except KeyError:
            raise ConfigError(f"unknown MCP server {key[0]}.{key[1]}@{key[2]!r}") from None
