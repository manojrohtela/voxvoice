"""ToolGovernance (M6.1B-2) — the governance facade.

Ties tenancy → policy → authorization → feature gates → rate limiting → approval
into one deterministic `evaluate()` call that produces a `GovernanceDecision`
(ALLOW/DENY/PENDING) + a full `ToolGovernanceTrace`. **Never executes a tool** —
that remains `ToolRuntime`'s job; this milestone deliberately does not modify
`tool/runtime.py` (already frozen at M6.1B-1). Wiring `ToolGovernance` into the
execution path via the existing `tool.extensions.PolicyEngine`/`ApprovalEngine`
seams is additive, future integration work.

Sync methods drive the async core through `tool._run.run_coro_sync` (reused
in-package, not duplicated — `governance` is a subpackage of `tool`, not a
sibling runtime; see `tool/_run.py`'s own docstring for why siblings duplicate
instead).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from core.events import Event, EventType

from .._run import run_coro_sync
from ..domain import ToolRequest, Visibility
from ..extensions import PolicyVerdict, SecretsManager
from .approval import ToolApprovalEngine
from .authorization import AuthorizationEngine
from .config import GovernanceConfiguration
from .domain import (
    GovernanceDecision,
    GovernanceIdentity,
    RateLimitRule,
    ToolGovernanceTrace,
)
from .events import PHASE_EVENT_TYPE, GovernancePhase
from .feature_gates import FeatureGateRegistry
from .ids import authorization_id_for, policy_eval_id_for, rate_limit_id_for
from .observability import Metric
from .policies import ToolPolicyEngine
from .rate_limit import RateLimitEngine
from .repository import GovernanceRepository
from .secrets import EchoSecretsProvider
from .serializer import GovernanceSerializer
from .tenancy import ToolTenancy

Publisher = Callable[..., Event | None]


class ToolGovernance:
    def __init__(
        self,
        *,
        config: GovernanceConfiguration,
        repository: GovernanceRepository,
        policy_engine: ToolPolicyEngine,
        approval_engine: ToolApprovalEngine,
        authorization_engine: AuthorizationEngine,
        tenancy: ToolTenancy,
        feature_gates: FeatureGateRegistry,
        rate_limit_engine: RateLimitEngine,
        secrets: SecretsManager | None = None,
        serializer: GovernanceSerializer | None = None,
        clock: Callable[[], float] | None = None,
        publish: Publisher | None = None,
    ) -> None:
        import time

        self._config = config
        self._repo = repository
        self._policy = policy_engine
        self._approval = approval_engine
        self._authorization = authorization_engine
        self._tenancy = tenancy
        self._feature_gates = feature_gates
        self._rate_limit = rate_limit_engine
        self._secrets = secrets or EchoSecretsProvider()
        self._serializer = serializer or GovernanceSerializer()
        self._clock = clock or time.time
        self._publish = publish
        self._counters: dict[str, int] = {}

    # ── sync surface ──────────────────────────────────────────────────────────
    def evaluate(
        self, request: ToolRequest, *,
        identity: GovernanceIdentity | None = None, region: str | None = None,
        visibility: Visibility = Visibility.PUBLIC, owner: str = "",
        rate_limit_rules: Sequence[RateLimitRule] = (),
    ) -> GovernanceDecision:
        return run_coro_sync(self.aevaluate(
            request, identity=identity, region=region, visibility=visibility,
            owner=owner, rate_limit_rules=rate_limit_rules,
        ))

    def approve(self, approval_id: str, *, decided_by: str = "", reason: str = "") -> Any:
        return run_coro_sync(self.aapprove(approval_id, decided_by=decided_by, reason=reason))

    def reject(self, approval_id: str, *, decided_by: str = "", reason: str = "") -> Any:
        return run_coro_sync(self.areject(approval_id, decided_by=decided_by, reason=reason))

    def cancel(self, approval_id: str, *, decided_by: str = "", reason: str = "") -> Any:
        return run_coro_sync(self.acancel(approval_id, decided_by=decided_by, reason=reason))

    def expire_approvals(self, *, now: float | None = None) -> int:
        return run_coro_sync(self.aexpire_approvals(now=now))

    def request_secret(self, key: str) -> str:
        value = self._secrets.resolve(key)
        self._incr(Metric.SECRET_REQUEST)
        self._emit(GovernancePhase.SECRET_REQUESTED, request_id="", extra={"key": key})
        return value

    def statistics(self) -> dict[str, int]:
        return dict(self._counters)

    # ── async core ────────────────────────────────────────────────────────────
    async def aevaluate(
        self, request: ToolRequest, *,
        identity: GovernanceIdentity | None = None, region: str | None = None,
        visibility: Visibility = Visibility.PUBLIC, owner: str = "",
        rate_limit_rules: Sequence[RateLimitRule] = (),
    ) -> GovernanceDecision:
        now = self._clock()

        tenant_decision = self._tenancy.guard(
            request, identity=identity, region=region, visibility=visibility, owner=owner,
        )
        if not tenant_decision.allowed:
            trace = ToolGovernanceTrace(tenant=tenant_decision)
            return await self._finish(request, PolicyVerdict.DENY, tenant_decision.reason, trace)

        policy_decision = self._policy.evaluate(request)
        self._incr(Metric.POLICY)
        await self._repo.add_policy_evaluation(
            policy_eval_id_for(request.request_id, policy_decision.verdict.value),
            request.request_id, policy_decision, now=now,
        )
        self._emit(
            GovernancePhase.POLICY_EVALUATED, request_id=request.request_id,
            extra={"verdict": policy_decision.verdict.value}, session_id=request.session_id,
            org_id=request.org_id,
        )
        if policy_decision.verdict is PolicyVerdict.DENY:
            trace = ToolGovernanceTrace(tenant=tenant_decision, policy=policy_decision)
            return await self._finish(request, PolicyVerdict.DENY, policy_decision.reason, trace)

        authz_decision = self._authorization.authorize(
            identity, action="execute", resource=f"{request.namespace}.{request.tool}",
        )
        self._incr(Metric.AUTHORIZATION)
        await self._repo.add_authorization(
            authorization_id_for(request.request_id, identity.id if identity else ""),
            request.request_id, authz_decision, now=now,
        )
        self._emit(
            GovernancePhase.AUTHORIZATION_COMPLETED, request_id=request.request_id,
            extra={"effect": authz_decision.effect.value}, session_id=request.session_id,
            org_id=request.org_id,
        )
        if not authz_decision.allowed:
            trace = ToolGovernanceTrace(
                tenant=tenant_decision, policy=policy_decision, authorization=authz_decision,
            )
            return await self._finish(request, PolicyVerdict.DENY, authz_decision.reason, trace)

        gate_decision = self._feature_gates.evaluate(f"{request.namespace}.{request.tool}")
        if not gate_decision.allowed:
            trace = ToolGovernanceTrace(
                tenant=tenant_decision, policy=policy_decision, authorization=authz_decision,
                feature_gate=gate_decision,
            )
            return await self._finish(request, PolicyVerdict.DENY, gate_decision.reason, trace)

        rate_decisions = []
        for rule in rate_limit_rules:
            decision = self._rate_limit.consume(rule, now)
            rate_decisions.append(decision)
            self._incr(Metric.RATE_LIMIT)
            await self._repo.add_rate_limit_decision(
                rate_limit_id_for(rule.scope.value, rule.key, now), request.request_id,
                decision, now=now,
            )
            self._emit(
                GovernancePhase.RATE_LIMIT_EVALUATED, request_id=request.request_id,
                extra={"allowed": decision.allowed, "scope": rule.scope.value, "key": rule.key},
                session_id=request.session_id, org_id=request.org_id,
            )
            if not decision.allowed:
                trace = ToolGovernanceTrace(
                    tenant=tenant_decision, policy=policy_decision, authorization=authz_decision,
                    feature_gate=gate_decision, rate_limit=tuple(rate_decisions),
                )
                return await self._finish(request, PolicyVerdict.DENY, decision.reason, trace)

        if policy_decision.verdict is PolicyVerdict.PENDING:
            record = self._approval.create(
                request_id=request.request_id, tool=request.tool, namespace=request.namespace,
                now=now, expires_at=(now + self._config.approval_ttl_s)
                if self._config.approval_ttl_s else None,
            )
            await self._repo.add_approval(record)
            self._incr(Metric.APPROVAL)
            self._emit(
                GovernancePhase.APPROVAL_REQUESTED, request_id=request.request_id,
                extra={"approval_id": record.approval_id}, session_id=request.session_id,
                org_id=request.org_id,
            )
            trace = ToolGovernanceTrace(
                tenant=tenant_decision, policy=policy_decision, authorization=authz_decision,
                feature_gate=gate_decision, rate_limit=tuple(rate_decisions), approval=record,
            )
            return await self._finish(request, PolicyVerdict.PENDING, "approval_required", trace)

        trace = ToolGovernanceTrace(
            tenant=tenant_decision, policy=policy_decision, authorization=authz_decision,
            feature_gate=gate_decision, rate_limit=tuple(rate_decisions),
        )
        return await self._finish(request, PolicyVerdict.ALLOW, "", trace)

    async def aapprove(self, approval_id: str, *, decided_by: str = "", reason: str = "") -> Any:
        record = await self._require_approval(approval_id)
        decided = self._approval.approve(record, decided_by=decided_by, reason=reason)
        await self._repo.update_approval(decided)
        self._emit(GovernancePhase.APPROVAL_GRANTED, request_id=decided.request_id,
                    extra={"approval_id": approval_id})
        return decided

    async def areject(self, approval_id: str, *, decided_by: str = "", reason: str = "") -> Any:
        record = await self._require_approval(approval_id)
        decided = self._approval.reject(record, decided_by=decided_by, reason=reason)
        await self._repo.update_approval(decided)
        self._emit(GovernancePhase.APPROVAL_REJECTED, request_id=decided.request_id,
                    extra={"approval_id": approval_id})
        return decided

    async def acancel(self, approval_id: str, *, decided_by: str = "", reason: str = "") -> Any:
        record = await self._require_approval(approval_id)
        decided = self._approval.cancel(record, decided_by=decided_by, reason=reason)
        await self._repo.update_approval(decided)
        return decided

    async def aexpire_approvals(self, *, now: float | None = None) -> int:
        now = self._clock() if now is None else now
        count = 0
        for record in await self._repo.pending_approvals():
            if record.expires_at is not None and record.expires_at <= now:
                expired = self._approval.expire(record)
                await self._repo.update_approval(expired)
                self._emit(GovernancePhase.APPROVAL_EXPIRED, request_id=expired.request_id,
                           extra={"approval_id": expired.approval_id})
                count += 1
        return count

    # ── internals ────────────────────────────────────────────────────────────
    async def _require_approval(self, approval_id: str) -> Any:
        from core.errors import ConfigError

        record = await self._repo.get_approval(approval_id)
        if record is None:
            raise ConfigError(f"unknown approval {approval_id!r}")
        return record

    async def _finish(
        self, request: ToolRequest, verdict: PolicyVerdict, reason: str, trace: ToolGovernanceTrace,
    ) -> GovernanceDecision:
        self._emit(
            GovernancePhase.GOVERNANCE_COMPLETED, request_id=request.request_id,
            extra={"verdict": verdict.value}, session_id=request.session_id, org_id=request.org_id,
        )
        return GovernanceDecision(request_id=request.request_id, verdict=verdict, reason=reason,
                                  trace=trace)

    def _incr(self, name: str) -> None:
        self._counters[name] = self._counters.get(name, 0) + 1

    def _emit(
        self, phase: GovernancePhase, *, request_id: str, extra: dict[str, Any] | None = None,
        session_id: str = "", org_id: str | None = None,
    ) -> None:
        if self._publish is None:
            return
        payload = self._serializer.event_payload(phase, request_id, extra or {})
        event_type: EventType = PHASE_EVENT_TYPE[phase]
        self._publish(event_type, payload, session_id=session_id, org_id=org_id or "")
