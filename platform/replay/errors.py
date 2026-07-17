"""Replay & Observability error hierarchy (M4.3).

Rooted in `core.errors.PlatformError`. The recorder subscribes to the Event Bus
read-only and never raises into it (a recording failure is isolated + counted, like
the gateway). These errors are raised only for local misuse (unknown session,
out-of-range cursor, invalid filter).
"""

from __future__ import annotations

from core.errors import PlatformError


class ReplayError(PlatformError):
    """Base for replay/observability errors."""


class UnknownSession(ReplayError):
    """No recorded session for the given id."""


class CursorOutOfRange(ReplayError):
    """A replay cursor move fell outside the timeline."""


class InvalidFilter(ReplayError):
    """An event filter is malformed."""
