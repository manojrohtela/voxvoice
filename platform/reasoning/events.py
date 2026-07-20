"""Reasoning events (M5.5B-1) — phase constants.

The Reasoning Runtime is the sole producer of its events. Per the approved design (and the
Memory/Knowledge/Context/Prompt precedent), the core reuses the **existing** M5.0
`reasoning.*` / `model.*` `EventType` members (no core/bus change): lifecycle events map onto
`REASONING_STARTED`/`REASONING_COMPLETED`/`REASONING_FAILED` with a `phase` discriminator,
streamed chunks onto `MODEL_TOKEN`, and usage onto `MODEL_USAGE`. Granular per-phase
`EventType`s are a deferred **additive vocabulary extension** — the bus is never modified.
"""

from __future__ import annotations

from enum import StrEnum


class ReasoningPhase(StrEnum):
    """The `phase` field carried on `reasoning.*` / `model.*` events."""

    REQUESTED = "requested"
    STARTED = "started"
    STREAMING = "streaming"
    CHUNK = "chunk"
    COMPLETED = "completed"
    DELIVERED = "delivered"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
