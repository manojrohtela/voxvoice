"""Reasoning Policy Engine (M5.5B-2) — deterministic, AI-free.

Reasoning-specific policy value objects + a `ReasoningPolicyEngine` that turns them into
**verdicts** the runtime/engines execute. The engine performs no I/O and no side effects: it
decides, the caller acts.

Policies (all seven from the milestone): Reasoning, Retry, Streaming, Tool, Provider,
Cancellation, Safety. The `ReasoningPolicies` bundle is the single config the factory threads
into the engines; each engine reads its own sub-policy so the surface stays in one place and
is validated once. Retry/Timeout reuse the shared `core.policies` value objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.policies import InvalidPolicy, RetryPolicy, TimeoutPolicy


# ── policy value objects ──────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class ReasoningPolicy:
    """Top-level orchestration policy."""

    default_streaming: bool = True
    max_tool_iterations: int = 4  # tool-loop bound (coordination only)

    def __post_init__(self) -> None:
        if self.max_tool_iterations < 0:
            raise InvalidPolicy("max_tool_iterations must be >= 0")


@dataclass(frozen=True, slots=True)
class RetryControlPolicy:
    """Provider/tool retry budget + deterministic backoff (wraps core `RetryPolicy`)."""

    backoff: RetryPolicy = field(default_factory=RetryPolicy)
    retry_on: frozenset[str] = frozenset({"provider", "tool"})
    circuit_breaker_threshold: int = 0  # 0 → disabled

    def __post_init__(self) -> None:
        if self.circuit_breaker_threshold < 0:
            raise InvalidPolicy("circuit_breaker_threshold must be >= 0")


@dataclass(frozen=True, slots=True)
class StreamingPolicy:
    """Chunk-flow orchestration bounds (never networking)."""

    max_buffered_chunks: int = 256  # backpressure threshold (0 → unbounded)
    timeout: TimeoutPolicy = field(default_factory=TimeoutPolicy)
    emit_partial: bool = True

    def __post_init__(self) -> None:
        if self.max_buffered_chunks < 0:
            raise InvalidPolicy("max_buffered_chunks must be >= 0")


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    """Tool-coordination guards (approval + allow-list)."""

    require_approval: bool = False
    allowed_tools: frozenset[str] = frozenset()  # empty → all
    timeout: TimeoutPolicy = field(default_factory=TimeoutPolicy)


@dataclass(frozen=True, slots=True)
class ProviderPolicy:
    """Which providers are admissible (empty → all)."""

    allowed_providers: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class CancellationPolicy:
    """Cancellation orchestration knobs."""

    honor_timeout: bool = True
    propagate_to_tools: bool = True


@dataclass(frozen=True, slots=True)
class SafetyPolicy:
    enabled: bool = False  # a SafetyValidator hook gates content when on (interface only)


@dataclass(frozen=True, slots=True)
class ReasoningPolicies:
    """The full deterministic policy bundle for a runtime."""

    reasoning: ReasoningPolicy = field(default_factory=ReasoningPolicy)
    retry: RetryControlPolicy = field(default_factory=RetryControlPolicy)
    streaming: StreamingPolicy = field(default_factory=StreamingPolicy)
    tool: ToolPolicy = field(default_factory=ToolPolicy)
    provider: ProviderPolicy = field(default_factory=ProviderPolicy)
    cancellation: CancellationPolicy = field(default_factory=CancellationPolicy)
    safety: SafetyPolicy = field(default_factory=SafetyPolicy)


# ── verdicts ────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Verdict:
    ok: bool
    reason: str = ""


# ── the engine ────────────────────────────────────────────────────────────────
class ReasoningPolicyEngine:
    """Holds the policy bundle and produces **verdicts** (no I/O, no side effects). The retry
    controller reads `retry`; the streaming controller reads `streaming`; the tool coordinator
    reads `tool`; tenancy/runtime read `provider`."""

    def __init__(self, policies: ReasoningPolicies | None = None) -> None:
        self._p = policies or ReasoningPolicies()

    @property
    def policies(self) -> ReasoningPolicies:
        return self._p

    def admit_provider(self, name: str) -> Verdict:
        allowed = self._p.provider.allowed_providers
        if allowed and name not in allowed:
            return Verdict(False, f"provider {name!r} not allowed")
        return Verdict(True)

    def admit_tool(self, name: str) -> Verdict:
        allowed = self._p.tool.allowed_tools
        if allowed and name not in allowed:
            return Verdict(False, f"tool {name!r} not allowed")
        if self._p.tool.require_approval:
            return Verdict(False, "approval required")
        return Verdict(True)

    def should_retry(self, kind: str, attempt: int) -> Verdict:
        if kind not in self._p.retry.retry_on:
            return Verdict(False, f"{kind} not retryable")
        if attempt >= self._p.retry.backoff.max_attempts:
            return Verdict(False, "retry budget exhausted")
        return Verdict(True)

    def backpressure_ok(self, buffered: int) -> Verdict:
        cap = self._p.streaming.max_buffered_chunks
        if cap > 0 and buffered > cap:
            return Verdict(False, f"buffered {buffered} exceeds {cap}")
        return Verdict(True)

    def safety_enabled(self) -> bool:
        return self._p.safety.enabled
