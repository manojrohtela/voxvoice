"""Governance serialization (M6.1B-2) — deterministic, round-trippable, no I/O.

Mirrors `tool.serializer.ToolSerializer`'s shape for the governance aggregates:
`ApprovalRecord`, `PolicyDecision`, `AuthorizationDecision`, `RateLimitDecision`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..extensions import PolicyVerdict
from .domain import (
    ApprovalRecord,
    ApprovalState,
    AuthorizationDecision,
    Effect,
    GovernanceIdentity,
    IdentityKind,
    PolicyDecision,
    PolicyLevel,
    PolicyRule,
    RateLimitDecision,
    RateLimitScope,
)
from .events import GovernancePhase


class GovernanceSerializer:
    # ── ApprovalRecord ────────────────────────────────────────────────────────
    def approval_to_document(self, a: ApprovalRecord) -> dict[str, Any]:
        return {
            "approval_id": a.approval_id, "request_id": a.request_id, "tool": a.tool,
            "namespace": a.namespace, "state": a.state.value, "requested_at": a.requested_at,
            "decided_at": a.decided_at, "decided_by": a.decided_by, "reason": a.reason,
            "expires_at": a.expires_at,
        }

    def approval_from_document(self, d: Mapping[str, Any]) -> ApprovalRecord:
        state = ApprovalState(d.get("state", ApprovalState.PENDING.value))
        return ApprovalRecord(
            approval_id=d["approval_id"], request_id=d["request_id"], tool=d["tool"],
            namespace=d["namespace"], state=state,
            requested_at=d.get("requested_at", 0.0), decided_at=d.get("decided_at"),
            decided_by=d.get("decided_by", ""), reason=d.get("reason", ""),
            expires_at=d.get("expires_at"),
        )

    # ── PolicyDecision ────────────────────────────────────────────────────────
    def policy_decision_to_document(
        self, request_id: str, p: PolicyDecision, *, now: float
    ) -> dict[str, Any]:
        return {
            "request_id": request_id, "verdict": p.verdict.value,
            "level": p.level.value if p.level else None, "reason": p.reason,
            "trail": [self._rule_to_dict(r) for r in p.trail], "at": now,
        }

    @staticmethod
    def _rule_to_dict(r: PolicyRule) -> dict[str, Any]:
        return {"level": r.level.value, "verdict": r.verdict.value, "tool": r.tool,
                "reason": r.reason}

    def policy_decision_from_document(self, d: Mapping[str, Any]) -> PolicyDecision:
        trail = tuple(
            PolicyRule(level=PolicyLevel(t["level"]), verdict=PolicyVerdict(t["verdict"]),
                       tool=t.get("tool"), reason=t.get("reason", ""))
            for t in d.get("trail", ())
        )
        return PolicyDecision(
            verdict=PolicyVerdict(d["verdict"]),
            level=PolicyLevel(d["level"]) if d.get("level") else None,
            reason=d.get("reason", ""), trail=trail,
        )

    # ── AuthorizationDecision ─────────────────────────────────────────────────
    def authorization_to_document(
        self, request_id: str, a: AuthorizationDecision, *, now: float
    ) -> dict[str, Any]:
        return {
            "request_id": request_id, "effect": a.effect.value,
            "identity_kind": a.identity.kind.value if a.identity else None,
            "identity_id": a.identity.id if a.identity else None,
            "action": a.action, "resource": a.resource, "reason": a.reason, "at": now,
        }

    def authorization_from_document(self, d: Mapping[str, Any]) -> AuthorizationDecision:
        identity = None
        if d.get("identity_id") is not None:
            identity = GovernanceIdentity(
                kind=IdentityKind(d["identity_kind"]), id=d["identity_id"]
            )
        return AuthorizationDecision(
            effect=Effect(d["effect"]), identity=identity, action=d.get("action", ""),
            resource=d.get("resource", ""), reason=d.get("reason", ""),
        )

    # ── RateLimitDecision ─────────────────────────────────────────────────────
    def rate_limit_to_document(
        self, request_id: str, r: RateLimitDecision, *, now: float
    ) -> dict[str, Any]:
        return {
            "request_id": request_id, "allowed": r.allowed, "remaining": r.remaining,
            "limit": r.limit, "scope": r.scope.value, "key": r.key,
            "retry_after_s": r.retry_after_s, "reason": r.reason, "at": now,
        }

    def rate_limit_from_document(self, d: Mapping[str, Any]) -> RateLimitDecision:
        return RateLimitDecision(
            allowed=d["allowed"], remaining=d["remaining"], limit=d["limit"],
            scope=RateLimitScope(d["scope"]), key=d["key"],
            retry_after_s=d.get("retry_after_s", 0.0), reason=d.get("reason", ""),
        )

    # ── event payloads ────────────────────────────────────────────────────────
    def event_payload(
        self, phase: GovernancePhase, request_id: str, extra: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {"phase": phase.value, "request_id": request_id, **extra}
