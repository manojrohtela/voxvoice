"""Provider domain model (M5.6B-1) — immutable value objects.

The vocabulary the Provider Runtime orchestrates: a neutral, self-sufficient
`ProviderRequest` (built by a caller — e.g. the future `ProviderModelBridge` composition-root
seam that will implement Reasoning's `ModelProvider` port; this package never imports
`reasoning`), the streamed `ProviderChunk`s, and the immutable, self-sufficient
`ProviderResponse` whose assembled response + usage + metadata downstream consumers use
without re-executing the call. Everything is frozen + slotted (deterministic, cheap,
replay-friendly). No orchestration logic, no vendor specifics, no networking.

Two independent state machines share this module (per the approved M5.6A design): the
**request execution** lifecycle (`ProviderRequestState`) and the **adapter health**
lifecycle (`ProviderHealthState`). Both are defined here in full; the M5.6B-1 core drives
only the deterministic subset that needs no routing/health intelligence (M5.6B-2).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum


class ProviderRequestState(StrEnum):
    REQUESTED = "requested"
    ROUTED = "routed"
    EXECUTING = "executing"
    STREAMING = "streaming"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    FAILED_OVER = "failed_over"  # M5.6B-2 (router-driven); defined now, unused in the core
    EXPIRED = "expired"


class ProviderHealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class ChunkKind(StrEnum):
    TOKEN = "token"  # a streamed output delta
    TOOL_CALL = "tool_call"  # a requested tool invocation (coordination is Reasoning's job)
    FINAL = "final"  # the terminal chunk


class FinishReason(StrEnum):
    STOP = "stop"
    LENGTH = "length"
    TOOL_CALL = "tool_call"
    CANCELLED = "cancelled"
    ERROR = "error"


class Capability(StrEnum):
    """Field names of `ProviderCapabilities` — used by capability-based discovery."""

    CHAT = "chat"
    STREAMING = "streaming"
    FUNCTION_CALLING = "function_calling"
    TOOL_CALLING = "tool_calling"
    VISION = "vision"
    EMBEDDINGS = "embeddings"
    IMAGE_GENERATION = "image_generation"
    AUDIO = "audio"
    JSON_MODE = "json_mode"
    STRUCTURED_OUTPUT = "structured_output"
    THINKING_MODELS = "thinking_models"
    REASONING_TOKENS = "reasoning_tokens"


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """The normalized, provider-neutral capability record every adapter declares (§9 of the
    M5.6A design). Discovery filters on these fields only — never a vendor name."""

    chat: bool = False
    streaming: bool = False
    function_calling: bool = False
    tool_calling: bool = False
    vision: bool = False
    embeddings: bool = False
    image_generation: bool = False
    audio: bool = False
    json_mode: bool = False
    structured_output: bool = False
    thinking_models: bool = False
    reasoning_tokens: bool = False
    context_window: int = 0
    extra: dict[str, bool] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    """Registration metadata for one adapter (§7 of the design). Immutable; `update()` in the
    registry replaces it explicitly (audited), never a silent overwrite."""

    name: str
    version: str = "1"
    region: str | None = None
    cost_class: int = 0  # lower = cheaper; a declared rank, not a live price
    latency_class: int = 0  # lower = faster; a declared rank, not a live measurement
    capabilities: ProviderCapabilities = field(default_factory=ProviderCapabilities)


@dataclass(frozen=True, slots=True)
class ProviderScope:
    """Ownership/isolation keys carried through execution (isolation intelligence is
    M5.6B-2; the core records the scope for downstream)."""

    org_id: str | None = None
    conversation_id: str | None = None
    session_id: str | None = None
    region: str | None = None


@dataclass(frozen=True, slots=True)
class GenerationParameters:
    """Model generation knobs — carried, never interpreted (Provider Runtime runs no
    inference itself; the injected adapter applies them). A neutral copy owned by this
    package (never imports Reasoning's or Prompt's equivalent)."""

    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    stop: tuple[str, ...] = ()
    seed: int | None = None
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderMessage:
    """A provider-neutral message (the shape every `AdapterPort` consumes)."""

    role: str
    content: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderRequestMetadata:
    """Request-side descriptive envelope (request/conversation/runtime metadata holders)."""

    request: dict[str, str] = field(default_factory=dict)
    conversation: dict[str, str] = field(default_factory=dict)
    runtime: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    """The self-sufficient neutral input to a provider execution. Immutable. Everything an
    adapter needs is here, so Provider Runtime never re-queries any layer above it."""

    session_id: str
    messages: tuple[ProviderMessage, ...]
    capability: str = Capability.CHAT.value
    provider: str = ""  # explicit adapter name hint; "" -> registry picks first eligible
    model: str = "default"
    generation: GenerationParameters = field(default_factory=GenerationParameters)
    streaming: bool = True
    timeout_s: float | None = None
    metadata: ProviderRequestMetadata = field(default_factory=ProviderRequestMetadata)
    request_hash: str = ""
    version: str = "1"
    scope: ProviderScope = field(default_factory=ProviderScope)


@dataclass(frozen=True, slots=True)
class ProviderChunk:
    """One streamed piece of the response. Immutable. `done` marks the terminal chunk."""

    id: str
    index: int
    delta: str
    kind: ChunkKind = ChunkKind.TOKEN
    done: bool = False
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StreamingMetadata:
    streamed: bool = False
    chunk_count: int = 0
    first_chunk_index: int = 0


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    """Normalized usage counts. **Never billing** — pricing is a separate downstream
    metering concern (§13 of the design)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    image_units: int = 0
    audio_seconds: float = 0.0
    embedding_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True, slots=True)
