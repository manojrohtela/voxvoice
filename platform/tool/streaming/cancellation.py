"""ToolCancellationToken (M6.1B-4) — a local copy of `pipeline.PipelineCancellation`'s
exact shape (duplicated, not imported: `tool` never imports `pipeline`, the
downstream composition root — DL-0044's precedent, reused here for the same
reason). One deterministic token, checked between every chunk; `cancel()` is
idempotent, `is_cancelled()` is a pure read. No timers, no threads.
"""

from __future__ import annotations


class ToolCancellationToken:
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
