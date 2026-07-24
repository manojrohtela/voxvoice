"""Tool Runtime Governance (M6.1B-2) tests.

Verifies the policy engine (verdicts + deterministic six-level precedence), the
five-state approval FSM, authorization, the secrets abstraction, tenant isolation,
feature gates, rate limiting, the temporary governance repository, governance
trace shape, governance events, serialization round-trips, configuration,
determinism/replay safety, and dependency boundaries (no ownership leakage into
execution — `ToolGovernance` never executes a tool). Streaming, a full
observability engine, workflow/agent/MCP/A2A/Skills/Hooks/Loop-Engine integration
are non-goals here (M6.1B-3+).
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from core.errors import ConfigError
from core.events import Event, EventSource, EventType
from core.fsm import FSMError
from tool import ToolRequest, Visibility
from tool.extensions import PolicyVerdict
from tool.governance import (
    ApprovalState,
    AuthorizationEngine,
    Effect,
    FeatureGateRegistry,
    FeatureGateState,
    GovernanceConfiguration,
    GovernanceFactory,
    GovernanceIdentity,
    GovernancePhase,
    GovernanceRepository,
    GovernanceSerializer,
    IdentityAuthorizer,
    IdentityKind,
    InMemoryRateLimitStore,
    PolicyLevel,
    PolicyRule,
    RateLimitEngine,
    RateLimitRule,
    RateLimitScope,
    ToolApprovalEngine,
    ToolGovernance,
    ToolPolicies,
    ToolPolicyEngine,
    ToolTenancy,
    advance_approval,
)
from tool.governance.lifecycle import ApprovalTrigger
from tool.stores import InMemoryDocumentStore


def _req(**kw) -> ToolRequest:
    base = dict(request_id="r1", tool="greet", namespace="internal",
                arguments={"name": "Ada"}, session_id="s1")
    base.update(kw)
    return ToolRequest(**base)


def _gov(**kwargs):
    kwargs.setdefault("clock", lambda: 100.0)
    return GovernanceFactory.create(GovernanceConfiguration.default(), **kwargs)


def _pending_policies() -> ToolPolicies:
    return ToolPolicies(rules=(
        PolicyRule(level=PolicyLevel.TOOL, verdict=PolicyVerdict.PENDING),
    ))


# ── PolicyEngine ────────────────────────────────────────────────────────────────
def test_policy_default_allow_when_no_rule_matches():
    engine = ToolPolicyEngine()
    decision = engine.evaluate(_req())
    assert decision.verdict is PolicyVerdict.ALLOW
    assert decision.level is None


def test_policy_precedence_narrower_scope_overrides_broader():
    policies = ToolPolicies(rules=(
        PolicyRule(level=PolicyLevel.SYSTEM, verdict=PolicyVerdict.PENDING, tool="internal.greet"),
        PolicyRule(level=PolicyLevel.TOOL, verdict=PolicyVerdict.ALLOW, tool="internal.greet"),
    ))
    decision = ToolPolicyEngine(policies).evaluate(_req())
    assert decision.verdict is PolicyVerdict.ALLOW
    assert decision.level is PolicyLevel.TOOL


def test_policy_deny_is_absolute_even_under_a_narrower_allow():
    policies = ToolPolicies(rules=(
        PolicyRule(level=PolicyLevel.SYSTEM, verdict=PolicyVerdict.DENY, tool="internal.greet"),
        PolicyRule(level=PolicyLevel.TOOL, verdict=PolicyVerdict.ALLOW, tool="internal.greet"),
    ))
    decision = ToolPolicyEngine(policies).evaluate(_req())
    assert decision.verdict is PolicyVerdict.DENY
    assert decision.level is PolicyLevel.SYSTEM


def test_policy_rule_scoped_to_a_different_tool_does_not_apply():
    policies = ToolPolicies(rules=(
        PolicyRule(level=PolicyLevel.TOOL, verdict=PolicyVerdict.DENY, tool="internal.other"),
    ))
    decision = ToolPolicyEngine(policies).evaluate(_req())
    assert decision.verdict is PolicyVerdict.ALLOW


def test_policy_engine_authorize_matches_evaluate_verdict():
    policies = ToolPolicies(rules=(PolicyRule(level=PolicyLevel.TOOL, verdict=PolicyVerdict.DENY),))
    engine = ToolPolicyEngine(policies)
    assert engine.authorize(_req()) is engine.evaluate(_req()).verdict is PolicyVerdict.DENY


def test_policy_trail_records_every_applicable_rule_in_precedence_order():
    policies = ToolPolicies(rules=(
        PolicyRule(level=PolicyLevel.ORGANIZATION, verdict=PolicyVerdict.ALLOW),
        PolicyRule(level=PolicyLevel.AGENT, verdict=PolicyVerdict.ALLOW),
    ))
    decision = ToolPolicyEngine(policies).evaluate(_req())
    assert [r.level for r in decision.trail] == [PolicyLevel.ORGANIZATION, PolicyLevel.AGENT]


# ── Approval FSM ──────────────────────────────────────────────────────────────
def test_approval_engine_creates_pending_record():
    engine = ToolApprovalEngine(clock=lambda: 10.0)
    record = engine.create(request_id="r1", tool="greet", namespace="internal", now=10.0)
    assert record.state is ApprovalState.PENDING
    assert record.requested_at == 10.0


@pytest.mark.parametrize("trigger,expected", [
    (ApprovalTrigger.APPROVE, ApprovalState.APPROVED),
    (ApprovalTrigger.REJECT, ApprovalState.REJECTED),
    (ApprovalTrigger.EXPIRE, ApprovalState.EXPIRED),
    (ApprovalTrigger.CANCEL, ApprovalState.CANCELLED),
])
def test_approval_fsm_all_terminal_transitions(trigger, expected):
    engine = ToolApprovalEngine(clock=lambda: 10.0)
    record = engine.create(request_id="r1", tool="greet", namespace="internal", now=10.0)
    advanced = advance_approval(record, trigger, clock=lambda: 20.0)
    assert advanced.state is expected


def test_approval_fsm_rejects_illegal_transition_from_terminal_state():
    engine = ToolApprovalEngine(clock=lambda: 10.0)
    record = engine.create(request_id="r1", tool="greet", namespace="internal", now=10.0)
    approved = engine.approve(record)
    with pytest.raises(FSMError):
        advance_approval(approved, ApprovalTrigger.REJECT)


def test_approval_engine_status_for_reflects_terminal_state():
    engine = ToolApprovalEngine(clock=lambda: 10.0)
    engine.create(request_id="r1", tool="greet", namespace="internal", now=10.0)
    from tool.extensions import ApprovalStatus

    assert engine.status_for("r1") is ApprovalStatus.PENDING
    record = engine.create(request_id="r1", tool="greet", namespace="internal", now=10.0)
    engine.reject(record)
    assert engine.status_for("r1") is ApprovalStatus.REJECTED


def test_approval_engine_status_for_unknown_request_is_pending():
    engine = ToolApprovalEngine()
    from tool.extensions import ApprovalStatus

    assert engine.status_for("never-requested") is ApprovalStatus.PENDING


# ── Authorization ─────────────────────────────────────────────────────────────
def test_authorization_default_allows_all():
    engine = AuthorizationEngine()
    decision = engine.authorize(None, action="execute", resource="internal.greet")
    assert decision.allowed
    assert decision.effect is Effect.ALLOW


def test_authorization_custom_authorizer_can_deny():
    class DenyAll(IdentityAuthorizer):
        def authorize(self, identity, action, resource):
            from tool.governance import AuthorizationDecision

            return AuthorizationDecision(effect=Effect.DENY, identity=identity, action=action,
                                         resource=resource, reason="blocked")

    engine = AuthorizationEngine(DenyAll())
    identity = GovernanceIdentity(kind=IdentityKind.USER, id="u1")
    decision = engine.authorize(identity, action="execute", resource="internal.greet")
    assert not decision.allowed
    assert decision.reason == "blocked"


# ── Secrets ────────────────────────────────────────────────────────────────────
def test_echo_secrets_provider_is_deterministic_and_never_echoes_raw_key():
    from tool.governance import EchoSecretsProvider

    provider = EchoSecretsProvider()
    a = provider.resolve("api_key")
    b = provider.resolve("api_key")
    assert a == b
    assert "api_key" not in a
    assert provider.resolve("other_key") != a


# ── Tenancy ────────────────────────────────────────────────────────────────────
def test_tenancy_default_allows_everything():
    decision = ToolTenancy().guard(_req())
    assert decision.allowed


def test_tenancy_denies_explicit_deny_list():
    tenancy = ToolTenancy(denied_tools=frozenset({"internal.greet"}))
    decision = tenancy.guard(_req())
    assert not decision.allowed


def test_tenancy_allow_list_blocks_unlisted_tool():
    tenancy = ToolTenancy(allowed_tools=frozenset({"internal.other"}))
    decision = tenancy.guard(_req())
    assert not decision.allowed


def test_tenancy_organization_restriction():
    tenancy = ToolTenancy(allowed_orgs=frozenset({"org-a"}))
    assert not tenancy.guard(_req(org_id="org-b")).allowed
    assert tenancy.guard(_req(org_id="org-a")).allowed


def test_tenancy_region_restriction():
    tenancy = ToolTenancy(allowed_regions=frozenset({"us"}))
    assert not tenancy.guard(_req(), region="eu").allowed
    assert tenancy.guard(_req(), region="us").allowed


def test_tenancy_private_visibility_requires_owner_match():
    tenancy = ToolTenancy()
    identity = GovernanceIdentity(kind=IdentityKind.USER, id="owner-1")
    assert tenancy.guard(
        _req(), identity=identity, visibility=Visibility.PRIVATE, owner="owner-1"
    ).allowed
    assert not tenancy.guard(
        _req(), identity=identity, visibility=Visibility.PRIVATE, owner="someone-else"
    ).allowed
    assert not tenancy.guard(_req(), visibility=Visibility.PRIVATE, owner="owner-1").allowed


def test_tenancy_org_visibility_requires_matching_org():
    tenancy = ToolTenancy()
    identity = GovernanceIdentity(kind=IdentityKind.USER, id="u1", org_id="org-a")
    assert tenancy.guard(
        _req(org_id="org-a"), identity=identity, visibility=Visibility.ORG
    ).allowed
    assert not tenancy.guard(
        _req(org_id="org-b"), identity=identity, visibility=Visibility.ORG
    ).allowed


# ── Feature gates ──────────────────────────────────────────────────────────────
def test_feature_gate_ungated_defaults_to_enabled():
    registry = FeatureGateRegistry()
    assert registry.evaluate("internal.greet").allowed


def test_feature_gate_disabled_never_allowed():
    registry = FeatureGateRegistry({"internal.greet": FeatureGateState.DISABLED})
    assert not registry.evaluate("internal.greet").allowed


def test_feature_gate_preview_requires_opt_in():
    registry = FeatureGateRegistry({"internal.greet": FeatureGateState.PREVIEW})
    assert not registry.evaluate("internal.greet").allowed
    assert registry.evaluate("internal.greet", allow_preview=True).allowed


def test_feature_gate_internal_requires_opt_in():
    registry = FeatureGateRegistry({"internal.greet": FeatureGateState.INTERNAL})
    assert not registry.evaluate("internal.greet").allowed
    assert registry.evaluate("internal.greet", allow_internal=True).allowed


# ── Rate limiting ──────────────────────────────────────────────────────────────
def test_rate_limit_allows_under_the_limit_then_denies():
    engine = RateLimitEngine(InMemoryRateLimitStore())
    rule = RateLimitRule(scope=RateLimitScope.TOOL, key="internal.greet", limit=2, window_s=60)
    first = engine.consume(rule, now=0.0)
    second = engine.consume(rule, now=1.0)
    third = engine.consume(rule, now=2.0)
    assert first.allowed and second.allowed
    assert not third.allowed
    assert third.retry_after_s > 0


def test_rate_limit_window_resets_after_window_elapses():
    engine = RateLimitEngine(InMemoryRateLimitStore())
    rule = RateLimitRule(scope=RateLimitScope.TOOL, key="internal.greet", limit=1, window_s=10)
    engine.consume(rule, now=0.0)
    assert not engine.consume(rule, now=5.0).allowed
    assert engine.consume(rule, now=11.0).allowed  # window rolled over


def test_rate_limit_unbounded_when_limit_non_positive():
    engine = RateLimitEngine(InMemoryRateLimitStore())
    rule = RateLimitRule(scope=RateLimitScope.TOOL, key="internal.greet", limit=0, window_s=10)
    for i in range(5):
        assert engine.consume(rule, now=float(i)).allowed


def test_rate_limit_check_does_not_mutate_state():
    engine = RateLimitEngine(InMemoryRateLimitStore())
    rule = RateLimitRule(scope=RateLimitScope.TOOL, key="internal.greet", limit=1, window_s=60)
    peek1 = engine.check(rule, now=0.0)
    peek2 = engine.check(rule, now=0.0)
    assert peek1.allowed and peek2.allowed  # peeking never consumes


# ── GovernanceRepository (temporary storage) ──────────────────────────────────
@pytest.mark.asyncio
async def test_repository_approval_round_trip():
    from tool.governance import GovernanceCollectionsConfig

    store = InMemoryDocumentStore()
    repo = GovernanceRepository(
        store, GovernanceSerializer(), collections=GovernanceCollectionsConfig()
    )
    engine = ToolApprovalEngine(clock=lambda: 10.0)
    record = engine.create(request_id="r1", tool="greet", namespace="internal", now=10.0)

    await repo.add_approval(record)
    fetched = await repo.get_approval(record.approval_id)
    assert fetched == record

    pending = await repo.pending_approvals()
    assert record in pending

    approved = engine.approve(record, decided_by="mgr")
    await repo.update_approval(approved)
    refetched = await repo.get_approval(record.approval_id)
    assert refetched.state is ApprovalState.APPROVED
    assert refetched not in await repo.pending_approvals()


# ── ToolGovernance (facade) ───────────────────────────────────────────────────
def test_governance_evaluate_allows_by_default():
    gov = _gov()
    decision = gov.evaluate(_req())
    assert decision.verdict is PolicyVerdict.ALLOW
    assert decision.trace.tenant.allowed
    assert decision.trace.authorization.allowed


def test_governance_evaluate_denies_on_tenant_violation():
    tenancy = ToolTenancy(denied_tools=frozenset({"internal.greet"}))
    gov = _gov(tenancy=tenancy)
    decision = gov.evaluate(_req())
    assert decision.verdict is PolicyVerdict.DENY
    assert decision.trace.policy is None  # tenancy short-circuits before policy runs


def test_governance_evaluate_denies_on_policy():
    policies = ToolPolicies(rules=(PolicyRule(level=PolicyLevel.TOOL, verdict=PolicyVerdict.DENY),))
    gov = _gov(policies=policies)
    decision = gov.evaluate(_req())
    assert decision.verdict is PolicyVerdict.DENY
    assert decision.trace.policy.verdict is PolicyVerdict.DENY


def test_governance_evaluate_pending_creates_approval_and_resolves():
    policies = _pending_policies()
    gov = _gov(policies=policies)
    decision = gov.evaluate(_req())
    assert decision.verdict is PolicyVerdict.PENDING
    approval = decision.trace.approval
    assert approval.state is ApprovalState.PENDING

    approved = gov.approve(approval.approval_id, decided_by="mgr", reason="ok")
    assert approved.state is ApprovalState.APPROVED
    assert approved.decided_by == "mgr"


def test_governance_reject_and_cancel():
    policies = _pending_policies()
    gov = _gov(policies=policies)
    d1 = gov.evaluate(_req(request_id="r1"))
    rejected = gov.reject(d1.trace.approval.approval_id, reason="no")
    assert rejected.state is ApprovalState.REJECTED

    d2 = gov.evaluate(_req(request_id="r2"))
    cancelled = gov.cancel(d2.trace.approval.approval_id)
    assert cancelled.state is ApprovalState.CANCELLED


def test_governance_expire_approvals_sweeps_past_ttl():
    policies = _pending_policies()
    gov = GovernanceFactory.create(
        GovernanceConfiguration(approval_ttl_s=10.0), policies=policies, clock=lambda: 100.0,
    )
    gov.evaluate(_req())
    assert gov.expire_approvals(now=100.0) == 0  # not yet expired
    assert gov.expire_approvals(now=200.0) == 1  # now past expiry


def test_governance_denies_on_feature_gate():
    gates = FeatureGateRegistry({"internal.greet": FeatureGateState.DISABLED})
    gov = _gov(feature_gates=gates)
    decision = gov.evaluate(_req())
    assert decision.verdict is PolicyVerdict.DENY
    assert decision.trace.feature_gate is not None
    assert not decision.trace.feature_gate.allowed


def test_governance_denies_on_rate_limit():
    gov = _gov()
    rule = RateLimitRule(scope=RateLimitScope.TOOL, key="internal.greet", limit=1, window_s=60)
    first = gov.evaluate(_req(), rate_limit_rules=[rule])
    second = gov.evaluate(_req(), rate_limit_rules=[rule])
    assert first.verdict is PolicyVerdict.ALLOW
    assert second.verdict is PolicyVerdict.DENY
    assert second.trace.rate_limit[-1].allowed is False


def test_governance_request_secret_never_returns_the_raw_key():
    gov = _gov()
    value = gov.request_secret("db_password")
    assert value != "db_password"
    assert gov.statistics()


def test_governance_statistics_counts_operations():
    gov = _gov()
    gov.evaluate(_req())
    stats = gov.statistics()
    assert stats.get("tool.governance.policy", 0) >= 1
    assert stats.get("tool.governance.authorization", 0) >= 1


# ── Determinism / replay safety ────────────────────────────────────────────────
def test_governance_evaluate_is_deterministic_for_identical_input():
    gov1 = _gov()
    gov2 = _gov()
    r = _req()
    d1 = gov1.evaluate(r)
    d2 = gov2.evaluate(r)
    assert d1.verdict == d2.verdict
    assert d1.trace.authorization == d2.trace.authorization


def test_governance_ids_are_content_derived_not_random():
    from tool.governance import approval_id_for, policy_eval_id_for

    assert approval_id_for("r1") == approval_id_for("r1")
    assert approval_id_for("r1") != approval_id_for("r2")
    assert policy_eval_id_for("r1", "allow") != policy_eval_id_for("r1", "deny")


# ── Governance events ──────────────────────────────────────────────────────────
def test_governance_emits_events_through_injected_publisher():
    captured: list[Event] = []

    def publish(type: EventType, payload: dict, *, session_id: str = "", org_id: str = ""):
        event = Event(
            id=f"e{len(captured)}", type=type, ts=1.0, seq=len(captured),
            session_id=session_id, source=EventSource.TOOL, org_id=org_id, data=payload,
        )
        captured.append(event)
        return event

    gov = _gov(publish=publish)
    gov.evaluate(_req())
    phases = {e.data["phase"] for e in captured}
    assert GovernancePhase.POLICY_EVALUATED.value in phases
    assert GovernancePhase.AUTHORIZATION_COMPLETED.value in phases
    assert GovernancePhase.GOVERNANCE_COMPLETED.value in phases
    assert all(e.type.value.startswith("tool.") for e in captured)


def test_governance_no_publisher_is_a_safe_no_op():
    gov = _gov(publish=None)
    decision = gov.evaluate(_req())
    assert decision.verdict is PolicyVerdict.ALLOW


# ── Serialization round-trips ──────────────────────────────────────────────────
def test_serializer_approval_round_trip():
    s = GovernanceSerializer()
    engine = ToolApprovalEngine(clock=lambda: 5.0)
    record = engine.create(request_id="r1", tool="greet", namespace="internal", now=5.0)
    doc = s.approval_to_document(record)
    assert s.approval_from_document(doc) == record


def test_serializer_policy_decision_round_trip():
    s = GovernanceSerializer()
    policies = ToolPolicies(
        rules=(PolicyRule(level=PolicyLevel.TOOL, verdict=PolicyVerdict.ALLOW),)
    )
    decision = ToolPolicyEngine(policies).evaluate(_req())
    doc = s.policy_decision_to_document("r1", decision, now=1.0)
    assert s.policy_decision_from_document(doc) == decision


def test_serializer_authorization_round_trip():
    s = GovernanceSerializer()
    identity = GovernanceIdentity(kind=IdentityKind.USER, id="u1")
    decision = AuthorizationEngine().authorize(
        identity, action="execute", resource="internal.greet"
    )
    doc = s.authorization_to_document("r1", decision, now=1.0)
    assert s.authorization_from_document(doc) == decision


def test_serializer_rate_limit_round_trip():
    from tool.governance.rate_limit import RateLimitState, evaluate_rate_limit

    s = GovernanceSerializer()
    rule = RateLimitRule(scope=RateLimitScope.TOOL, key="internal.greet", limit=5, window_s=60)
    decision, _ = evaluate_rate_limit(rule, RateLimitState(), now=0.0)
    doc = s.rate_limit_to_document("r1", decision, now=0.0)
    assert s.rate_limit_from_document(doc) == decision


# ── Configuration ──────────────────────────────────────────────────────────────
def test_governance_configuration_rejects_non_positive_ttl():
    with pytest.raises(ConfigError):
        GovernanceConfiguration(approval_ttl_s=0.0)


def test_governance_configuration_default_is_valid():
    assert GovernanceConfiguration.default().approval_ttl_s == 3600.0


# ── Dependency rules (no ownership leakage) ────────────────────────────────────
def _imports(pkg: str) -> set[str]:
    tops: set[str] = set()
    for p in pathlib.Path(pkg).rglob("*.py"):
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.ImportFrom) and n.module and not n.level:
                tops.add(n.module.split(".")[0])
            elif isinstance(n, ast.Import):
                for a in n.names:
                    tops.add(a.name.split(".")[0])
    return tops


def test_tool_governance_dependency_rules():
    # tool.governance depends only on the foundation (core) plus its own parent
    # package (tool) — no `tenant` import (the local `GovernanceIdentity`/`Effect`
    # shapes duplicate the pattern instead; see tenancy.py's own docstring), no
    # sibling runtime, no bus implementation.
    forbidden = {"runtime", "api", "gateway", "replay", "events", "memory", "knowledge",
                 "context", "prompt", "reasoning", "provider", "bridge", "pipeline",
                 "voicebot", "observability", "tenant"}
    assert not (_imports("tool/governance") & forbidden), _imports("tool/governance")


def test_governance_owns_no_execution_logic():
    """`ToolGovernance` produces verdicts only — it must never expose an
    execute/invoke/run-shaped method (that stays `ToolRuntime`'s job)."""
    surface = {name for name in dir(ToolGovernance) if not name.startswith("_")}
    assert not ({"execute", "invoke", "run", "aexecute"} & surface)
