"""GovernanceRepository — the temporary-storage boundary (M6.1B-2).

Mirrors `tool.repositories.ToolRepository`: the only code binding the
`DocumentStore` contract for governance records. Temporary only — approval
records, policy evaluations, authorization decisions, and rate-limit decisions
are a working set, not a durable audit trail (a future compliance/audit sink is
an `AuditSink`-shaped extension, out of scope here).
"""

from __future__ import annotations

from collections.abc import Sequence

from core.providers.stores import DocumentStore

from .config import GovernanceCollectionsConfig
from .domain import (
    ApprovalRecord,
    ApprovalState,
    AuthorizationDecision,
    PolicyDecision,
    RateLimitDecision,
)
from .serializer import GovernanceSerializer


class GovernanceRepository:
    def __init__(
        self,
        store: DocumentStore,
        serializer: GovernanceSerializer,
        *,
        collections: GovernanceCollectionsConfig | None = None,
    ) -> None:
        self._store = store
        self._serializer = serializer
        self._c = collections or GovernanceCollectionsConfig()

    # ── approvals (mutable: the FSM advances one record over time) ───────────
    async def add_approval(self, record: ApprovalRecord) -> None:
        await self._store.upsert(
            self._c.approvals, record.approval_id, self._serializer.approval_to_document(record)
        )

    async def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        d = await self._store.get(self._c.approvals, approval_id)
        return self._serializer.approval_from_document(d) if d is not None else None

    async def update_approval(self, record: ApprovalRecord) -> None:
        await self.add_approval(record)

    async def pending_approvals(self) -> Sequence[ApprovalRecord]:
        rows = await self._store.query(self._c.approvals, {"state": ApprovalState.PENDING.value})
        return [self._serializer.approval_from_document(r) for r in rows]

    async def all_approvals(self) -> Sequence[ApprovalRecord]:
        rows = await self._store.query(self._c.approvals, {})
        return [self._serializer.approval_from_document(r) for r in rows]

    # ── append-only evaluation logs ──────────────────────────────────────────
    async def add_policy_evaluation(
        self, eval_id: str, request_id: str, decision: PolicyDecision, *, now: float
    ) -> None:
        await self._store.upsert(
            self._c.policy_evaluations, eval_id,
            self._serializer.policy_decision_to_document(request_id, decision, now=now),
        )

    async def add_authorization(
        self, auth_id: str, request_id: str, decision: AuthorizationDecision, *, now: float
    ) -> None:
        await self._store.upsert(
            self._c.authorizations, auth_id,
            self._serializer.authorization_to_document(request_id, decision, now=now),
        )

    async def add_rate_limit_decision(
        self, rl_id: str, request_id: str, decision: RateLimitDecision, *, now: float
    ) -> None:
        await self._store.upsert(
            self._c.rate_limits, rl_id,
            self._serializer.rate_limit_to_document(request_id, decision, now=now),
        )
