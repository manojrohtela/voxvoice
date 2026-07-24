"""Observability configuration (M6.1B-3) — frozen, validated value objects.
Mirrors `tool.config.ToolConfiguration` / `governance.config.GovernanceConfiguration`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.errors import ConfigError

from ..config import StoreConfig

RUNTIME_VERSION = "M6.1B-3"


@dataclass(frozen=True, slots=True)
class ObservabilityCollectionsConfig:
    replays: str = "tool_observability_replays"
    audits: str = "tool_observability_audits"


@dataclass(frozen=True, slots=True)
class ObservabilityConfiguration:
    name: str = "tool_observability"
    document_store: StoreConfig = field(default_factory=StoreConfig)
    collections: ObservabilityCollectionsConfig = field(
        default_factory=ObservabilityCollectionsConfig
    )
    replay_ttl_s: float | None = None  # temporary storage; None -> no expiry

    def __post_init__(self) -> None:
        if self.replay_ttl_s is not None and self.replay_ttl_s <= 0:
            raise ConfigError("replay_ttl_s must be > 0 or None")

    @classmethod
    def default(cls) -> ObservabilityConfiguration:
        return cls()

    def fingerprint(self) -> str:
        from ..ids import content_hash

        return content_hash(
            self.name, self.document_store.kind, self.collections.replays,
            self.collections.audits, self.replay_ttl_s,
        )
