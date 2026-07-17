"""Transport adapters — WebSocket + SSE (M4.2).

Both transports consume **exactly the same** serialized `EventEnvelope` (produced by
the `EventSerializer`); they differ only in **wire framing**. Each adapter drains one
`ClientSession`'s `EventStream` and writes framed payloads to a transport-agnostic
`TransportConnection` (any object with async `send(str)` / `close()`), so the
adapters are independent of each other and of the network layer — a real ASGI
WebSocket/SSE response, or an in-memory test double, plugs in identically. Future
transports (gRPC, HTTP streaming, MQTT) add a new adapter here with no change to the
gateway, the bus, or the Runtime.

A per-connection send failure (network error) ends the drain loop and closes the
connection; it is reported via the optional `on_close` hook so the gateway can drop
the client. It never propagates to the Runtime.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from .session import ClientSession


@runtime_checkable
class TransportConnection(Protocol):
    async def send(self, data: str) -> None: ...
    async def close(self) -> None: ...


class _Transport:
    """Shared drain loop; subclasses supply the wire framing + heartbeat."""

    name = "transport"

    def frame(self, payload: str) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def heartbeat_payload(self) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    async def run(
        self,
        session: ClientSession,
        conn: TransportConnection,
        *,
        on_close: Callable[[str], None] | None = None,
    ) -> None:
        """Stream this client's events to `conn` until the stream closes or a send
        fails. Ordered, exactly-once (whatever the stream buffered). Never raises."""
        reason = "stream_closed"
        try:
            async for payload in session.stream:
                await conn.send(self.frame(payload))
        except Exception:  # noqa: BLE001 — network error isolated to this connection
            reason = "network_error"
        finally:
            try:
                await conn.close()
            except Exception:  # noqa: BLE001
                pass
            if on_close is not None:
                on_close(reason)

    async def heartbeat(self, conn: TransportConnection) -> None:
        """Send one keep-alive frame (ping / comment). The transport's own timer
        drives cadence; this is the payload."""
        await conn.send(self.heartbeat_payload())


class WebSocketTransport(_Transport):
    """Sends each serialized envelope as a WebSocket text message (unframed JSON)."""

    name = "websocket"

    def frame(self, payload: str) -> str:
        return payload

    def heartbeat_payload(self) -> str:
        return '{"type":"ping"}'


class SSETransport(_Transport):
    """Frames each serialized envelope as a Server-Sent Event. A monotonic `id:`
    line supports `Last-Event-ID` reconnect; `: ping` comments are heartbeats."""

    name = "sse"

    def __init__(self) -> None:
        self._counter = 0

    def frame(self, payload: str) -> str:
        self._counter += 1
        return f"id: {self._counter}\ndata: {payload}\n\n"

    def heartbeat_payload(self) -> str:
        return ": ping\n\n"
