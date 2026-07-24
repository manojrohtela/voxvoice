"""Skill Runtime configuration (M6.2B-1) — frozen, validated value objects.
Mirrors `tool.config.ToolConfiguration`'s shape. **No credentials** — nothing
in Skill Runtime ever authenticates to anything, it declares data only.
Validated at construction (fail fast). `default()` yields an all-in-memory
development config.
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
    registrations: str = "skill_registrations"


@dataclass(frozen=True, slots=True)
class SkillConfiguration:
    name: str = "skill"
    document_store: StoreConfig = field(default_factory=StoreConfig)
    collections: CollectionsConfig = field(default_factory=CollectionsConfig)
    default_namespace: str = "internal"
    registration_ttl_s: float | None = None  # temporary registration-record TTL; None -> no expiry

    def __post_init__(self) -> None:
        if self.registration_ttl_s is not None and self.registration_ttl_s <= 0:
            raise ConfigError("registration_ttl_s must be > 0 or None")
        if not self.default_namespace:
            raise ConfigError("default_namespace must be non-empty")

    @classmethod
    def default(cls) -> SkillConfiguration:
        return cls()
