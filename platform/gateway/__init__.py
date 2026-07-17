"""gateway — the Live Event Gateway (Constitution §A5; ADR-0004/0006, M4.2).

Streams Internal Event Bus events to external clients over pluggable transports
(WebSocket, SSE) that all consume the **same** `core.events.Event` envelope. It owns
external delivery only — never Runtime state, never event creation. It subscribes to
the bus through the public `core.events.EventBus` contract, so the **Runtime never
knows a gateway, client, or transport exists** and continues publishing exactly as
M4.1.

Edge layer: imports `core` (the envelope + bus contract) and the stdlib — **never**
`runtime` or `api`. A future `api` layer mounts the transports onto ASGI routes; the
gateway core stays transport- and network-independent. Authentication/authorization/
rate-limiting/RBAC/audit/tenant-isolation are **extension points only** (M4.2
non-goals). In-process delivery only — no durable queue/broker (M4.3+).
"""

from .connection import ConnectionManager
from .errors import (
    DuplicateSubscription,
    GatewayError,
    GatewayShutdown,
    InvalidSubscription,
    UnknownClient,
)
from .gateway import LiveEventGateway
from .metrics import GatewayMetrics
from .scope import EventChannel, Scope, ScopeKind
from .serialization import SCHEMA_VERSION, EventSerializer
from .session import ClientSession, ConnectionState
from .stream import EventStream, OfferResult, OverflowPolicy
from .subscription import SubscriptionManager
from .transports import SSETransport, TransportConnection, WebSocketTransport

__all__ = [
    "LiveEventGateway",
    "ConnectionManager",
    "SubscriptionManager",
    "EventChannel",
    "Scope",
    "ScopeKind",
    "EventSerializer",
    "SCHEMA_VERSION",
    "EventStream",
    "OverflowPolicy",
    "OfferResult",
    "ClientSession",
    "ConnectionState",
    "GatewayMetrics",
    "WebSocketTransport",
    "SSETransport",
    "TransportConnection",
    "GatewayError",
    "InvalidSubscription",
    "DuplicateSubscription",
    "UnknownClient",
    "GatewayShutdown",
]
