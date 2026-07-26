"""MCP Runtime domain models (M6.8B-1) — deterministic, immutable value objects.

Per the approved M6.8A design: the MCP Runtime is a closed, additive Epic 6
capability owning the client-side MCP protocol lifecycle. Bundle 1 defines the
**server model, connection/session state, transport descriptor, and negotiated
capability snapshot** — no request execution, no JSON-RPC methods, no auth, no
replay/observability (all Bundle 2). Every shape here is a frozen dataclass; the
two lifecycle FSMs (`lifecycle.py`) drive `MCPConnection`/`MCPSession` state.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any


# ── lifecycle state enums (driven by lifecycle.py FSMs) ─────────────────────
class MCPConnectionState(StrEnum):
    REGISTERED = "registered"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    DISCONNECTED = "disconnected"
    CLOSED = "closed"


class MCPSessionState(StrEnum):
    CREATED = "created"
    INITIALIZING = "initializing"
    NEGOTIATING = "negotiating"
    READY = "ready"
    CLOSING = "closing"
    CLOSED = "closed"


class MCPTransportKind(StrEnum):
    LOOPBACK = "loopback"          # deterministic, offline (Bundle 1 default)
    STDIO = "stdio"               # declared-only until Bundle 2
    HTTP_SSE = "http_sse"         # declared-only until Bundle 2
    STREAMABLE_HTTP = "streamable_http"  # declared-only until Bundle 2


# ── transport + server contract ─────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class MCPTransportDescriptor:
    """How to reach a server. `endpoint` is opaque (a command line for stdio, a
    URL for http); Bundle 1 interprets only `loopback`."""

    kind: MCPTransportKind = MCPTransportKind.LOOPBACK
    endpoint: str = ""
    options: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MCPServerManifest:
    """The immutable, versioned contract a caller registers — never executable
    code, never credentials (auth is declared as a *mode* only; the handshake is
    Bundle 2). `declared_capabilities` are the server's advertised capability
    kinds as documented by its operator; the *negotiated* set is discovered live
    (`MCPCapabilitySnapshot`)."""

    id: str
    namespace: str = "external"
    version: str = "1.0.0"
    interface_version: str = "1.0"
    display_name: str = ""
    transport: MCPTransportDescriptor = field(default_factory=MCPTransportDescriptor)
    # declared only in Bundle 1 (none|oauth2|token|mtls); the handshake is Bundle 2
    auth_mode: str = "none"
    declared_capabilities: frozenset[str] = field(default_factory=frozenset)
    tags: frozenset[str] = field(default_factory=frozenset)
    metadata: Mapping[str, str] = field(default_factory=dict)

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}.{self.id}"


@dataclass(frozen=True, slots=True)
class MCPServerIdentity:
    """The content identity of a registered server version."""

    server_id: str
    namespace: str
    id: str
    version: str
    display_name: str = ""

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}.{self.id}"


@dataclass(frozen=True, slots=True)
class MCPServer:
    """The resolved server value the registry stores/returns: identity + its
    immutable manifest."""

    identity: MCPServerIdentity
    manifest: MCPServerManifest

    @property
    def qualified_name(self) -> str:
        return self.identity.qualified_name

    @property
    def version(self) -> str:
        return self.identity.version


@dataclass(frozen=True, slots=True)
class MCPServerDescriptor:
    """A registry entry: a registered `MCPServer` plus when it was registered.
    Immutable — registration is durable-for-process (mirrors `ToolRegistry`);
    the *connection* FSM lives on `MCPConnection`, not here."""

    descriptor_id: str
    server: MCPServer
    registered_at: float = 0.0

    @property
    def qualified_name(self) -> str:
        return self.server.qualified_name

    @property
    def version(self) -> str:
        return self.server.version


# ── capabilities ────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class MCPCapability:
    """One advertised capability. `kind` is an MCP capability category
    (`tools`/`resources`/`prompts`/`sampling`); `name` optionally scopes it."""

    kind: str
    name: str = ""


@dataclass(frozen=True, slots=True)
class MCPCapabilityDescriptor:
    """A negotiated capability with provenance — the catalog entry form."""

    capability: MCPCapability
    server_id: str
    protocol_version: str
    negotiated_at: float = 0.0


@dataclass(frozen=True, slots=True)
class MCPCapabilitySnapshot:
    """The immutable set of capabilities negotiated for **one session**. Fixed
    for the session's lifetime (no lazy renegotiation, M6.8A §5); a reconnect
    produces a *new* snapshot."""

    snapshot_id: str
    server_id: str
    protocol_version: str
    capabilities: tuple[MCPCapability, ...] = ()
    negotiated_at: float = 0.0

    def has(self, kind: str) -> bool:
        return any(c.kind == kind for c in self.capabilities)

    @property
    def kinds(self) -> frozenset[str]:
        return frozenset(c.kind for c in self.capabilities)


# ── connection + session (FSM-driven) ───────────────────────────────────────
@dataclass(frozen=True, slots=True)
class MCPConnection:
    """One transport connection to a server. `state` is driven by the connection
    FSM (`lifecycle.py`); this shape carries no transport logic."""

    connection_id: str
    server_id: str
    transport_kind: MCPTransportKind
    state: MCPConnectionState = MCPConnectionState.REGISTERED
    attempts: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0

    def with_state(self, state: MCPConnectionState, *, updated_at: float) -> MCPConnection:
        return replace(self, state=state, updated_at=updated_at)

    def with_attempt(self, *, updated_at: float) -> MCPConnection:
        return replace(self, attempts=self.attempts + 1, updated_at=updated_at)


@dataclass(frozen=True, slots=True)
class MCPSession:
    """One protocol session over a connection. `state` is driven by the session
    FSM; `snapshot` holds the immutable negotiated capability set once READY."""

    session_id: str
    connection_id: str
    server_id: str
    state: MCPSessionState = MCPSessionState.CREATED
    protocol_version: str = ""
    snapshot: MCPCapabilitySnapshot | None = None
    created_at: float = 0.0
    updated_at: float = 0.0

    def with_state(self, state: MCPSessionState, *, updated_at: float) -> MCPSession:
        return replace(self, state=state, updated_at=updated_at)

    def with_negotiation(
        self, snapshot: MCPCapabilitySnapshot, *, updated_at: float
    ) -> MCPSession:
        return replace(
            self, snapshot=snapshot, protocol_version=snapshot.protocol_version,
            updated_at=updated_at,
        )


# ── handshake shapes (negotiation only; not general request execution) ──────
@dataclass(frozen=True, slots=True)
class MCPHandshakeRequest:
    """The `initialize` handshake a client sends — Bundle 1 exchanges only this,
    never a `tools/call` or any other method (those are Bundle 2)."""

    client_protocol_version: str
    client_info: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MCPHandshakeResult:
    """The server's `initialize` response: its protocol version + advertised
    capabilities. Opaque beyond these fields."""

    server_protocol_version: str
    capabilities: tuple[MCPCapability, ...] = ()
    server_info: Mapping[str, str] = field(default_factory=dict)


# ── statistics ──────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class MCPRuntimeStatistics:
    """A point-in-time snapshot: counters + registered servers + live
    connections/sessions."""

    name: str
    state: str
    healthy: bool
    servers: tuple[str, ...] = ()
    connections: int = 0
    sessions: int = 0
    metrics: Mapping[str, Any] = field(default_factory=dict)
