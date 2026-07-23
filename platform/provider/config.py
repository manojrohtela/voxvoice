"""Provider Runtime configuration (M5.6B-1) — frozen, validated value objects.

Declarative wiring: store kinds, the temporary-storage TTL, collection names, and the
default capability/model. **No vendor credentials** — adapters are injected `AdapterPort`
instances (the default `EchoProviderAdapter`), never configured here. Validated at
construction (fail fast). `default()` yields an all-in-memory development config.
Router/health/policy/tenancy intelligence knobs are **not** here — that is M5.6B-2.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from core.errors import ConfigError


@dataclass(frozen=True, slots=True)
class StoreConfig:
    kind: str = "in_memory"
    options: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CollectionsConfig:
    responses: str = "provider_responses"
    sessions: str = "provider_sessions"


@dataclass(frozen=True, slots=True)
class ProviderConfiguration:
    name: str = "provider"
    document_store: StoreConfig = field(default_factory=StoreConfig)
    metadata_store: StoreConfig = field(default_factory=StoreConfig)
    collections: CollectionsConfig = field(default_factory=CollectionsConfig)
    default_provider: str = "echo"
    default_model: str = "default"
    default_capability: str = "chat"
    response_ttl_s: float | None = 900.0  # temporary storage TTL (15 min); None → no expiry

    def __post_init__(self) -> None:
        if self.response_ttl_s is not None and self.response_ttl_s <= 0:
            raise ConfigError("response_ttl_s must be > 0 or None")
        if not self.default_provider:
            raise ConfigError("default_provider must be non-empty")

    @classmethod
    def default(cls) -> ProviderConfiguration:
        return cls()