class ProviderMetadata:
    """Normalized, future-proofed metadata (`extra` is the escape hatch — never a reason to
    touch this frozen shape for a single vendor's quirk)."""

    provider: str = ""
    model: str = ""
    model_version: str = ""
    latency_ms: float = 0.0
    duration_ms: float = 0.0
    request_id: str = ""
    response_id: str = ""
    region: str | None = None
    organization: str | None = None
    retries: int = 0
    endpoint: str | None = None
    pricing_metadata: dict[str, str] = field(default_factory=dict)
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """One ordered intelligence event in the provider execution trace (metadata only — never
    changes execution). M5.6B-2."""

    stage: str  # provider_selection | routing_decision | policy_verdict | failover |
    #             health_change | execution | completion
    detail: str = ""
    seq: int = 0
    at: float = 0.0


@dataclass(frozen=True, slots=True)
class ProviderExecutionTrace:
    """The ordered intelligence trace preserved on the response (provider selection → routing
    decision → policy verdict → failover → health change → execution → completion). Metadata
    only; empty by default so the M5.6B-1 core path stays byte-for-byte unchanged. M5.6B-2."""

    events: tuple[TraceEvent, ...] = ()

    def with_event(
        self, stage: str, *, detail: str = "", at: float = 0.0
    ) -> ProviderExecutionTrace:
        ev = TraceEvent(stage=stage, detail=detail, seq=len(self.events), at=at)
        return replace(self, events=(*self.events, ev))


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    """The execute-once artifact: identity + lifecycle state + the assembled text + the
    streamed chunks + usage + finish reason + normalized metadata. Immutable and
    **self-sufficient** — downstream consumers read `text` + `usage` + `finish_reason` and
    proceed; they never re-execute the call."""

    id: str
    session_id: str
    scope: ProviderScope
    state: ProviderRequestState
    text: str = ""
    chunks: tuple[ProviderChunk, ...] = ()
    usage: ProviderUsage = field(default_factory=ProviderUsage)
    finish_reason: FinishReason = FinishReason.STOP
    streaming: StreamingMetadata = field(default_factory=StreamingMetadata)
    timing: dict[str, float] = field(default_factory=dict)
    metadata: ProviderMetadata = field(default_factory=ProviderMetadata)
    provider: str = "echo"
    model: str = "default"
    request_hash: str = ""
    response_hash: str = ""
    response_version: str = "1"
    created_at: float = 0.0
    expires_at: float | None = None
    org_id: str | None = None
    conversation_id: str | None = None
    # ── M5.6B-2 additive: intelligence trace (empty by default; core-unchanged) ──
    trace: ProviderExecutionTrace = field(default_factory=ProviderExecutionTrace)

    # ── pure derivations (return a new response; never mutate) ────────────────
    def with_state(self, state: ProviderRequestState) -> ProviderResponse:
        return replace(self, state=state)

    def with_expiry(self, expires_at: float | None) -> ProviderResponse:
        return replace(self, expires_at=expires_at)

    def is_expired(self, now: float) -> bool:
        return self.expires_at is not None and self.expires_at <= now


@dataclass(frozen=True, slots=True)
class ProviderSession:
    """A per-conversation working set: the ids of responses produced this session (temporary
    history) + session metadata. Immutable snapshot; responses live in the temporary
    repository, not here."""

    session_id: str
    scope: ProviderScope = field(default_factory=ProviderScope)
    response_ids: tuple[str, ...] = ()
    updated_at: float = 0.0
    metadata: dict[str, str] = field(default_factory=dict)

    def record_response(self, response_id: str, *, now: float | None = None) -> ProviderSession:
        ids = (*self.response_ids, response_id)
        ts = now if now is not None else self.updated_at
        return replace(self, response_ids=ids, updated_at=ts)


@dataclass(frozen=True, slots=True)
class ProviderRegistryEntry:
    """One registered adapter's descriptor + current health state. Immutable; the registry
    replaces the entry on a state transition (never mutates in place)."""

    descriptor: ProviderDescriptor
    health: ProviderHealthState = ProviderHealthState.HEALTHY

    def with_health(self, health: ProviderHealthState) -> ProviderRegistryEntry:
        return replace(self, health=health)


@dataclass(frozen=True, slots=True)
class ProviderExecutionResult:
    """The rich result the runtime returns from the internal execute path: the response plus
    a terminal status + reason."""

    response: ProviderResponse
    delivered: bool
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ProviderStatistics:
    """A point-in-time snapshot: health + counters + temporary-storage size + providers."""

    name: str
    state: str
    healthy: bool
    requests: int = 0
    sessions: int = 0
    provider: str = "echo"
    providers: tuple[str, ...] = ()
    metrics: dict[str, object] = field(default_factory=dict)
