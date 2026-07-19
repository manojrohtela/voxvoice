"""Memory Runtime configuration (M5.1B-1) — frozen, validated value objects.

Declarative wiring for the runtime: which store kind backs each repository, the
embedder, per-tier options, and cache settings. Validated at construction (fail fast,
Constitution §A2). `default()` yields an all-in-memory development configuration.

TTL / capacity / cache fields are **data only** — no expiration, eviction, or cache
logic is implemented in M5.1B-1 (those are later milestones); the config simply
declares the shape so those milestones need no config change.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from core.errors import ConfigError

from .domain import MemoryTier


@dataclass(frozen=True, slots=True)
class StoreConfig:
    kind: str = "in_memory"  # adapter selector resolved by the factory
    collection: str = "memories"
    options: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EmbedderConfig:
    kind: str = "local"
    dimensions: int = 64
    options: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.dimensions <= 0:
            raise ConfigError("EmbedderConfig.dimensions must be positive")


@dataclass(frozen=True, slots=True)
class CacheConfig:
    """Hot-object cache settings (the Memory Cache lives behind the repository)."""

    enabled: bool = False
    max_items: int = 256


@dataclass(frozen=True, slots=True)
class RepositoryConfig:
    document: StoreConfig = field(default_factory=StoreConfig)
    vector: StoreConfig = field(default_factory=lambda: StoreConfig(collection="vectors"))
    metadata: StoreConfig = field(default_factory=lambda: StoreConfig(collection="metadata"))
    embedder: EmbedderConfig = field(default_factory=EmbedderConfig)


@dataclass(frozen=True, slots=True)
class TierConfig:
    """Per-tier options. `vectorize` is the only field with behaviour in M5.1B-1
    (whether stored records of this tier are also indexed into the vector store)."""

    tier: MemoryTier
    vectorize: bool = False
    ttl_seconds: int | None = None  # data only (expiration deferred)
    max_items: int | None = None  # data only (capacity deferred)


@dataclass(frozen=True, slots=True)
class MemoryConfiguration:
    name: str = "memory"
    repository: RepositoryConfig = field(default_factory=RepositoryConfig)
    tiers: tuple[TierConfig, ...] = ()
    cache: CacheConfig = field(default_factory=CacheConfig)

    def __post_init__(self) -> None:
        seen: set[MemoryTier] = set()
        for tc in self.tiers:
            if tc.tier in seen:
                raise ConfigError(f"duplicate tier configuration for {tc.tier.value!r}")
            seen.add(tc.tier)

    def tier(self, tier: MemoryTier) -> TierConfig:
        """The declared config for `tier`, or an inert default if unspecified."""
        for tc in self.tiers:
            if tc.tier is tier:
                return tc
        return TierConfig(tier=tier)

    @classmethod
    def default(cls) -> MemoryConfiguration:
        """All-in-memory development configuration. The semantic tier is vectorized;
        everything else is document-only."""
        return cls(
            tiers=(
                TierConfig(tier=MemoryTier.SEMANTIC, vectorize=True),
            ),
            cache=CacheConfig(enabled=True, max_items=512),
        )
