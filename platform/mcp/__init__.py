"""mcp — the MCP Runtime (Epic 6, M6.8B-1: Core Foundation).

Per the approved M6.8A design: a new, **additive** closed capability that is the
platform's single owner of client-side Model Context Protocol communication with
external MCP servers. Built on the same closed-runtime shape as Tool/Skill/
Workflow Runtime — manifest/descriptor separation, `core.fsm` lifecycles,
registry/catalog separation, factory composition root.

**M6.8B-1 delivers the core only**: the server model (`MCPServerManifest`/
`MCPServer`/`MCPServerIdentity`/`MCPServerDescriptor`), `MCPServerRegistry` +
`MCPCapabilityRegistry`, the connection and session lifecycles (two `core.fsm`
FSMs) via `MCPConnectionManager`/`MCPSessionManager`, the transport abstraction
with a deterministic `LoopbackTransport` + `EchoMCPServer`, one-time capability
negotiation producing an immutable `MCPCapabilitySnapshot`, config/validator/
factory. **Explicit non-goals (Bundle 2):** request execution, JSON-RPC methods,
authentication, replay, observability, diagnostics, the Tool bridge, streaming,
notifications, resources, prompts, sampling.

`mcp → core` only (plus stdlib; `tenant` reserved for Bundle 2 isolation). The
MCP Runtime imports no sibling runtime, and nothing it composes imports it back.
"""

from .authentication import MCPAuthentication, MCPCredential
from .catalog import MCPCapabilityRegistry
from .config import (
    RUNTIME_VERSION,
    SUPPORTED_INTERFACE_VERSIONS,
    SUPPORTED_PROTOCOL_VERSIONS,
    CollectionsConfig,
    MCPConfiguration,
    StoreConfig,
)
from .connection import MCPConnectionManager
from .credentials import CredentialProvider, StaticCredentialProvider
from .diagnostics import MCPRuntimeDiagnostics, build_diagnostics
from .domain import (
    MCPCapability,
    MCPCapabilityDescriptor,
    MCPCapabilitySnapshot,
    MCPConnection,
    MCPConnectionState,
    MCPHandshakeRequest,
    MCPHandshakeResult,
    MCPRuntimeStatistics,
    MCPServer,
    MCPServerDescriptor,
    MCPServerIdentity,
    MCPServerManifest,
    MCPSession,
    MCPSessionState,
    MCPTransportDescriptor,
    MCPTransportKind,
)
from .errors import (
    MCPAuthenticationError,
    MCPCapabilityError,
    MCPError,
    MCPProtocolError,
    MCPServerError,
    MCPTimeoutError,
    MCPTransportError,
)
from .factory import MCPBuilder, MCPConfigurationValidator, MCPFactory
from .health import MCPHealthMonitor
from .ids import (
    connection_id_for,
    content_hash,
    descriptor_id_for,
    server_id_for,
    session_id_for,
    snapshot_id_for,
)
from .lifecycle import (
    MCP_CONNECTION_LIFECYCLE,
    MCP_SESSION_LIFECYCLE,
    MCPConnectionTrigger,
    MCPSessionTrigger,
    advance_connection,
    advance_session,
)
from .negotiation import negotiate, select_protocol_version
from .observability import (
    InMemoryMCPObservabilitySink,
    MCPEvent,
    MCPEventType,
    MCPMetrics,
    MCPMetricsSnapshot,
)
from .protocol import (
    MCPMethod,
    MCPNotification,
    MCPRequest,
    MCPResponse,
    MCPResponseChunk,
    MCPToolCallResult,
)
from .registry import MCPServerRegistry
from .replay import (
    MCPFrame,
    MCPReplayEngine,
    MCPReplayRecord,
    MCPSessionRecorder,
    ReplayComparison,
)
from .request_executor import MCPRequestExecutor
from .runtime import MCPRuntime
from .session import MCPSessionManager
from .transport import EchoMCPServer, LoopbackTransport, MCPTransport, transport_for
from .transports import (
    HttpSseTransport,
    StdioTransport,
    StreamableHttpTransport,
    parse_sse_stream,
)
from .trust import MCPTrustPolicy
from .validation import validate_manifest

__all__ = [
    "RUNTIME_VERSION",
    # runtime + composition
    "MCPRuntime",
    "MCPFactory",
    "MCPBuilder",
    "MCPConfigurationValidator",
    "MCPServerRegistry",
    "MCPCapabilityRegistry",
    "MCPConnectionManager",
    "MCPSessionManager",
    # domain — server model
    "MCPServerManifest",
    "MCPServer",
    "MCPServerIdentity",
    "MCPServerDescriptor",
    "MCPTransportDescriptor",
    "MCPTransportKind",
    # domain — capabilities
    "MCPCapability",
    "MCPCapabilityDescriptor",
    "MCPCapabilitySnapshot",
    # domain — connection/session
    "MCPConnection",
    "MCPConnectionState",
    "MCPSession",
    "MCPSessionState",
    "MCPHandshakeRequest",
    "MCPHandshakeResult",
    "MCPRuntimeStatistics",
    # transport
    "MCPTransport",
    "LoopbackTransport",
    "EchoMCPServer",
    "transport_for",
    "StdioTransport",
    "HttpSseTransport",
    "StreamableHttpTransport",
    "parse_sse_stream",
    # trust (M6.8.1, F5)
    "MCPTrustPolicy",
    # protocol (M6.8B-2)
    "MCPMethod",
    "MCPRequest",
    "MCPResponse",
    "MCPNotification",
    "MCPResponseChunk",
    "MCPToolCallResult",
    # execution / auth / health (M6.8B-2)
    "MCPRequestExecutor",
    "MCPAuthentication",
    "MCPCredential",
    "CredentialProvider",
    "StaticCredentialProvider",
    "MCPHealthMonitor",
    # replay (M6.8B-2)
    "MCPReplayEngine",
    "MCPReplayRecord",
    "MCPSessionRecorder",
    "MCPFrame",
    "ReplayComparison",
    # observability + diagnostics (M6.8B-2)
    "InMemoryMCPObservabilitySink",
    "MCPEvent",
    "MCPEventType",
    "MCPMetrics",
    "MCPMetricsSnapshot",
    "MCPRuntimeDiagnostics",
    "build_diagnostics",
    # errors (M6.8B-2)
    "MCPError",
    "MCPTransportError",
    "MCPProtocolError",
    "MCPAuthenticationError",
    "MCPTimeoutError",
    "MCPCapabilityError",
    "MCPServerError",
    # negotiation
    "negotiate",
    "select_protocol_version",
    # lifecycle
    "MCP_CONNECTION_LIFECYCLE",
    "MCP_SESSION_LIFECYCLE",
    "MCPConnectionTrigger",
    "MCPSessionTrigger",
    "advance_connection",
    "advance_session",
    # validation + config
    "validate_manifest",
    "MCPConfiguration",
    "StoreConfig",
    "CollectionsConfig",
    "SUPPORTED_INTERFACE_VERSIONS",
    "SUPPORTED_PROTOCOL_VERSIONS",
    # ids
    "content_hash",
    "server_id_for",
    "descriptor_id_for",
    "connection_id_for",
    "session_id_for",
    "snapshot_id_for",
]
