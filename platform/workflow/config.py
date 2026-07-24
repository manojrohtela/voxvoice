"""Workflow Runtime configuration (M6.4B-1) — frozen, validated value
objects. Mirrors `skill.config.SkillConfiguration`'s shape. **No
credentials** — nothing in Workflow Runtime ever authenticates to anything,
it declares data only. `document_store` here backs the temporary
registration log only (§ `repositories/workflow_repository.py`) — not the
durable execution-state store the M6.4A design reserves for Bundle 2.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from core.errors import ConfigError

RUNTIME_VERSION = "M6.4B-1"


@dataclass(frozen=True, slots=True)
class StoreConfig:
    kind: str = "in_memory"
    options: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkflowCollectionsConfig:
    registrations: str = "workflow_registrations"


@dataclass(frozen=True, slots=True)
class WorkflowConfiguration:
    name: str = "workflow"
    document_store: StoreConfig = field(default_factory=StoreConfig)
    collections: WorkflowCollectionsConfig = field(default_factory=WorkflowCollectionsConfig)
    default_namespace: str = "internal"
    registration_ttl_s: float | None = None  # temporary registration-record TTL; None -> no expiry

    def __post_init__(self) -> None:
        if self.registration_ttl_s is not None and self.registration_ttl_s <= 0:
            raise ConfigError("registration_ttl_s must be > 0 or None")
        if not self.default_namespace:
            raise ConfigError("default_namespace must be non-empty")

    @classmethod
    def default(cls) -> WorkflowConfiguration:
        return cls()

    def fingerprint(self) -> str:
        from .ids import content_hash

        return content_hash(
            self.name, self.document_store.kind, self.collections.registrations,
            self.default_namespace, self.registration_ttl_s,
        )
