"""ResultNormalizer (M6.1B-1) — unified result assembly. No intelligence.

Turns a `ToolAdapter`'s raw `ToolExecutionOutcome` into the runtime's unified,
self-sufficient `ToolResult` (§12 of the M6.1A design) — identity, timing,
classification, and hashing are the runtime's concern, never the adapter's, so
every adapter (however implemented) produces the exact same result shape. Mirrors
`provider.response.ResponseNormalizer`.
"""

from __future__ import annotations

from .domain import Classification, ExecutionState, ToolDescriptor, ToolRequest, ToolResult
from .ids import result_hash_for
from .ports import ToolExecutionOutcome


class ResultNormalizer:
    def success(
        self,
        request: ToolRequest,
        descriptor: ToolDescriptor,
        outcome: ToolExecutionOutcome,
        *,
        now: float,
        expires_at: float | None,
    ) -> ToolResult:
        return ToolResult(
            request_id=request.request_id,
            state=ExecutionState.EXECUTING,  # advanced to COMPLETED by the runtime's FSM call
            success=True,
            output=outcome.output,
            classification=descriptor.manifest.classification,
            usage=outcome.usage,
            attachments=outcome.attachments,
            tool=descriptor.manifest.name,
            namespace=descriptor.manifest.namespace,
            version=descriptor.version,
            request_hash=request.request_hash,
            result_hash=result_hash_for(request.request_hash, outcome.output),
            created_at=now,
            expires_at=expires_at,
            session_id=request.session_id,
            org_id=request.org_id,
        )

    def failure(
        self,
        request: ToolRequest,
        *,
        state: ExecutionState,
        error: str,
        classification: Classification = Classification.READ_ONLY,
        now: float,
    ) -> ToolResult:
        """Build the pre-FAIL result content at whatever state the failure was
        detected in (`VALIDATING` or `EXECUTING`) — the runtime fires the `FAIL`
        trigger itself via `advance_execution`, so the FSM (not this normalizer)
        remains the sole writer of state transitions."""
        return ToolResult(
            request_id=request.request_id,
            state=state,
            success=False,
            error=error,
            classification=classification,
            tool=request.tool,
            namespace=request.namespace,
            version=request.version or "",
            request_hash=request.request_hash,
            created_at=now,
            session_id=request.session_id,
            org_id=request.org_id,
        )
