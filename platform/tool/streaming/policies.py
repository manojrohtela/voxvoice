"""ToolStreamingPolicyEngine (M6.1B-4) — deterministic, AI-free. Mirrors
`reasoning.policies.ReasoningPolicyEngine`'s streaming sub-policy exactly, over
tool chunks instead of response tokens. Verdicts only, no I/O, no side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.policies import InvalidPolicy, TimeoutPolicy

from .domain import Verdict


@dataclass(frozen=True, slots=True)
class StreamingPolicy:
    """Chunk-flow orchestration bounds (never networking)."""

    max_buffered_chunks: int = 256  # backpressure threshold (0 -> unbounded)
    timeout: TimeoutPolicy = field(default_factory=TimeoutPolicy)
    emit_partial: bool = True

    def __post_init__(self) -> None:
        if self.max_buffered_chunks < 0:
            raise InvalidPolicy("max_buffered_chunks must be >= 0")


class ToolStreamingPolicyEngine:
    def __init__(self, policy: StreamingPolicy | None = None) -> None:
        self._p = policy or StreamingPolicy()

    @property
    def policy(self) -> StreamingPolicy:
        return self._p

    def backpressure_ok(self, buffered: int) -> Verdict:
        cap = self._p.max_buffered_chunks
        if cap > 0 and buffered > cap:
            return Verdict(False, f"buffered {buffered} exceeds {cap}")
        return Verdict(True)

    def timeout_exceeded(self, elapsed_s: float) -> bool:
        timeout = self._p.timeout.seconds
        return timeout > 0 and elapsed_s > timeout

    def emit_partial_enabled(self) -> bool:
        return self._p.emit_partial
