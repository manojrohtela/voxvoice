"""Reasoning domain model (M5.5B-1) — immutable value objects.

The vocabulary the Reasoning Runtime orchestrates: a neutral, self-sufficient
`InferenceRequest` (projected from a `PromptPackage` by a composition-root bridge — Reasoning
never imports `prompt`), the streamed `ResponseChunk`s, and the immutable, self-sufficient
`ResponsePackage` whose assembled response + usage + lineage the future Agent/Workflow
runtimes consume without re-running inference. Everything is frozen + slotted (deterministic,
cheap, replay-friendly). No orchestration logic, no provider specifics, no networking.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum


class InferenceState(StrEnum):
    REQUESTED = "requested"
    STARTED = "started"
    STREAMING = "streaming"
    TOOL_WAIT = "tool_wait"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ChunkKind(StrEnum):
    TOKEN = "token"  # a streamed output delta
    TOOL_CALL = "tool_call"  # a requested tool invocation (coordination is M5.5B-2)
    FINAL = "final"  # the terminal chunk


class FinishReason(StrEnum):
    STOP = "stop"
    LENGTH = "length"
    TOOL_CALL = "tool_call"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ReasoningScope:
    """Ownership/isolation keys carried through inference (isolation is M5.5B-2; the core
    records the scope for downstream)."""

    org_id: str | None = None
    conversation_id: str | None = None
    session_id: str | None = None
    user_id: str | None = None


@dataclass(frozen=True, slots=True)
class GenerationParameters:
    """Model generation knobs — carried, never interpreted (Reasoning runs no inference; the
    injected provider applies them). A neutral copy owned by Reasoning (never imports the
    Prompt Runtime's type)."""

    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    stop: tuple[str, ...] = ()
    seed: int | None = None
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InferenceMessage:
    """A provider-neutral message (the shape the injected `ModelProvider` consumes) — the
    projection of a rendered `PromptMessage`. `role` is a neutral string."""

    role: str
    content: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReasoningMetadata:
    """Request-side descriptive envelope (request/conversation/runtime metadata holders)."""

    request: dict[str, str] = field(default_factory=dict)
    conversation: dict[str, str] = field(default_factory=dict)
    runtime: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InferenceRequest:
    """The self-sufficient neutral input to the model-call loop. Immutable. Built by the
    composition-root `PromptConsumer` bridge from a `PromptPackage`; everything the loop needs
    is here, so Reasoning never re-queries Prompt/Context/Memory/Knowledge."""

    session_id: str
    messages: tuple[InferenceMessage, ...]
    provider: str = "echo"
    model: str = "default"
    generation: GenerationParameters = field(default_factory=GenerationParameters)
    prompt_hash: str = ""
    prompt_fingerprint: str = ""
    streaming: bool = True
    timeout_s: float | None = None
    metadata: ReasoningMetadata = field(default_factory=ReasoningMetadata)
    cancellation_token: str | None = None
    request_hash: str = ""
    version: str = "1"
    scope: ReasoningScope = field(default_factory=ReasoningScope)


@dataclass(frozen=True, slots=True)
class ResponseChunk:
    """One streamed piece of the response. Immutable. `done` marks the terminal chunk."""

    id: str
    index: int
    delta: str
    kind: ChunkKind = ChunkKind.TOKEN
    done: bool = False
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True, slots=True)
class StreamingMetadata:
    streamed: bool = False
    chunk_count: int = 0
    first_chunk_index: int = 0


@dataclass(frozen=True, slots=True)
class RetryRecord:
    """A retry attempt record (retry orchestration is M5.5B-2; the core never retries — the
    tuple is empty, but the shape is defined so the package is stable)."""

    attempt: int
    reason: str
    delay_s: float = 0.0
    at: float = 0.0


@dataclass(frozen=True, slots=True)
class CancellationRecord:
    """A cancellation record (cancellation orchestration is M5.5B-2)."""

    reason: str
    at: float = 0.0


