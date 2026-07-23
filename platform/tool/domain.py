"""Tool domain model (M6.1B-1) — immutable value objects.

The vocabulary the Tool Runtime orchestrates, per the approved M6.1A design: an
immutable, contract-only `ToolManifest` (never executable code), the runtime-loaded
`ToolDescriptor` (manifest + lifecycle bookkeeping — a distinct object from the
manifest itself, never conflated), a neutral `ToolRequest`, and the self-sufficient
`ToolResult` downstream consumers read without re-invoking the tool. Everything is
frozen + slotted (deterministic, cheap, replay-friendly). No execution logic, no
vendor/integration specifics, no networking.

Two independent state machines share this module (per the M6.1A design): the
**descriptor lifecycle** (`DescriptorState` — is this tool version usable at all)
and the **execution lifecycle** (`ExecutionState` — how did this one call go). Both
mirror the Provider Runtime's dual-FSM precedent (`ProviderRequestState` /
`ProviderHealthState`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any


class DescriptorState(StrEnum):
    DRAFT = "draft"
    REGISTERED = "registered"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class ExecutionState(StrEnum):
    RECEIVED = "received"
    VALIDATING = "validating"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Classification(StrEnum):
    """Metadata only in this milestone — no behaviour change. The future policy
    engine (M6.1B-2) consumes this to gate approval/rate-limit decisions."""

    READ_ONLY = "read_only"
    READ_WRITE = "read_write"
    DESTRUCTIVE = "destructive"
    INTERNAL = "internal"
    EXTERNAL = "external"
    STREAMING = "streaming"  # reserved (M6.1B-4)
    ASYNC = "async"  # reserved (future)


class Visibility(StrEnum):
    PUBLIC = "public"
    ORG = "org"
    PRIVATE = "private"


@dataclass(frozen=True, slots=True)
class ToolManifest:
    """The versioned, diffable **contract** for one tool — never executable code
    (§7 of the M6.1A design). Origin-namespaced (`internal.*` / `mcp.<server>.*` /
    `org.<org_id>.*`); identity is `(namespace, name, version)`."""

    name: str
    namespace: str = "internal"
    version: str = "1.0.0"
    description: str = ""
    capabilities: frozenset[str] = field(default_factory=frozenset)
    inputs: Mapping[str, Any] = field(default_factory=dict)  # JSON-Schema-shaped contract
    outputs: Mapping[str, Any] = field(default_factory=dict)  # JSON-Schema-shaped contract
    permissions: frozenset[str] = field(default_factory=frozenset)
    classification: Classification = Classification.READ_ONLY
    tags: frozenset[str] = field(default_factory=frozenset)
    visibility: Visibility = Visibility.PUBLIC
    owner: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}.{self.name}"


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    """Loaded runtime metadata: the immutable `ToolManifest` plus registry-assigned
    lifecycle bookkeeping. Deliberately a separate object from the manifest (§8 of
    the design) — the registry replaces this on a state transition, never the
    manifest itself, never in place."""

    manifest: ToolManifest
    state: DescriptorState = DescriptorState.DRAFT
    registered_at: float = 0.0

    @property
    def qualified_name(self) -> str:
        return self.manifest.qualified_name

    @property
    def version(self) -> str:
        return self.manifest.version

    def with_state(self, state: DescriptorState) -> ToolDescriptor:
        return replace(self, state=state)


@dataclass(frozen=True, slots=True)
class ToolRequest:
    """The self-sufficient neutral input to a tool execution. Immutable. Identity +
    routing (`tool`/`namespace`/`version`) + arguments + a minimal inline request
    context (session/trace/org) — a local, tool-scoped shape rather than an import
    of `pipeline.RequestContext` (the pipeline is a downstream composition root that
    will import `tool`, not the reverse; see DL-0044)."""

    request_id: str
    tool: str
    namespace: str = "internal"
    version: str | None = None  # None -> registry resolves the highest ACTIVE version
    arguments: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, str] = field(default_factory=dict)
    session_id: str = ""
    trace_id: str = ""
    org_id: str | None = None
    request_hash: str = ""


@dataclass(frozen=True, slots=True)
class ToolUsage:
    """Normalized execution cost — metered only when an adapter reports it."""

    duration_ms: float = 0.0
    units: int = 0


@dataclass(frozen=True, slots=True)
class ToolAttachment:
    """A reference to binary/file content a tool produced — never inline bytes
    (§12 of the design). Placeholder shape; no storage backend wired yet."""

    uri: str
    content_type: str = ""
    content_hash: str = ""


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """One ordered execution-lifecycle event (metadata only)."""

    stage: str  # received | validating | executing | completed | failed | cancelled
    detail: str = ""
    seq: int = 0
    at: float = 0.0


@dataclass(frozen=True, slots=True)
class ToolExecutionTrace:
    """The ordered execution trace preserved on the result. Metadata only; empty by
    default."""

    events: tuple[TraceEvent, ...] = ()

    def with_event(self, stage: str, *, detail: str = "", at: float = 0.0) -> ToolExecutionTrace:
        ev = TraceEvent(stage=stage, detail=detail, seq=len(self.events), at=at)
        return replace(self, events=(*self.events, ev))


@dataclass(frozen=True, slots=True)
class ToolResult:
    """The execute-once artifact: identity + lifecycle state + output + usage +
    trace + classification + normalized metadata. Immutable and self-sufficient —
    downstream consumers read `success`/`output`/`error` and proceed; they never
    re-invoke the tool."""

    request_id: str
    state: ExecutionState = ExecutionState.RECEIVED
    success: bool = False
    output: Any = None
    error: str | None = None
    classification: Classification = Classification.READ_ONLY
    usage: ToolUsage = field(default_factory=ToolUsage)
    trace: ToolExecutionTrace = field(default_factory=ToolExecutionTrace)
    attachments: tuple[ToolAttachment, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=dict)
    tool: str = ""
    namespace: str = "internal"
    version: str = ""
    request_hash: str = ""
    result_hash: str = ""
    created_at: float = 0.0
    expires_at: float | None = None
    session_id: str = ""
    org_id: str | None = None

    def with_state(self, state: ExecutionState) -> ToolResult:
        return replace(self, state=state)

    def with_expiry(self, expires_at: float | None) -> ToolResult:
        return replace(self, expires_at=expires_at)

    def is_expired(self, now: float) -> bool:
        return self.expires_at is not None and self.expires_at <= now


@dataclass(frozen=True, slots=True)
class ToolStatistics:
    """A point-in-time snapshot: counters + temporary-storage size + registered tools."""

    name: str
    state: str
    healthy: bool
    executions: int = 0
    results: int = 0
    tools: tuple[str, ...] = ()
    metrics: dict[str, object] = field(default_factory=dict)
