"""Planning configuration (M6.2B-2) — frozen, validated value objects. Mirrors
`tool.observability.config.ObservabilityConfiguration`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.errors import ConfigError

from ..config import StoreConfig

RUNTIME_VERSION = "M6.2B-2"


@dataclass(frozen=True, slots=True)
class PlanningCollectionsConfig:
    replays: str = "skill_planning_replays"
    traces: str = "skill_planning_traces"


@dataclass(frozen=True, slots=True)
class PlanningConfiguration:
    name: str = "skill_planning"
    document_store: StoreConfig = field(default_factory=StoreConfig)
    collections: PlanningCollectionsConfig = field(default_factory=PlanningCollectionsConfig)
    replay_ttl_s: float | None = None  # temporary storage; None -> no expiry

    def __post_init__(self) -> None:
        if self.replay_ttl_s is not None and self.replay_ttl_s <= 0:
            raise ConfigError("replay_ttl_s must be > 0 or None")

    @classmethod
    def default(cls) -> PlanningConfiguration:
        return cls()

    def fingerprint(self) -> str:
        from ..ids import content_hash

        return content_hash(
            self.name, self.document_store.kind, self.collections.replays,
            self.collections.traces, self.replay_ttl_s,
        )
