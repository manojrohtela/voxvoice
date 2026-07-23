"""ToolCapabilityCatalog (M6.1B-1) — normalized capability discovery. No intelligence.

A thin, read-only view over the `ToolRegistry` (§6/§14 of the M6.1A design): "what
can this tool do" and "which tools support X" — never "which is best" (there is no
scoring/ranking here; that is an Agent Runtime planning concern, not this catalog's).
No I/O, no side effects, no caching beyond the registry's own state.

Deliberately separate from `ToolRegistry` (§3 of the design: "registry must remain
intentionally dumb") so future semantic capability lookup (embedding-based tool
selection) can be added here as an additive method later without ever touching
registration/discovery/lifecycle in `registry.py`.
"""

from __future__ import annotations

from .registry import ToolRegistry


class ToolCapabilityCatalog:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def capabilities_for(
        self, namespace: str, name: str, version: str | None = None
    ) -> frozenset[str]:
        return self._registry.get_descriptor(namespace, name, version).manifest.capabilities

    def supports(
        self, namespace: str, name: str, capability: str, version: str | None = None
    ) -> bool:
        return capability in self.capabilities_for(namespace, name, version)

    def discover(self, capability: str) -> tuple[str, ...]:
        """Qualified names of every ACTIVE tool declaring `capability`, sorted
        (deterministic)."""
        return tuple(d.qualified_name for d in self._registry.find(capability))

    def catalog(self) -> dict[str, frozenset[str]]:
        """The full normalized capability map, qualified name → capabilities (sorted
        iteration; the highest-resolvable version per name)."""
        out: dict[str, frozenset[str]] = {}
        for qualified_name in self._registry.names():
            namespace, name = qualified_name.split(".", 1)
            out[qualified_name] = self.capabilities_for(namespace, name)
        return out
