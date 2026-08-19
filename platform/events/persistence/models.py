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

No DB-level foreign keys — the same decoupled, order-free table creation Epic 8 and Epic 9
chose. At M10.6 `evt_outbox.event_id` references `evt_event.event_id` **logically**: it is
the same `Event.id`, and the relationship is carried by that shared identity plus the one
transaction that writes both rows (ADR-0035 DW5), not by a DB constraint. Following the
established convention keeps table creation order-free and keeps retention (M10.5) free to
decide deletion order rather than having a constraint decide it.
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


class DurableOutboxRow(SQLModel, table=True):
    """One event's **mutable delivery state** (M10.6, ADR-0035 DW5).

    Separate from `evt_event` because that table is append-only by contract (**ED5**) — it
    has no update path, and delivery state is by definition updated. Two tables is what lets
    both invariants hold at once: history stays immutable, state stays mutable.

    `event_id` is the primary key, which *is* how "one row per event" is enforced — it is
    `Event.id` reused (**ED3**), never a second identity and never a surrogate. `seq` is the
    bus-assigned value copied verbatim so pending reads have a deterministic order without a
    join; the bus remains the sole sequence authority and nothing here assigns or derives it.

    `state` is a plain string, not a DB enum, so **M10.7** can add `retrying`,
    `dead_lettered` and `quarantined` **additively** (AR-32) without a migration that
    rewrites M10.6's rows. M10.6 writes only `pending` and `delivered` (`ports.OutboxState`).

    **Deliberately absent** (M10.7 owns them — ADR-0035 DW6): attempt counters, retry counts,
    `next_attempt_at`, backoff, failure history, claim/lease columns and any multi-consumer
    delivery state. In-process synchronous subscribers are not outbox destinations, so there
    is no consumer column to key them by.

    Timestamps come from the injected clock (ADR-0034 DS11), never an ambient one.
    """

    __tablename__ = "evt_outbox"

    event_id: str = Field(primary_key=True)
    seq: int = Field(index=True)
    state: str = Field(default="pending", index=True)
    created_at: float = Field(default=0.0, index=True)
    delivered_at: float | None = None

    # `read_pending()` filters on state and orders by (seq, event_id); the composite makes
    # that an index scan rather than a filter-then-sort. `evt_`-prefixed for the NS-2 reason
    # in the module docstring.
    __table_args__ = (Index("ix_evt_outbox_state_seq", "state", "seq"),)


# The tables migration **0034** owns. Frozen: `v0034_durable_event_log.TABLES` is this tuple,
# and migrations 0001–0034 are not modified, so nothing may be added here.
EVENT_TABLES = (DurableEventRow,)

# The tables migration **0035** owns (M10.6).
OUTBOX_TABLES = (DurableOutboxRow,)

# Every table this package owns, across every migration. `database.create_all` builds from
# this one tuple, so a table cannot exist in a migration and be missing from a fresh
# database. Each migration builds from its own tuple above, which is what keeps an applied
# migration's scope fixed after the fact.
ALL_EVENT_TABLES = EVENT_TABLES + OUTBOX_TABLES
