"""Tool Runtime configuration (M6.1B-1) — frozen, validated value objects.

Declarative wiring: store kinds, the temporary-storage TTL, collection names, and
the default namespace. **No credentials, no secrets** (§11 of the M6.1A design) —
adapters are injected `ToolAdapter` instances (the default `EchoToolAdapter`), never
configured here. Validated at construction (fail fast). `default()` yields an
all-in-memory development config.
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
    results: str = "tool_results"


@dataclass(frozen=True, slots=True)
class ToolConfiguration:
    name: str = "tool"
    document_store: StoreConfig = field(default_factory=StoreConfig)
    collections: CollectionsConfig = field(default_factory=CollectionsConfig)
    default_namespace: str = "internal"
    result_ttl_s: float | None = 900.0  # temporary storage TTL (15 min); None -> no expiry

    def __post_init__(self) -> None:
        if self.result_ttl_s is not None and self.result_ttl_s <= 0:
            raise ConfigError("result_ttl_s must be > 0 or None")
        if not self.default_namespace:
            raise ConfigError("default_namespace must be non-empty")

    @classmethod
    def default(cls) -> ToolConfiguration:
        return cls()
