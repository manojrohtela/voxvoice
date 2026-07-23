"""PipelineCancellation (M5.7) — one cancellation token, checked between every stage.

Memory/Knowledge/Context/Prompt have no cancellation-token field on their frozen request
shapes (adding one would be a runtime redesign — out of scope here), so the pipeline
enforces cancellation at its own boundary: `RuntimePipeline` checks `is_cancelled()`
**before** invoking each stage and stops deterministically (no partial re-entry, no
half-run stage) if it is set. Only Reasoning's `InferenceRequest.cancellation_token`
accepts the same token natively, so Reasoning's own (M5.5B-2, opt-in) `CancellationManager`
can additionally act on it mid-stage — a bonus, not a requirement.
"""

from __future__ import annotations


class PipelineCancellation:
    """A single deterministic token. `cancel()` is idempotent; `is_cancelled()` is a pure
    read. No timers, no threads — the caller decides when to call `cancel()`."""

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
