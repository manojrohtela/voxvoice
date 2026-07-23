"""ProviderRegistry (M5.6B-1) — registration, discovery, lifecycle, lookup. No routing.

Pure bookkeeping (§7 of the M5.6A design): registering an `AdapterPort` under a
`ProviderDescriptor`, looking one up by name or by declared capability, and tracking each
entry's health state. It performs **no scoring, no strategy, no selection preference** — that
is the `ProviderRouter`'s job (M5.6B-2). `ProviderRuntime` (the core) selects deterministically
by explicit name or "first eligible by capability, in sorted-name order" — a trivial,
routing-free selection, never a heuristic.
"""

from __future__ import annotations

from collections.abc import Callable

from core.errors import ConfigError

from .domain import Capability, ProviderDescriptor, ProviderHealthState, ProviderRegistryEntry
from .lifecycle import HealthTrigger, advance_health
from .ports import AdapterPort


class ProviderRegistry:
    """Maps a provider name → its `AdapterPort` + `ProviderRegistryEntry` (descriptor +
    health). The only place adapters are named; an unknown name fails fast with
    `ConfigError`."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        import time

        self._adapters: dict[str, AdapterPort] = {}
        self._entries: dict[str, ProviderRegistryEntry] = {}
        self._clock = clock or time.time

    # ── registration ──────────────────────────────────────────────────────────
    def register(self, descriptor: ProviderDescriptor, adapter: AdapterPort) -> None:
        if descriptor.name in self._adapters:
            raise ConfigError(f"provider {descriptor.name!r} already registered")
        self._adapters[descriptor.name] = adapter
        self._entries[descriptor.name] = ProviderRegistryEntry(descriptor=descriptor)

    def update(self, descriptor: ProviderDescriptor, adapter: AdapterPort | None = None) -> None:
        """Explicit, audited replace of a registered descriptor (and optionally its adapter).
        Never a silent overwrite via `register` — unregistered names raise `ConfigError`."""
        if descriptor.name not in self._entries:
            raise ConfigError(f"unknown provider {descriptor.name!r} (register before update)")
        health = self._entries[descriptor.name].health
        self._entries[descriptor.name] = ProviderRegistryEntry(descriptor=descriptor, health=health)
        if adapter is not None:
            self._adapters[descriptor.name] = adapter

    def deregister(self, name: str) -> None:
        self._adapters.pop(name, None)
        self._entries.pop(name, None)

    # ── lookup ────────────────────────────────────────────────────────────────
    def get(self, name: str) -> AdapterPort:
        adapter = self._adapters.get(name)
        if adapter is None:
            raise ConfigError(f"unknown provider {name!r}")
        return adapter

    def descriptor(self, name: str) -> ProviderDescriptor:
        entry = self._entries.get(name)
        if entry is None:
            raise ConfigError(f"unknown provider {name!r}")
        return entry.descriptor

    def entry(self, name: str) -> ProviderRegistryEntry:
        entry = self._entries.get(name)
        if entry is None:
            raise ConfigError(f"unknown provider {name!r}")
        return entry

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._entries))

    def find(self, capability: str | Capability) -> tuple[ProviderDescriptor, ...]:
        """All registered descriptors declaring `capability` — sorted by name (deterministic;
        no scoring, no preference ordering)."""
        cap = capability.value if isinstance(capability, Capability) else capability
        return tuple(
            self._entries[name].descriptor
            for name in sorted(self._entries)
            if getattr(self._entries[name].descriptor.capabilities, cap, False)
        )

    # ── health partitions (no automatic feedback loop in the core) ───────────
    def healthy(self) -> tuple[str, ...]:
        return self._partition(ProviderHealthState.HEALTHY)

    def degraded(self) -> tuple[str, ...]:
        return self._partition(ProviderHealthState.DEGRADED)

    def unavailable(self) -> tuple[str, ...]:
        return self._partition(ProviderHealthState.UNAVAILABLE)

    def _partition(self, state: ProviderHealthState) -> tuple[str, ...]:
        return tuple(n for n in sorted(self._entries) if self._entries[n].health is state)

    # ── deterministic, caller-driven health transitions (no observation loop) ─
    def mark_outcome_failure(self, name: str) -> ProviderHealthState:
        return self._advance(name, HealthTrigger.OUTCOME_FAILURE)

    def mark_outcome_timeout(self, name: str) -> ProviderHealthState:
        return self._advance(name, HealthTrigger.OUTCOME_TIMEOUT)

    def mark_outcome_success(self, name: str) -> ProviderHealthState:
        return self._advance(name, HealthTrigger.OUTCOME_SUCCESS)

    def mark_unavailable(self, name: str) -> ProviderHealthState:
        return self._advance(name, HealthTrigger.FORCE_UNAVAILABLE)

    def recover(self, name: str) -> ProviderHealthState:
        return self._advance(name, HealthTrigger.RECOVER)

    def _advance(self, name: str, trigger: HealthTrigger) -> ProviderHealthState:
        entry = self.entry(name)
        updated = advance_health(entry, trigger, clock=self._clock)
        self._entries[name] = updated
        return updated.health
