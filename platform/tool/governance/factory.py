"""GovernanceFactory — the composition root for `ToolGovernance` (M6.1B-2).

Mirrors `tool.factory.ToolFactory`: resolves store kinds to local adapters, builds
the temporary repository, and assembles every engine with its permissive-default.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.errors import ConfigError

from ..config import StoreConfig
from ..extensions import SecretsManager
from ..stores import InMemoryDocumentStore
from .approval import ToolApprovalEngine
from .authorization import AuthorizationEngine, IdentityAuthorizer
from .config import GovernanceConfiguration
from .feature_gates import FeatureGateRegistry
from .policies import ToolPolicies, ToolPolicyEngine
from .rate_limit import RateLimitEngine, RateLimitStore
from .repository import GovernanceRepository
from .runtime import Publisher, ToolGovernance
from .secrets import EchoSecretsProvider
from .serializer import GovernanceSerializer
from .tenancy import ToolTenancy

_LOCAL = frozenset({"in_memory", "local"})


class GovernanceFactory:
    @staticmethod
    def create(
        config: GovernanceConfiguration,
        *,
        policies: ToolPolicies | None = None,
        authorizer: IdentityAuthorizer | None = None,
        tenancy: ToolTenancy | None = None,
        feature_gates: FeatureGateRegistry | None = None,
        rate_limit_store: RateLimitStore | None = None,
        secrets: SecretsManager | None = None,
        clock: Callable[[], float] | None = None,
        publish: Publisher | None = None,
        **kwargs: Any,
    ) -> ToolGovernance:
        import time

        the_clock = clock or time.time
        serializer = GovernanceSerializer()
        store = _document_store(config.document_store)
        repository = GovernanceRepository(store, serializer, collections=config.collections)

        return ToolGovernance(
            config=config, repository=repository,
            policy_engine=ToolPolicyEngine(policies),
            approval_engine=ToolApprovalEngine(clock=the_clock),
            authorization_engine=AuthorizationEngine(authorizer),
            tenancy=tenancy or ToolTenancy(),
            feature_gates=feature_gates or FeatureGateRegistry(),
            rate_limit_engine=RateLimitEngine(rate_limit_store),
            secrets=secrets or EchoSecretsProvider(),
            serializer=serializer, clock=the_clock, publish=publish, **kwargs,
        )

    @staticmethod
    def default(**kwargs: Any) -> ToolGovernance:
        return GovernanceFactory.create(GovernanceConfiguration.default(), **kwargs)


def _document_store(cfg: StoreConfig) -> InMemoryDocumentStore:
    if cfg.kind in _LOCAL:
        return InMemoryDocumentStore()
    raise ConfigError(f"unknown document store kind {cfg.kind!r}")
