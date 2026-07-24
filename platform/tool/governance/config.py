"""Governance configuration (M6.1B-2) — frozen, validated value objects.

Mirrors `tool.config.ToolConfiguration`'s shape: store kind + collection names +
a temporary-storage TTL, this time for the approval workflow. No credentials
(secrets are injected `SecretsManager` instances, never configured here).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.errors import ConfigError

from ..config import StoreConfig


@dataclass(frozen=True, slots=True)
class GovernanceCollectionsConfig:
    approvals: str = "tool_governance_approvals"
    policy_evaluations: str = "tool_governance_policy_evaluations"
    authorizations: str = "tool_governance_authorizations"
    rate_limits: str = "tool_governance_rate_limits"


@dataclass(frozen=True, slots=True)
class GovernanceConfiguration:
    name: str = "tool_governance"
    document_store: StoreConfig = field(default_factory=StoreConfig)
    collections: GovernanceCollectionsConfig = field(default_factory=GovernanceCollectionsConfig)
    approval_ttl_s: float | None = 3600.0  # default approval expiry window (1h); None -> no expiry

    def __post_init__(self) -> None:
        if self.approval_ttl_s is not None and self.approval_ttl_s <= 0:
            raise ConfigError("approval_ttl_s must be > 0 or None")

    @classmethod
    def default(cls) -> GovernanceConfiguration:
        return cls()
