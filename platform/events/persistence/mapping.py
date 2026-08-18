"""Row ↔ envelope translation (M10.2) — the one place a stored row becomes a
`core.events.Event` and back.

Kept in its own module for the M9.4 reason: the durable log should express *append and
read*, not field lists, and the round-trip is the property worth testing in isolation.

**Round-trip fidelity is the contract**: `to_event(to_row(e, recorded_at=t)) == e` for every
envelope. `Event` is a frozen, slotted dataclass of primitives plus two open `dict`s, so a
single explicit mapping is correct for all of it — and explicit is right here rather than a
type-driven codec, because the envelope is *one* fixed shape, not thirty aggregates.

Two asymmetries are deliberate:

* **`type` and `source` are re-created as their enums** on the way out. They are stored as
  their `StrEnum` string values (stable, per §A5) and restored to `EventType`/`EventSource`,
  so a consumer gets the same typed envelope the producer published — not a stringly-typed
  lookalike that compares unequal.
* **`recorded_at` does not appear on the envelope.** It is the log's own fact about when it
  wrote, not the producer's fact about when the event happened, and inventing an envelope
  field for it would be exactly the parallel event vocabulary ADR-0032 ED3 forbids.
"""

from __future__ import annotations

from core.events import Event, EventSource, EventType

from .models import DurableEventRow


def to_row(event: Event, *, recorded_at: float) -> DurableEventRow:
    """Project one envelope into its stored row. `recorded_at` comes from the log's
    injected clock — never from an ambient one (ADR-0034 DS11)."""
    return DurableEventRow(
        event_id=event.id,
        session_id=event.session_id,
        seq=event.seq,
        type=event.type.value,
        source=event.source.value,
        org_id=event.org_id,
        conversation_id=event.conversation_id,
        correlation_id=event.correlation_id,
        turn=event.turn,
        ts=event.ts,
        recorded_at=recorded_at,
        data=dict(event.data),
        meta=dict(event.metadata),
    )


def to_event(row: DurableEventRow) -> Event:
    """Rebuild the envelope from its stored row, byte-for-byte equal to the one appended."""
    return Event(
        id=row.event_id,
        type=EventType(row.type),
        ts=row.ts,
        seq=row.seq,
        session_id=row.session_id,
        source=EventSource(row.source),
        org_id=row.org_id,
        conversation_id=row.conversation_id,
        correlation_id=row.correlation_id,
        turn=row.turn,
        data=dict(row.data or {}),
        metadata=dict(row.meta or {}),
    )