@dataclass(frozen=True, slots=True)
class ResponseMetadata:
    """Provider/model/completion detail containers (opaque, provider-supplied later)."""

    provider_metadata: dict[str, str] = field(default_factory=dict)
    model_metadata: dict[str, str] = field(default_factory=dict)
    completion_metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReasoningLineage:
    """The reasoning history preserved on every response — so no downstream runtime loses it.
    Retry/tool histories are populated by the M5.5B-2 engines; empty in the core."""

    prompt_hash: str = ""
    prompt_fingerprint: str = ""
    inference_request_hash: str = ""
    provider: str = ""
    model: str = ""
    retry_history: tuple[RetryRecord, ...] = ()
    tool_history: tuple[dict[str, str], ...] = ()
    streaming: StreamingMetadata = field(default_factory=StreamingMetadata)
    transformation: str = "inference"
    response_hash: str = ""


@dataclass(frozen=True, slots=True)
class ResponsePackage:
    """The reason-once artifact: identity + lifecycle state + the assembled text + the streamed
    chunks + usage + finish reason + full lineage. Immutable and **self-sufficient** —
    Agent/Workflow read `text` + `usage` + `finish_reason` and proceed; they never re-run
    inference."""

    id: str
    session_id: str
    scope: ReasoningScope
    state: InferenceState
    text: str = ""
    chunks: tuple[ResponseChunk, ...] = ()
    tool_decisions: tuple[dict[str, str], ...] = ()  # populated in M5.5B-2
    usage: TokenUsage = field(default_factory=TokenUsage)
    finish_reason: FinishReason = FinishReason.STOP
    streaming: StreamingMetadata = field(default_factory=StreamingMetadata)
    retry: tuple[RetryRecord, ...] = ()
    cancellation: CancellationRecord | None = None
    timing: dict[str, float] = field(default_factory=dict)
    metadata: ResponseMetadata = field(default_factory=ResponseMetadata)
    lineage: ReasoningLineage = field(default_factory=ReasoningLineage)
    quality: dict[str, float] = field(default_factory=dict)
    provider: str = "echo"
    model: str = "default"
    request_hash: str = ""
    response_hash: str = ""
    response_version: str = "1"
    created_at: float = 0.0
    expires_at: float | None = None
    org_id: str | None = None
    conversation_id: str | None = None

    # ── pure derivations (return a new package; never mutate) ─────────────────
    def with_state(self, state: InferenceState) -> ResponsePackage:
        return replace(self, state=state)

    def with_expiry(self, expires_at: float | None) -> ResponsePackage:
        return replace(self, expires_at=expires_at)

    def is_expired(self, now: float) -> bool:
        return self.expires_at is not None and self.expires_at <= now


@dataclass(frozen=True, slots=True)
class ReasoningSession:
    """A per-conversation working set: the ids of response packages produced this session
    (temporary history) + session metadata. Immutable snapshot; packages live in the
    temporary repository, not here."""

    session_id: str
    scope: ReasoningScope = field(default_factory=ReasoningScope)
    response_ids: tuple[str, ...] = ()
    updated_at: float = 0.0
    metadata: dict[str, str] = field(default_factory=dict)

    def record_response(self, response_id: str, *, now: float | None = None) -> ReasoningSession:
        ids = (*self.response_ids, response_id)
        ts = now if now is not None else self.updated_at
        return replace(self, response_ids=ids, updated_at=ts)


@dataclass(frozen=True, slots=True)
class InferenceResponse:
    """The rich result the runtime returns from the internal infer path: the package plus a
    terminal status + reason."""

    package: ResponsePackage
    delivered: bool
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ReasoningStatistics:
    """A point-in-time snapshot: health + counters + temporary-storage size + provider."""

    name: str
    state: str
    healthy: bool
    responses: int = 0
    sessions: int = 0
    provider: str = "echo"
    providers: tuple[str, ...] = ()
    metrics: dict[str, object] = field(default_factory=dict)
