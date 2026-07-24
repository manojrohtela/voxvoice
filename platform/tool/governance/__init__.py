"""tool.governance — Tool Runtime Governance (M6.1B-2).

The complete governance layer for Tool Runtime: policy evaluation (deterministic
six-level precedence), authorization, an offline approval workflow (5-state FSM),
a secrets abstraction, tenant/organization isolation, feature gates, and rate
limiting — all producing **verdicts only**, never executing a tool. Built as an
additive subpackage of `tool`, reusing `tool`'s own domain/extensions/ids/_run
patterns rather than duplicating them a second time (this is not a sibling
runtime — it is `tool`'s own governance layer).

Explicit non-goals (per the M6.1B-2 prompt): streaming, a full observability
engine, workflow orchestration, agent planning, MCP, A2A, Skills, Hooks, Loop
Engine. `ToolGovernance` is not wired into `tool.runtime.ToolRuntime` in this
milestone — the frozen `tool.extensions.PolicyEngine`/`ApprovalEngine`/
`SecretsManager` seams this satisfies make that wiring a later, additive step.
"""

from .approval import ToolApprovalEngine
from .authorization import AllowAllAuthorizer, AuthorizationEngine, IdentityAuthorizer
from .config import GovernanceCollectionsConfig, GovernanceConfiguration
from .domain import (
    POLICY_PRECEDENCE,
    ApprovalRecord,
    ApprovalState,
    AuthorizationDecision,
    Effect,
    FeatureGateDecision,
    FeatureGateState,
    GovernanceDecision,
    GovernanceIdentity,
    IdentityKind,
    PolicyDecision,
    PolicyLevel,
    PolicyRule,
    RateLimitDecision,
    RateLimitRule,
    RateLimitScope,
    TenantDecision,
    ToolGovernanceTrace,
)
from .events import PHASE_EVENT_TYPE, GovernancePhase
from .extensions import (
    ABACProvider,
    CloudSecretManager,
    EnterprisePolicyProvider,
    ExternalApprovalProvider,
    KMSProvider,
    OIDCProvider,
    RBACProvider,
    SSOProvider,
    VaultProvider,
)
from .factory import GovernanceFactory
from .feature_gates import FeatureGateRegistry
from .ids import (
    approval_id_for,
    authorization_id_for,
    policy_eval_id_for,
    rate_limit_id_for,
)
from .lifecycle import APPROVAL_LIFECYCLE, ApprovalTrigger, advance_approval
from .observability import Metric
from .policies import InvalidPolicy, ToolPolicies, ToolPolicyEngine
from .rate_limit import (
    InMemoryRateLimitStore,
    RateLimitEngine,
    RateLimitState,
    RateLimitStore,
    evaluate_rate_limit,
)
from .repository import GovernanceRepository
from .runtime import ToolGovernance
from .secrets import EchoSecretsProvider
from .serializer import GovernanceSerializer
from .tenancy import ToolTenancy

__all__ = [
    "ToolGovernance",
    "GovernanceFactory",
    # policy
    "ToolPolicyEngine",
    "ToolPolicies",
    "InvalidPolicy",
    "PolicyLevel",
    "POLICY_PRECEDENCE",
    "PolicyRule",
    "PolicyDecision",
    # approval
    "ToolApprovalEngine",
    "ApprovalState",
    "ApprovalRecord",
    "ApprovalTrigger",
    "APPROVAL_LIFECYCLE",
    "advance_approval",
    # authorization
    "AuthorizationEngine",
    "IdentityAuthorizer",
    "AllowAllAuthorizer",
    "GovernanceIdentity",
    "IdentityKind",
    "Effect",
    "AuthorizationDecision",
    # secrets
    "EchoSecretsProvider",
    # tenancy
    "ToolTenancy",
    "TenantDecision",
    # feature gates
    "FeatureGateRegistry",
    "FeatureGateState",
    "FeatureGateDecision",
    # rate limiting
    "RateLimitEngine",
    "RateLimitStore",
    "InMemoryRateLimitStore",
    "RateLimitState",
    "RateLimitRule",
    "RateLimitScope",
    "RateLimitDecision",
    "evaluate_rate_limit",
    # trace + decision
    "ToolGovernanceTrace",
    "GovernanceDecision",
    # repository + serializer + config
    "GovernanceRepository",
    "GovernanceSerializer",
    "GovernanceConfiguration",
    "GovernanceCollectionsConfig",
    # ids
    "approval_id_for",
    "policy_eval_id_for",
    "authorization_id_for",
    "rate_limit_id_for",
    # events + observability
    "GovernancePhase",
    "PHASE_EVENT_TYPE",
    "Metric",
    # enterprise extension interfaces (no implementation)
    "EnterprisePolicyProvider",
    "ExternalApprovalProvider",
    "CloudSecretManager",
    "VaultProvider",
    "KMSProvider",
    "OIDCProvider",
    "SSOProvider",
    "RBACProvider",
    "ABACProvider",
]
