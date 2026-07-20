"""Reasoning Runtime configuration (M5.5B-1) — frozen, validated value objects.

Declarative wiring: store kinds, the temporary-storage TTL, collection names, and the
default provider/model. **No provider configuration** — providers are injected
`ModelProvider` adapters (the default `EchoProvider`), not configured here. Validated at
construction (fail fast). `default()` yields an all-in-memory development config.
Retry/cancellation/tool/policy/tenancy intelligence knobs are **not** here — that is M5.5B-2.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from core.errors import ConfigError

from .policies import ReasoningPolicies


@dataclass(frozen=True, slots=True)
class StoreConfig:
    kind: str = "in_memory"
    options: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CollectionsConfig:
    responses: str = "reasoning_responses"
    sessions: str = "reasoning_sessions"


@dataclass(frozen=True, slots=True)
class ReasoningConfiguration:
    name: str = "reasoning"
    document_store: StoreConfig = field(default_factory=StoreConfig)
    metadata_store: StoreConfig = field(default_factory=StoreConfig)
    collections: CollectionsConfig = field(default_factory=CollectionsConfig)
    default_provider: str = "echo"
    default_model: str = "default"
    package_ttl_s: float | None = 900.0  # temporary storage TTL (15 min); None → no expiry
    # ── M5.5B-2 intelligence (opt-in; the core path is unchanged when disabled) ──
    policies: ReasoningPolicies = field(default_factory=ReasoningPolicies)

    def __post_init__(self) -> None:
        if self.package_ttl_s is not None and self.package_ttl_s <= 0:
            raise ConfigError("package_ttl_s must be > 0 or None")
        if not self.default_provider:
            raise ConfigError("default_provider must be non-empty")

    @classmethod
    def default(cls) -> ReasoningConfiguration:
        return cls()
