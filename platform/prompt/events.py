"""Prompt events (M5.4B-1) — phase constants.

The Prompt Runtime is the sole producer of its events. Per the approved design (and the
Memory/Knowledge/Context precedent), the core reuses the **existing** M5.0 `prompt.*`
`EventType` members (no core/bus change): lifecycle events map onto `PROMPT_RENDERED` with
a `phase` discriminator, and template-registry changes onto `PROMPT_TEMPLATE_UPDATED`.
Granular per-phase `EventType`s are a deferred **additive vocabulary extension** — the bus
is never modified.
"""

from __future__ import annotations

from enum import StrEnum


class PromptPhase(StrEnum):
    """The `phase` field carried on `prompt.*` lifecycle events."""

    REQUESTED = "requested"
    BUILT = "built"
    VALIDATED = "validated"
    RENDERED = "rendered"
    REJECTED = "rejected"
    DELIVERED = "delivered"
    EXPIRED = "expired"
