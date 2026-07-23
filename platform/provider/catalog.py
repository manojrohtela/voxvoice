"""CapabilityCatalog (M5.6B-1) — normalized capability discovery. No intelligence.

A thin, read-only view over the `ProviderRegistry` (§9 of the M5.6A design): "what can this
adapter do" and "which adapters support X" — never "which is best" (that scoring is the
`ProviderRouter`'s job, M5.6B-2). No I/O, no side effects, no caching beyond the registry's
own state.
"""

from __future__ import annotations

from .domain import Capability, ProviderCapabilities
from .registry import ProviderRegistry


class CapabilityCatalog:
    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    def capabilities_for(self, name: str) -> ProviderCapabilities:
        return self._registry.descriptor(name).capabilities

    def supports(self, name: str, capability: str | Capability) -> bool:
        cap = capability.value if isinstance(capability, Capability) else capability
        return bool(getattr(self.capabilities_for(name), cap, False))

    def discover(self, capability: str | Capability) -> tuple[str, ...]:
        """Names of every registered adapter declaring `capability`, sorted (deterministic)."""
        return tuple(d.name for d in self._registry.find(capability))

    def catalog(self) -> dict[str, ProviderCapabilities]:
        """The full normalized capability map, name → capabilities (sorted iteration)."""
        return {
            name: self._registry.descriptor(name).capabilities for name in self._registry.names()
        }
