"""WorkflowCancellationToken (M6.4B-2) — a local duplicate of
`pipeline.PipelineCancellation`'s shape (not imported — the established
"duplicate, don't cross-import" precedent every closed runtime in this
repository already follows for this exact primitive; Tool Runtime's own
streaming bundle did the same). A single deterministic token: `cancel()` is
idempotent, `is_cancelled()` is a pure read. No timers, no threads -- the
caller decides when to call `cancel()`. Checked between every node boundary
by `WorkflowExecutionEngine`; cancellation always reaches a terminal state
(`CANCELLED`), never orphaned.
"""

from __future__ import annotations


class WorkflowCancellationToken:
    def __init__(self, token: str) -> None:
        self._token = token
        self._cancelled = False
        self._reason = ""

    @property
    def token(self) -> str:
        return self._token

    def cancel(self, *, reason: str = "manual") -> None:
        self._cancelled = True
        self._reason = reason

    def is_cancelled(self) -> bool:
        return self._cancelled

    @property
    def reason(self) -> str:
        return self._reason
