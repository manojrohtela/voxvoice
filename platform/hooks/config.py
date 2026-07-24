"""Hook Framework configuration (M6.3B) — frozen, validated value objects.
Mirrors `skill.config.SkillConfiguration`'s shape. **No credentials.**
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from core.errors import ConfigError

RUNTIME_VERSION = "M6.3B"


@dataclass(frozen=True, slots=True)
class StoreConfig:
    kind: str = "in_memory"
    options: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HookCollectionsConfig:
    registrations: str = "hook_registrations"
    replays: str = "hook_replays"
    traces: str = "hook_traces"


@dataclass(frozen=True, slots=True)
class HookConfiguration:
    name: str = "hooks"
    document_store: StoreConfig = field(default_factory=StoreConfig)
    collections: HookCollectionsConfig = field(default_factory=HookCollectionsConfig)
    default_namespace: str = "internal"
    replay_ttl_s: float | None = None  # temporary storage; None -> no expiry

    def __post_init__(self) -> None:
        if self.replay_ttl_s is not None and self.replay_ttl_s <= 0:
            raise ConfigError("replay_ttl_s must be > 0 or None")
        if not self.default_namespace:
            raise ConfigError("default_namespace must be non-empty")

    @classmethod
    def default(cls) -> HookConfiguration:
        return cls()

    def fingerprint(self) -> str:
        from .ids import content_hash

        return content_hash(
            self.name, self.document_store.kind, self.collections.registrations,
            self.collections.replays, self.collections.traces, self.default_namespace,
            self.replay_ttl_s,
        )
