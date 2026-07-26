"""MCP Runtime configuration (M6.8B-1) — frozen, validated value objects.

Mirrors `tool.config`/`skill.config` shape. Validated at construction (fail
fast). `default()` yields the fully-offline development config (loopback
transport, in-memory stores). No credentials live here (auth config is Bundle 2).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from core.errors import ConfigError

from .domain import MCPTransportKind
from .trust import MCPTrustPolicy

RUNTIME_VERSION = "6.8.0"
SUPPORTED_INTERFACE_VERSIONS = frozenset({"1.0"})
#: MCP protocol versions this runtime can negotiate. The loopback EchoMCPServer
#: advertises the newest; negotiation picks the highest mutually supported.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18",)


@dataclass(frozen=True, slots=True)
class StoreConfig:
    kind: str = "in_memory"
    options: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CollectionsConfig:
    servers: str = "mcp_servers"


@dataclass(frozen=True, slots=True)
class MCPConfiguration:
    name: str = "mcp"
    document_store: StoreConfig = field(default_factory=StoreConfig)
    collections: CollectionsConfig = field(default_factory=CollectionsConfig)
    default_namespace: str = "external"
    default_transport: MCPTransportKind = MCPTransportKind.LOOPBACK
    offline_mode: bool = True                 # Bundle 1: only loopback is available
    client_protocol_version: str = SUPPORTED_PROTOCOL_VERSIONS[0]
    connect_timeout_s: float = 10.0
    request_timeout_s: float = 30.0           # honoured by Bundle 2's executor
    heartbeat_interval_s: float = 30.0        # honoured by Bundle 2's health monitor
    max_reconnect_attempts: int = 3
    max_connections: int = 64
    #: Trust policy for real (non-loopback) transports (M6.8.1, F5). `None` in the
    #: offline default (loopback needs no trust); required in practice before a
    #: real transport is used — a real transport with no policy verifies TLS via
    #: the transport's own explicit default context but performs no allow-listing.
    trust: MCPTrustPolicy | None = None

    def __post_init__(self) -> None:
        if not self.default_namespace:
            raise ConfigError("default_namespace must be non-empty")
        if not self.client_protocol_version:
            raise ConfigError("client_protocol_version must be non-empty")
        if self.client_protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
            raise ConfigError(
                f"unsupported client_protocol_version {self.client_protocol_version!r}; "
                f"supported: {SUPPORTED_PROTOCOL_VERSIONS}"
            )
        if self.connect_timeout_s <= 0:
            raise ConfigError("connect_timeout_s must be > 0")
        if self.request_timeout_s <= 0:
            raise ConfigError("request_timeout_s must be > 0")
        if self.heartbeat_interval_s <= 0:
            raise ConfigError("heartbeat_interval_s must be > 0")
        if self.max_reconnect_attempts < 0:
            raise ConfigError("max_reconnect_attempts must be >= 0")
        if self.max_connections < 1:
            raise ConfigError("max_connections must be >= 1")
        if self.offline_mode and self.default_transport is not MCPTransportKind.LOOPBACK:
            raise ConfigError("offline_mode requires default_transport = loopback (Bundle 1)")
        if (
            not self.offline_mode
            and self.default_transport is not MCPTransportKind.LOOPBACK
            and self.trust is None
        ):
            raise ConfigError(
                "a non-offline real transport requires an explicit trust policy "
                "(M6.8.1 F5); set MCPConfiguration.trust"
            )

    @classmethod
    def default(cls) -> MCPConfiguration:
        return cls()
