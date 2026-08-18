"""Persistence models (M10.2) — SQLModel tables for the durable event log.

The **persistence representation only**. Nothing outside this package sees a row:
`mapping.py` translates row ↔ `core.events.Event` at the boundary, and no SQLModel type
escapes `events/persistence/` (the ADR-0031 §13 / M8.4 / M9.4 precedent).

**Namespace isolation.** Every table is prefixed **`evt_`** (ADR-0034 DS9). Not `cp_`,
which is the Control Plane's, and not `cp_billing_`, which is Epic 9's and frozen. Every
index and constraint name carries the same prefix — deliberately, because certification
control **NS-2** checks Billing's index names for collision against *every* table
registered in the process, so an unprefixed index here could fail a blocking Epic 9
control from outside Billing entirely.

**Row shape — projection + payload**, the M9.4 shape. Projected columns carry what the
port actually queries on (identity, session, ordering, type, tenancy) as indexed typed
columns; the two JSON columns carry the envelope's open maps verbatim. Projected columns
are *derived from* the envelope and are never a second source of truth for it (§A2.5).

**Why `data`/`meta` and not `metadata`.** `metadata` is reserved on the SQLModel/SQLAlchemy
declarative base; a column of that name shadows the registry and breaks table creation.
`mapping.py` restores the envelope's field names on the way out, so the envelope is
unaffected by this storage-level rename.

No DB-level foreign keys: the event log references no other table and owns no relation —
the same decoupled, order-free table creation Epic 8 and Epic 9 chose.
"""

from __future__ import annotations

from sqlalchemy import JSON as SAJSON
from sqlalchemy import Column, Index
from sqlmodel import Field, SQLModel


class DurableEventRow(SQLModel, table=True):
    """One durably recorded `core.events.Event`.

    `event_id` is the primary key because `Event.id` **is** the identity (ADR-0032 ED3) —
    the log invents no surrogate identity, and the PK is what makes `append` idempotent
    rather than duplicating a fact on redelivery.

    `seq` is stored exactly as the bus assigned it and is never recomputed here: the bus is
    the sole sequence authority (ED3), and a second one would be a second truth about order.
    """

    __tablename__ = "evt_event"

    event_id: str = Field(primary_key=True)
    session_id: str = Field(index=True)
    seq: int = Field(index=True)
    type: str = Field(index=True)
    source: str = ""
    org_id: str = Field(default="", index=True)
    conversation_id: str | None = None
    correlation_id: str | None = None
    turn: int | None = None

    # `ts` is the producing runtime's clock, carried on the envelope. `recorded_at` is the
    # log's own injected clock at the moment of durable append (ADR-0034 DS11) — two
    # different facts, so two columns. Collapsing them would lose the ability to see how
    # far behind the durable write ran.
    ts: float = 0.0
    recorded_at: float = Field(default=0.0, index=True)

    data: dict = Field(default_factory=dict, sa_column=Column("data", SAJSON))
    meta: dict = Field(default_factory=dict, sa_column=Column("meta", SAJSON))

    # Ordered reads are (session_id, seq) and (seq, event_id); both are indexed as
    # composites so the port's deterministic ordering is an index scan, not a sort.
    # Names carry the `evt_` prefix for the NS-2 reason in the module docstring.
    __table_args__ = (
        Index("ix_evt_event_session_seq", "session_id", "seq"),
        Index("ix_evt_event_seq_id", "seq", "event_id"),
    )


# Every table this package owns. `database.create_all` and migration 0034 both build from
# this one tuple, so a table cannot exist in one and be missing from the other.
EVENT_TABLES = (DurableEventRow,)
