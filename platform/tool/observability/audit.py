"""ToolAuditRecord builder (M6.1B-3) — never includes secrets/arguments/output,
only names, ids, and verdicts.
"""

from __future__ import annotations

from ..governance.domain import GovernanceIdentity
from .domain import ToolAuditRecord
from .ids import audit_id_for


def audit_record_for(
    *,
    request_id: str,
    tool: str,
    action: str,
    decision: str,
    now: float,
    identity: GovernanceIdentity | None = None,
    org_id: str | None = None,
    workspace_id: str | None = None,
    replay_ref: str | None = None,
    trace_ref: str | None = None,
) -> ToolAuditRecord:
    return ToolAuditRecord(
        audit_id=audit_id_for(request_id, action, decision),
        identity_kind=identity.kind.value if identity else None,
        identity_id=identity.id if identity else None,
        org_id=org_id if org_id is not None else (identity.org_id if identity else None),
        workspace_id=workspace_id if workspace_id is not None
        else (identity.workspace_id if identity else None),
        tool=tool, action=action, decision=decision, timestamp=now,
        replay_ref=replay_ref, trace_ref=trace_ref,
    )
