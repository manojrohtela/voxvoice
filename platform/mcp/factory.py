"""MCP Runtime composition root (M6.8B-1) — `MCPFactory`, `MCPBuilder`,
`MCPConfigurationValidator`.

`MCPFactory.default()` builds the fully-offline, deterministic runtime (loopback
transport + `EchoMCPServer`, in-memory registry/catalog) — matching every
capability factory's own offline default. Construction is deterministic and
dependency-injected; configuration is validated fail-fast.
"""

from __future__ import annotations

from collections.abc import Callable

from core.errors import ConfigError

from .authentication import MCPAuthentication
from .catalog import MCPCapabilityRegistry
from .config import SUPPORTED_PROTOCOL_VERSIONS, MCPConfiguration
from .connection import MCPConnectionManager
from .credentials import CredentialProvider
from .domain import MCPTransportKind
from .health import MCPHealthMonitor
from .observability import InMemoryMCPObservabilitySink, MCPMetrics
from .registry import MCPServerRegistry
from .replay import MCPReplayEngine
from .runtime import MCPRuntime
from .session import MCPSessionManager


class MCPConfigurationValidator:
    """Validates an `MCPConfiguration` beyond its own `__post_init__` field
    checks — cross-field/profile invariants for the composition root. Raises
    `ConfigError` on any violation (fail fast)."""

    @staticmethod
    def validate(config: MCPConfiguration) -> None:
        # `__post_init__` already validated field-level constraints; re-assert the
        # composition-root invariants explicitly (defense-in-depth, deterministic).
        if config.client_protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
            raise ConfigError(
                f"unsupported client_protocol_version {config.client_protocol_version!r}"
            )
        if config.offline_mode and config.default_transport is not MCPTransportKind.LOOPBACK:
            raise ConfigError("offline_mode requires the loopback transport (Bundle 1)")
        if config.max_connections < 1:
            raise ConfigError("max_connections must be >= 1")


class MCPBuilder:
    """Deterministic construction of the MCP Runtime's own managers. Every part
    depends only on `core` + config — no sibling runtime."""

    def __init__(
        self,
        config: MCPConfiguration | None = None,
        *,
        credential_provider: CredentialProvider | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        import time

        self._config = config or MCPConfiguration.default()
        MCPConfigurationValidator.validate(self._config)
        self._credential_provider = credential_provider
        self._clock = clock or time.time

    def build(self) -> MCPRuntime:
        cfg = self._config
        clock = self._clock
        registry = MCPServerRegistry(clock=clock)
        catalog = MCPCapabilityRegistry()
        connections = MCPConnectionManager(
            offline_mode=cfg.offline_mode, max_connections=cfg.max_connections,
            connect_timeout_s=cfg.connect_timeout_s, request_timeout_s=cfg.request_timeout_s,
            trust=cfg.trust, clock=clock,
        )
        sessions = MCPSessionManager(
            client_protocol_version=cfg.client_protocol_version, clock=clock,
        )
        authentication = MCPAuthentication(self._credential_provider)
        health = MCPHealthMonitor(
            max_reconnect_attempts=cfg.max_reconnect_attempts,
            heartbeat_interval_s=cfg.heartbeat_interval_s, clock=clock,
        )
        return MCPRuntime(
            config=cfg, registry=registry, catalog=catalog,
            connections=connections, sessions=sessions,
            authentication=authentication, health=health,
            observability=InMemoryMCPObservabilitySink(), metrics=MCPMetrics(),
            replay_engine=MCPReplayEngine(), clock=clock,
        )


class MCPFactory:
    @staticmethod
    def create(
        config: MCPConfiguration | None = None,
        *,
        credential_provider: CredentialProvider | None = None,
        clock: Callable[[], float] | None = None,
    ) -> MCPRuntime:
        return MCPBuilder(config, credential_provider=credential_provider, clock=clock).build()

    @staticmethod
    def default(*, clock: Callable[[], float] | None = None) -> MCPRuntime:
        return MCPFactory.create(MCPConfiguration.default(), clock=clock)
