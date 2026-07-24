"""ReplayRecord builder (M6.1B-3) — everything needed to re-verify one
execution deterministically later, without a live registry/adapter reference.
"""

from __future__ import annotations

from ..domain import ToolRequest, ToolResult
from ..serializer import ToolSerializer
from .domain import ExecutionTrace, ReplayRecord, ToolGovernanceDecisionTrace
from .ids import replay_id_for


def replay_record_for(
    request: ToolRequest,
    result: ToolResult,
    execution_trace: ExecutionTrace,
    *,
    governance_trace: ToolGovernanceDecisionTrace | None = None,
    now: float = 0.0,
    serializer: ToolSerializer | None = None,
) -> ReplayRecord:
    s = serializer or ToolSerializer()
    # Persist the runtime-resolved request hash (`result.request_hash`), not the
    # caller-supplied `request`'s own — the runtime resolves it on an internal
    # copy and never mutates the caller's object (see trace.py).
    request_document = s.request_to_dict(request)
    request_document["request_hash"] = result.request_hash
    return ReplayRecord(
        replay_id=replay_id_for(request.request_id, result.result_hash),
        request_document=request_document,
        governance_trace=governance_trace,
        execution_trace=execution_trace,
        result_document=s.result_to_document(result),
        replay_hash=execution_trace.replay_hash,
        replay_timestamp=now,
    )
