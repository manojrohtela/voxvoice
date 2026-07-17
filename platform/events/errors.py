"""Event-platform error hierarchy (M4.1).

Rooted in `core.errors.PlatformError` so every event-bus failure is typed and
translatable at a boundary. The bus never lets these corrupt its state — a failing
subscriber is isolated (logged), not raised into the producer; these are raised
only for *misuse* (duplicate registration, invalid event, use-after-shutdown).
"""

from __future__ import annotations

from core.errors import PlatformError


class EventBusError(PlatformError):
    """Base for event-bus errors."""


class DuplicateSubscription(EventBusError):
    """The same handler is already registered (runtime-safe rejection)."""


class InvalidEvent(EventBusError):
    """An event with a non-`EventType` type (or otherwise malformed) was published."""


class EventBusShutdown(EventBusError):
    """An operation was attempted on a bus that has been shut down."""
