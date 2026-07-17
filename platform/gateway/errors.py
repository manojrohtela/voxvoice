"""Live Event Gateway error hierarchy (M4.2).

Rooted in `core.errors.PlatformError` so every gateway failure is typed. A client
failure must never reach the Runtime — these are raised only for local misuse
(invalid/duplicate subscription, use-after-shutdown); delivery/serialization/network
failures are isolated per-connection and turned into metrics + disconnects, never
raised into the Event Bus.
"""

from __future__ import annotations

from core.errors import PlatformError


class GatewayError(PlatformError):
    """Base for Live Event Gateway errors."""


class InvalidSubscription(GatewayError):
    """A subscription/channel is malformed (e.g. an unknown scope value)."""


class DuplicateSubscription(GatewayError):
    """A client already holds an identical channel."""


class UnknownClient(GatewayError):
    """No connected client for the given id."""


class GatewayShutdown(GatewayError):
    """An operation was attempted on a gateway that has been shut down."""
