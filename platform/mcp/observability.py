"""MCPObservability (M6.8B-2) — MCP-local metrics + protocol events.

Ownership stays **local to MCP**; Platform Integration correlates by `trace_id`
only (never a shared bus emission required here). MCP records its own
`mcp.*` events and metrics; no capability metric is duplicated. The event
vocabulary is a local `MCPEventType` (the six protocol events the milestone
names), carried with the shared `trace_id` so a future correlator can group a
platform-wide execution.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class MCPEventType(StrEnum):
    CONNECT = "mcp.connect"
    DISCONNECT = "mcp.disconnect"
    REQUEST = "mcp.request"
    RESPONSE = "mcp.response"
    RETRY = "mcp.retry"
    FAILURE = "mcp.failure"


@dataclass(frozen=True, slots=True)
class MCPEvent:
    type: MCPEventType
    server_id: str = ""
    session_id: str = ""
    trace_id: str = ""
    at: float = 0.0
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MCPConnectionTrace:
    server_id: str
    connection_id: str
    trace_id: str = ""
    connect_latency_ms: float = 0.0
    negotiation_latency_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class MCPRequestTrace:
    server_id: str
    session_id: str
    method: str
    trace_id: str = ""
    latency_ms: float = 0.0
    ok: bool = True


class InMemoryMCPObservabilitySink:
    """A local, read-only-after-write event sink (mirrors Hook/Workflow's own
    in-memory sinks). Never touches another capability's telemetry."""

    def __init__(self) -> None:
        self._events: list[MCPEvent] = []

    def record(self, event: MCPEvent) -> None:
        self._events.append(event)

    def events(self) -> tuple[MCPEvent, ...]:
        return tuple(self._events)

    def by_trace(self, trace_id: str) -> tuple[MCPEvent, ...]:
        return tuple(e for e in self._events if e.trace_id == trace_id)

    def by_type(self, type_: MCPEventType) -> tuple[MCPEvent, ...]:
        return tuple(e for e in self._events if e.type is type_)


@dataclass(frozen=True, slots=True)
class MCPMetricsSnapshot:
    """A point-in-time metrics view — MCP-local counters/latencies only."""

    request_count: int = 0
    failure_count: int = 0
    reconnect_count: int = 0
    authentication_failures: int = 0
    connection_latency_ms: float = 0.0
    request_latency_ms: float = 0.0
    negotiation_latency_ms: float = 0.0


class MCPMetrics:
    """A mutable MCP-local metrics accumulator. Owns only MCP's own numbers;
    Integration references its snapshot, never recomputes it."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}
        self._latency_ms: dict[str, float] = {}

    def incr(self, name: str, amount: int = 1) -> None:
        self._counters[name] = self._counters.get(name, 0) + amount

    def observe_latency(self, name: str, ms: float) -> None:
        self._latency_ms[name] = ms

    def snapshot(self) -> MCPMetricsSnapshot:
        return MCPMetricsSnapshot(
            request_count=self._counters.get("request", 0),
            failure_count=self._counters.get("failure", 0),
            reconnect_count=self._counters.get("reconnect", 0),
            authentication_failures=self._counters.get("auth_failure", 0),
            connection_latency_ms=self._latency_ms.get("connection", 0.0),
            request_latency_ms=self._latency_ms.get("request", 0.0),
            negotiation_latency_ms=self._latency_ms.get("negotiation", 0.0),
        )
