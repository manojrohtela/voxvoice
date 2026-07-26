"""Capability negotiation (M6.8B-1) — the `initialize` handshake → an immutable
capability snapshot.

Per M6.8A §5: negotiation runs once per session; the resulting snapshot is
**fixed for the session's lifetime** (no lazy renegotiation). A reconnect
produces a *new* snapshot. Deterministic given (transport, request).
"""

from __future__ import annotations

from collections.abc import Callable

from core.errors import ConfigError

from .config import SUPPORTED_PROTOCOL_VERSIONS
from .domain import MCPCapabilitySnapshot, MCPHandshakeRequest
from .ids import snapshot_id_for
from .transport import MCPTransport


def select_protocol_version(client_version: str, server_version: str) -> str:
    """Deterministically pick the agreed protocol version: the server's version
    if this client supports it, else fail fast (a protocol mismatch is a
    contract violation, never silently downgraded)."""
    if server_version in SUPPORTED_PROTOCOL_VERSIONS:
        return server_version
    if server_version == client_version:
        return server_version
    raise ConfigError(
        f"no mutually supported MCP protocol version: client supports "
        f"{SUPPORTED_PROTOCOL_VERSIONS}, server offered {server_version!r}"
    )


def negotiate(
    transport: MCPTransport,
    *,
    server_id: str,
    client_protocol_version: str,
    client_name: str = "voxvoice",
    clock: Callable[[], float] | None = None,
) -> MCPCapabilitySnapshot:
    """Run the `initialize` handshake over `transport` and freeze the result into
    an immutable `MCPCapabilitySnapshot`."""
    import time

    the_clock = clock or time.time
    result = transport.handshake(MCPHandshakeRequest(
        client_protocol_version=client_protocol_version,
        client_info={"name": client_name},
    ))
    agreed = select_protocol_version(client_protocol_version, result.server_protocol_version)
    now = the_clock()
    return MCPCapabilitySnapshot(
        snapshot_id=snapshot_id_for(server_id, agreed, now),
        server_id=server_id,
        protocol_version=agreed,
        capabilities=tuple(result.capabilities),
        negotiated_at=now,
    )
