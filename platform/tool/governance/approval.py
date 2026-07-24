"""ToolApprovalEngine (M6.1B-2) — offline approval workflow, no UI/notifications.

Implements the frozen `tool.extensions.ApprovalEngine` seam (`status_for`)
directly, plus a richer surface (`request_approval`/`approve`/`reject`/`cancel`/
`expire`) for `ToolGovernance` to drive. State lives only in the injected
`GovernanceRepository` (temporary storage) — this class holds no state of its own,
mirroring the registry/runtime split in `tool/registry.py` + `tool/runtime.py`.
"""

from __future__ import annotations

from collections.abc import Callable

from ..extensions import ApprovalEngine as ApprovalEngineABC
from ..extensions import ApprovalStatus
from .domain import ApprovalRecord, ApprovalState
from .ids import approval_id_for
from .lifecycle import ApprovalTrigger, advance_approval

_TERMINAL_TO_STATUS: dict[ApprovalState, ApprovalStatus] = {
    ApprovalState.APPROVED: ApprovalStatus.APPROVED,
    ApprovalState.REJECTED: ApprovalStatus.REJECTED,
    # the narrow extension ABC has no EXPIRED/CANCELLED member; both read back
    # as REJECTED (terminal, non-executable) through that narrower seam.
    ApprovalState.EXPIRED: ApprovalStatus.REJECTED,
    ApprovalState.CANCELLED: ApprovalStatus.REJECTED,
    ApprovalState.PENDING: ApprovalStatus.PENDING,
}


class ToolApprovalEngine(ApprovalEngineABC):
    """Pure logic over `ApprovalRecord`s; persistence is the caller's job
    (`ToolGovernance` owns the repository). Every method here is a deterministic
    function of its inputs — no I/O, no clock reads of its own beyond what's
    passed in."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        import time

        self._clock = clock or time.time
        self._by_id: dict[str, ApprovalRecord] = {}

    def status_for(self, request_id: str) -> ApprovalStatus:
        """Frozen `ApprovalEngine` ABC surface: looks up by the *request* id
        (one approval per request; see `ids.approval_id_for`)."""
        record = self._by_id.get(approval_id_for(request_id))
        if record is None:
            return ApprovalStatus.PENDING
        return _TERMINAL_TO_STATUS[record.state]

    def create(
        self, *, request_id: str, tool: str, namespace: str, now: float,
        expires_at: float | None = None,
    ) -> ApprovalRecord:
        record = ApprovalRecord(
            approval_id=approval_id_for(request_id), request_id=request_id, tool=tool,
            namespace=namespace, state=ApprovalState.PENDING, requested_at=now,
            expires_at=expires_at,
        )
        self._by_id[record.approval_id] = record
        return record

    def approve(
        self, record: ApprovalRecord, *, decided_by: str = "", reason: str = ""
    ) -> ApprovalRecord:
        return self._decide(record, ApprovalTrigger.APPROVE, decided_by=decided_by, reason=reason)

    def reject(
        self, record: ApprovalRecord, *, decided_by: str = "", reason: str = ""
    ) -> ApprovalRecord:
        return self._decide(record, ApprovalTrigger.REJECT, decided_by=decided_by, reason=reason)

    def cancel(
        self, record: ApprovalRecord, *, decided_by: str = "", reason: str = ""
    ) -> ApprovalRecord:
        return self._decide(record, ApprovalTrigger.CANCEL, decided_by=decided_by, reason=reason)

    def expire(self, record: ApprovalRecord) -> ApprovalRecord:
        return self._decide(record, ApprovalTrigger.EXPIRE, reason="expired")

    def _decide(
        self, record: ApprovalRecord, trigger: ApprovalTrigger,
        *, decided_by: str = "", reason: str = "",
    ) -> ApprovalRecord:
        from dataclasses import replace

        advanced = advance_approval(record, trigger, clock=self._clock)
        decided = replace(
            advanced, decided_at=self._clock(), decided_by=decided_by, reason=reason,
        )
        self._by_id[decided.approval_id] = decided
        return decided
