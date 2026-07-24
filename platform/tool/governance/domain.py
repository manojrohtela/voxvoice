"""Governance domain model (M6.1B-2) — deterministic, offline value objects.

The vocabulary `ToolGovernance` orchestrates: policy verdicts + precedence trail,
the five-state approval lifecycle, authorization decisions over a local, tool-scoped
identity shape (no `tenant` import — see the module docstring in `tenancy.py` for
why), tenant/feature-gate/rate-limit decisions, and the `ToolGovernanceTrace` that
bundles them for one governed request. Everything is frozen + slotted, mirroring
`tool.domain`. No execution, no business rules beyond what each engine computes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ..extensions import PolicyVerdict

__all__ = [
    "PolicyLevel",
    "POLICY_PRECEDENCE",
    "PolicyRule",
    "PolicyDecision",
    "ApprovalState",
    "ApprovalRecord",
    "IdentityKind",
    "GovernanceIdentity",
    "Effect",
    "AuthorizationDecision",
    "TenantDecision",
    "FeatureGateState",
    "FeatureGateDecision",
    "RateLimitScope",
    "RateLimitRule",
    "RateLimitDecision",
    "ToolGovernanceTrace",
    "GovernanceDecision",
]


class PolicyLevel(StrEnum):
    """Precedence scopes, listed low → high precedence (§ "Policy Precedence" of
    the M6.1B-2 prompt). `SKILL` is reserved — no Skill Runtime exists yet
    (Epic 6, later milestone); the level is defined now so its slot in the
    precedence chain never shifts when Skill Runtime lands."""

    SYSTEM = "system"
    ORGANIZATION = "organization"
    WORKSPACE = "workspace"
    AGENT = "agent"
    SKILL = "skill"  # reserved
    TOOL = "tool"


POLICY_PRECEDENCE: tuple[PolicyLevel, ...] = (
    PolicyLevel.SYSTEM,
    PolicyLevel.ORGANIZATION,
    PolicyLevel.WORKSPACE,
    PolicyLevel.AGENT,
    PolicyLevel.SKILL,
    PolicyLevel.TOOL,
)


@dataclass(frozen=True, slots=True)
class PolicyRule:
    """One configured rule at one precedence level. `tool=None` matches every
    tool at that level (a level-wide default); otherwise it matches a single
    `namespace.name` qualified tool name exactly."""

    level: PolicyLevel
    verdict: PolicyVerdict
    tool: str | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """The `ToolPolicyEngine`'s verdict + the ordered rules that were considered,
    for audit/trace purposes (never re-derived — the trail is what actually ran)."""

    verdict: PolicyVerdict
    level: PolicyLevel | None = None
    reason: str = ""
    trail: tuple[PolicyRule, ...] = ()


class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    """One approval workflow instance for a `PolicyVerdict.PENDING` request. No
    UI, no notifications — offline bookkeeping only; resolution (`approve`/
    `reject`) is driven by whatever caller has authority, out of scope here."""

    approval_id: str
    request_id: str
    tool: str
    namespace: str
    state: ApprovalState = ApprovalState.PENDING
    requested_at: float = 0.0
    decided_at: float | None = None
    decided_by: str = ""
    reason: str = ""
    expires_at: float | None = None

    def with_state(self, state: ApprovalState) -> ApprovalRecord:
        from dataclasses import replace

        return replace(self, state=state)


class IdentityKind(StrEnum):
    USER = "user"
    ORGANIZATION = "organization"
    WORKSPACE = "workspace"
    SERVICE = "service"
    AGENT = "agent"  # future Agent identity (Agent Runtime, later milestone)


@dataclass(frozen=True, slots=True)
class GovernanceIdentity:
    """A local, tool-governance-scoped identity — deliberately not an import of
    `tenant.TenantContext` (reuse the *semantics*, not the module; see
    `tenancy.py`). Carries just enough for isolation + authorization + rate
    limiting: who is acting, and their org/workspace scope."""

    kind: IdentityKind
    id: str
    org_id: str | None = None
    workspace_id: str | None = None
    roles: tuple[str, ...] = ()


class Effect(StrEnum):
    """A local copy of `tenant.Effect`'s two values (duplicated, not imported —
    same precedent as `_run.py`/`ids.py` across every closed runtime)."""

    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    effect: Effect
    identity: GovernanceIdentity | None
    action: str
    resource: str
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.effect is Effect.ALLOW


@dataclass(frozen=True, slots=True)
class TenantDecision:
    allowed: bool
    org_id: str | None = None
    workspace_id: str | None = None
    reason: str = ""


class FeatureGateState(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    PREVIEW = "preview"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class FeatureGateDecision:
    name: str
    state: FeatureGateState
    allowed: bool
    reason: str = ""


class RateLimitScope(StrEnum):
    TOOL = "tool"
    ORGANIZATION = "organization"
    WORKSPACE = "workspace"
    IDENTITY = "identity"


@dataclass(frozen=True, slots=True)
class RateLimitRule:
    scope: RateLimitScope
    key: str
    limit: int  # <= 0 -> unbounded
    window_s: float = 60.0


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    limit: int
    scope: RateLimitScope
    key: str
    retry_after_s: float = 0.0
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ToolGovernanceTrace:
    """Everything a governed request's decision was based on. No execution data
    (§ "Governance Trace" of the M6.1B-2 prompt) — that is `ToolResult`'s job."""

    tenant: TenantDecision | None = None
    policy: PolicyDecision | None = None
    authorization: AuthorizationDecision | None = None
    feature_gate: FeatureGateDecision | None = None
    rate_limit: tuple[RateLimitDecision, ...] = ()
    approval: ApprovalRecord | None = None


@dataclass(frozen=True, slots=True)
class GovernanceDecision:
    """The overall verdict for one request: `ALLOW` (proceed to execution),
    `DENY` (never executes), or `PENDING` (blocked on `ApprovalRecord`
    resolution). Never executes a tool itself — `ToolRuntime`'s job."""

    request_id: str
    verdict: PolicyVerdict
    reason: str = ""
    trace: ToolGovernanceTrace = field(default_factory=ToolGovernanceTrace)
