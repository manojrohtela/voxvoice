"""`evt_outbox` + `SqlDurableEventSpine` (M10.6) — the executable form of ADR-0035
DW1/DW5/DW6/DW7 and verification criteria DV6/DV7/DV10.

Restart durability uses **file-backed SQLite under `tmp_path`**, never in-memory: an
in-memory database is pinned to the object's lifetime by construction, so a "restart"
against it cannot fail — the test would pass while proving nothing (ADR-0034 DS10).

The EV5 proof is a hermetic **test-only** compound producer. It exists here, in the test
suite, precisely so that no production runtime transaction abstraction is created to satisfy
a verification criterion (ADR-0035 DV6, owner ruling D-5).
"""

from __future__ import annotations

import inspect as _inspect

import pytest
from sqlalchemy import inspect as sa_inspect
from sqlmodel import select

from core.events import Event, EventSource, EventType
from core.policies import RetryPolicy
from events.persistence import (
    ALL_EVENT_TABLES,
    EVENT_TABLES,
    OUTBOX_TABLES,
    DurableEventRow,
    DurableOutboxRow,
    EventDatabase,
    SqlDurableEventSpine,
)
from events.persistence.migrations import CHAIN_STARTS_AFTER, MIGRATIONS, upgrade_all
from events.ports import DeliveryOutcome, DurableOutbox, OutboxEntry, OutboxState


class FrozenClock:
    """Injected clock (ADR-0034 DS11). Deterministic, never ambient."""

    def __init__(self, start: float = 1000.0, step: float = 1.0) -> None:
        self.now = start
        self.step = step

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value


def make_event(event_id: str, *, seq: int, session_id: str = "s-1") -> Event:
    return Event(
        id=event_id,
        type=EventType.RUNTIME_STARTED,
        ts=1.0,
        seq=seq,
        session_id=session_id,
        source=EventSource.RUNTIME,
        org_id="org-1",
        conversation_id="c-1",
        correlation_id=None,
        turn=None,
        data={"k": "v"},
        metadata={"m": 1},
    )


@pytest.fixture
def spine():
    db = EventDatabase()
    db.create_all()
    yield SqlDurableEventSpine(db, clock=FrozenClock())
    db.close()


# ── DW5: one row per event, both written together ───────────────────────────────


def test_append_writes_one_event_row_and_one_outbox_row(spine):
    assert spine.append(make_event("e-1", seq=1)) is True
    assert spine.count() == 1
    assert spine.count_entries() == 1


def test_the_outbox_entry_starts_pending(spine):
    spine.append(make_event("e-1", seq=7))
    entry = spine.get("e-1")
    assert entry == OutboxEntry(
        event_id="e-1", seq=7, state=OutboxState.PENDING,
        created_at=entry.created_at, delivered_at=None,
    )
    assert entry.is_pending is True


def test_the_outbox_reuses_event_id_and_introduces_no_second_identity(spine):
    """ED3. `event_id` is `Event.id`; the primary key is what makes 'one row per event'
    structural rather than a convention."""
    event = make_event("e-1", seq=1)
    spine.append(event)
    assert spine.get(event.id).event_id == event.id
    assert DurableOutboxRow.__table__.primary_key.columns.keys() == ["event_id"]


def test_the_outbox_persists_the_bus_seq_verbatim(spine):
    """ED3 — copied, never assigned or derived here. The value must equal the event's."""
    spine.append(make_event("e-1", seq=42))
    assert spine.get("e-1").seq == 42
    assert spine.read_all()[0].seq == 42


def test_append_many_writes_both_rows_for_every_event(spine):
    added = spine.append_many([make_event(f"e-{i}", seq=i) for i in range(1, 6)])
    assert added == 5
    assert spine.count() == 5
    assert spine.count_entries() == 5
    assert [e.event_id for e in spine.read_pending()] == [f"e-{i}" for i in range(1, 6)]


def test_a_redelivered_append_creates_no_second_outbox_row(spine):
    """ED2 — at-least-once means redelivery is ordinary traffic, and it must not duplicate
    delivery state any more than it duplicates history."""
    event = make_event("e-1", seq=1)
    assert spine.append(event) is True
    assert spine.append(event) is False
    assert spine.count() == 1
    assert spine.count_entries() == 1


def test_timestamps_come_from_the_injected_clock(spine):
    spine.append(make_event("e-1", seq=1))
    created = spine.get("e-1").created_at
    assert created == 1000.0  # FrozenClock's first tick, not wall-clock time


# ── DW6: PENDING → DELIVERED, and only that ─────────────────────────────────────


def test_mark_delivered_transitions_pending_to_delivered(spine):
    spine.append(make_event("e-1", seq=1))
    assert spine.mark_delivered("e-1") is True

    entry = spine.get("e-1")
    assert entry.state is OutboxState.DELIVERED
    assert entry.delivered_at is not None
    assert entry.is_pending is False


def test_mark_delivered_is_idempotent(spine):
    spine.append(make_event("e-1", seq=1))
    assert spine.mark_delivered("e-1") is True
    assert spine.mark_delivered("e-1") is False  # already delivered — not an error


def test_mark_delivered_on_an_unknown_event_is_false_not_an_error(spine):
    assert spine.mark_delivered("never-existed") is False


def test_read_pending_excludes_delivered_entries(spine):
    for i in range(1, 4):
        spine.append(make_event(f"e-{i}", seq=i))
    spine.mark_delivered("e-2")

    assert [e.event_id for e in spine.read_pending()] == ["e-1", "e-3"]
    assert spine.count_pending() == 2
    assert spine.count_entries() == 3


def test_read_pending_is_deterministically_ordered(spine):
    """ED4. Ordered by (seq, event_id), stable across calls."""
    for event_id, seq in (("z", 3), ("a", 1), ("m", 2)):
        spine.append(make_event(event_id, seq=seq))

    order = [e.event_id for e in spine.read_pending()]
    assert order == ["a", "m", "z"]
    assert [e.event_id for e in spine.read_pending()] == order


# ── I. state boundary: M10.7 machinery is absent ────────────────────────────────


def test_the_lifecycle_exposes_exactly_the_five_ratified_states():
    """Retargeted at M10.7 (owner ruling D-8): M10.6 pinned this to `{pending, delivered}` to
    prove the ladder had not arrived early. ADR-0036 RL2 authorized the other three, so the
    assertion now pins the **complete** lifecycle — exact equality, so a sixth state cannot leak
    in from M10.8 or anywhere else. A state the implementation cannot reach is still a promise
    it cannot keep."""
    assert {s.value for s in OutboxState} == {
        "pending", "retrying", "delivered", "dead_lettered", "quarantined",
    }


def test_the_outbox_table_carries_exactly_the_ratified_columns():
    """Retargeted at M10.7 (owner ruling D-8). The five M10.6 columns plus exactly the five
    ADR-0036 RL11 authorized — and nothing else. Still an exact set, so it keeps guarding: the
    forbidden list below is what M10.7 was ruled *not* to add, and what later milestones must
    not smuggle in either."""
    columns = set(DurableOutboxRow.__table__.columns.keys())
    forbidden = {
        # M10.7 was ruled not to add these (ADR-0036 RL11): failure history is the authoritative
        # error evidence, so no `last_error`; and there is no worker, so nothing to lease.
        "last_error", "failure_history", "retry_history",
        "attempts", "retry_count", "backoff", "backoff_seconds",
        "claimed_at", "claimed_by", "lease_expires_at", "locked_by", "worker_id", "visible_at",
        # Later milestones: M10.5 retention, M10.10 metering, DW5 destinations.
        "retained_until", "expires_at", "purge_after", "metered_at", "meter_code",
        "consumer_id", "destination", "endpoint_id",
    }
    assert not (columns & forbidden), f"forbidden columns leaked: {columns & forbidden}"
    assert columns == {
        "event_id", "seq", "state", "created_at", "delivered_at",
        "attempt_count", "next_attempt_at",
        "dead_lettered_at", "quarantined_at", "quarantine_reason",
    }


def test_the_outbox_port_declares_no_retry_or_recovery_methods():
    methods = {n for n in dir(DurableOutbox) if not n.startswith("_")}
    forbidden = {
        "retry", "schedule_retry", "dead_letter", "quarantine", "claim", "lease",
        "recover", "redispatch", "dispatch", "drain",
    }
    assert not (methods & forbidden), f"M10.7/M10.8 methods leaked: {methods & forbidden}"


def test_the_state_column_is_a_plain_string_so_m107_can_extend_it_additively():
    """AR-32. A DB-level enum would force a migration that rewrites M10.6 rows."""
    from sqlalchemy import Enum as SAEnum
    from sqlalchemy.dialects import sqlite

    column_type = DurableOutboxRow.__table__.columns["state"].type
    assert not isinstance(column_type, SAEnum), "a DB enum would need a migration per state"
    assert "VARCHAR" in column_type.compile(dialect=sqlite.dialect()).upper()


def test_the_spine_runs_no_recovery_worker_and_never_redispatches(spine):
    """DW7 — M10.8 owns recovery. Reading pending entries must not deliver them."""
    spine.append(make_event("e-1", seq=1))
    pending_before = spine.read_pending()
    assert [e.event_id for e in pending_before] == ["e-1"]
    # Reading changed nothing: still pending, still undelivered.
    assert spine.get("e-1").state is OutboxState.PENDING
    assert spine.count_pending() == 1


# ── H. outbox separation: evt_event stays immutable ─────────────────────────────


def test_delivery_state_is_not_stored_on_the_event_row():
    """ED5 — the append-only log has no update path, so it cannot carry mutable state."""
    columns = set(DurableEventRow.__table__.columns.keys())
    delivery_state = {"state", "delivered", "delivered_at", "pending", "outbox_state"}
    assert not (columns & delivery_state), f"delivery state leaked onto evt_event: {columns}"


def test_marking_delivered_does_not_touch_the_event_row(spine):
    spine.append(make_event("e-1", seq=1))
    before = spine.read_all()[0]
    spine.mark_delivered("e-1")
    assert spine.read_all()[0] == before


def test_the_event_log_port_still_has_no_update_or_delete_method():
    from events.ports import DurableEventLog

    methods = {n for n in dir(DurableEventLog) if not n.startswith("_")}
    assert not (methods & {"update", "delete", "remove", "purge", "expire", "mark_delivered"})


def test_the_two_tables_are_distinct_and_both_evt_prefixed():
    assert DurableEventRow.__tablename__ == "evt_event"
    assert DurableOutboxRow.__tablename__ == "evt_outbox"


# ── DV6 / EV5: compound rollback through one session ────────────────────────────


class _TestOnlyCompoundProducer:
    """A hermetic, **test-only** producer that writes an event and its outbox entry through
    the same `EventDatabase.session` and can be made to fail between the two writes.

    This exists to prove EV5 — *"outbox write and originating state change share one
    transaction (proven by rollback)"* — without creating any production runtime transaction
    abstraction, which ADR-0035 DW1 forbids. It is a test double for a transactional
    producer, not a design for one.
    """

    def __init__(self, database: EventDatabase, *, now: float = 500.0) -> None:
        self._db = database
        self._now = now

    def write(self, event: Event, *, fail_after_event_row: bool = False) -> None:
        session = self._db.session
        try:
            session.add(
                DurableEventRow(
                    event_id=event.id, session_id=event.session_id, seq=event.seq,
                    type=event.type.value, source=event.source.value, org_id=event.org_id,
                    ts=event.ts, recorded_at=self._now, data={}, meta={},
                )
            )
            if fail_after_event_row:
                raise RuntimeError("producer failed between the two writes")
            session.add(
                DurableOutboxRow(
                    event_id=event.id, seq=event.seq,
                    state=OutboxState.PENDING.value, created_at=self._now,
                )
            )
            session.commit()
        except Exception:
            session.rollback()
            raise


def test_ev5_event_and_outbox_rows_roll_back_together(spine):
    """EV5, ADR-0035 DV6. Both writes share one session and one transaction: a failure
    between them must leave **neither** row behind, not a half-written pair."""
    db = spine._db
    producer = _TestOnlyCompoundProducer(db)
    event = make_event("ev5", seq=1)

    with pytest.raises(RuntimeError, match="between the two writes"):
        producer.write(event, fail_after_event_row=True)

    assert spine.count() == 0, "the event row survived a rolled-back transaction"
    assert spine.count_entries() == 0, "the outbox row survived a rolled-back transaction"
    assert spine.get("ev5") is None


def test_ev5_the_same_producer_commits_both_rows_on_success(spine):
    """The other half of the proof: the mechanism does write both when it succeeds, so the
    rollback test above is not passing because nothing was ever written."""
    producer = _TestOnlyCompoundProducer(spine._db)
    producer.write(make_event("ev5-ok", seq=1))

    assert spine.count() == 1
    assert spine.count_entries() == 1


def test_ev5_uses_the_same_session_object_as_the_spine(spine):
    """The claim is about *one transaction*, which is only true if it is one session."""
    producer = _TestOnlyCompoundProducer(spine._db)
    assert producer._db.session is spine._db.session


def test_append_rolls_both_rows_back_when_the_commit_fails(spine, monkeypatch):
    """The production path, not the test double: if the commit raises, neither row lands and
    the exception propagates to the *caller of the log* (the bus catches it — DW4)."""
    session = spine._db.session
    monkeypatch.setattr(session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("db down")))

    with pytest.raises(RuntimeError, match="db down"):
        spine.append(make_event("e-1", seq=1))

    monkeypatch.undo()
    assert spine.count() == 0
    assert spine.count_entries() == 0


def test_append_many_remains_all_or_nothing_across_both_tables(spine, monkeypatch):
    """M10.2's all-or-nothing guarantee, preserved and extended to the outbox rows."""
    session = spine._db.session
    monkeypatch.setattr(session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("db down")))

    with pytest.raises(RuntimeError):
        spine.append_many([make_event(f"e-{i}", seq=i) for i in range(1, 4)])

    monkeypatch.undo()
    assert spine.count() == 0
    assert spine.count_entries() == 0


# ── F. restart durability (file-backed SQLite, never in-memory) ─────────────────


def test_event_and_outbox_state_survive_a_restart(tmp_path):
    """ADR-0034 DS10 / DV7. Close the database, reopen the same file, read the state back."""
    url = f"sqlite:///{tmp_path / 'spine.db'}"

    db = EventDatabase(url)
    db.create_all()
    spine = SqlDurableEventSpine(db, clock=FrozenClock())
    spine.append_many([make_event(f"e-{i}", seq=i) for i in range(1, 4)])
    spine.mark_delivered("e-2")
    db.close()

    reopened = EventDatabase(url)
    revived = SqlDurableEventSpine(reopened, clock=FrozenClock())

    assert revived.count() == 3
    assert revived.count_entries() == 3
    assert [e.event_id for e in revived.read_pending()] == ["e-1", "e-3"]
    assert revived.get("e-2").state is OutboxState.DELIVERED
    reopened.close()


def test_pending_entries_survive_shutdown_which_the_in_memory_queue_never_did(tmp_path):
    """DW7. The M4.1 bus discards its queue on `shutdown()`; a persisted PENDING row is not
    in that queue and is therefore still there afterwards. **M10.6 does not re-dispatch it**
    — recovery is M10.8."""
    from events import InProcessEventBus

    url = f"sqlite:///{tmp_path / 'shutdown.db'}"
    db = EventDatabase(url)
    db.create_all()
    spine = SqlDurableEventSpine(db, clock=FrozenClock())

    bus = InProcessEventBus(durable=spine)
    bus.publish(make_event("e-1", seq=0))
    bus.shutdown()
    db.close()

    reopened = EventDatabase(url)
    revived = SqlDurableEventSpine(reopened, clock=FrozenClock())
    assert revived.count_pending() == 1
    reopened.close()


def test_the_restart_proof_does_not_use_an_in_memory_database():
    """A guard on the guard: an in-memory URL cannot express a restart, so a test that used
    one would pass while proving nothing (ADR-0034 DS10)."""
    source = _inspect.getsource(test_event_and_outbox_state_survive_a_restart)
    assert "tmp_path" in source
    assert "sqlite://'" not in source and ":memory:" not in source


# ── DV10 / migration 0035 ───────────────────────────────────────────────────────


def test_migration_0035_is_registered_after_0034():
    versions = [m.VERSION for m in MIGRATIONS]
    assert versions == ["0034", "0035", "0036"]
    assert CHAIN_STARTS_AFTER == "0033"


def test_the_migration_chain_is_contiguous_and_ordered():
    versions = [int(m.VERSION) for m in MIGRATIONS]
    assert versions == sorted(versions)
    assert all(b - a == 1 for a, b in zip(versions, versions[1:], strict=False))


def test_migration_0034_still_owns_only_the_event_table():
    """0034 is applied history and must not have been widened to include `evt_outbox`."""
    from events.persistence.migrations import v0034_durable_event_log

    assert v0034_durable_event_log.TABLES == EVENT_TABLES
    assert [t.__tablename__ for t in v0034_durable_event_log.TABLES] == ["evt_event"]


def test_migration_0035_owns_only_the_outbox_table():
    from events.persistence.migrations import v0035_durable_outbox

    assert v0035_durable_outbox.TABLES == OUTBOX_TABLES
    assert [t.__tablename__ for t in v0035_durable_outbox.TABLES] == ["evt_outbox"]


def test_upgrade_all_creates_both_tables_on_an_empty_database(tmp_path):
    db = EventDatabase(f"sqlite:///{tmp_path / 'migrated.db'}")
    applied = upgrade_all(db)

    assert applied == ("0034", "0035", "0036")
    tables = set(sa_inspect(db.engine).get_table_names())
    assert {"evt_event", "evt_outbox"} <= tables
    db.close()


def test_migration_0035_is_idempotent(tmp_path):
    from events.persistence.migrations import v0035_durable_outbox

    db = EventDatabase(f"sqlite:///{tmp_path / 'idem.db'}")
    upgrade_all(db)
    v0035_durable_outbox.upgrade(db)  # must not raise on a database that already has it
    v0035_durable_outbox.upgrade(db)

    spine = SqlDurableEventSpine(db, clock=FrozenClock())
    spine.append(make_event("e-1", seq=1))
    assert spine.count_entries() == 1
    db.close()


def test_migration_0035_downgrade_is_a_documented_no_op(tmp_path):
    from events.persistence.migrations import v0035_durable_outbox

    db = EventDatabase(f"sqlite:///{tmp_path / 'down.db'}")
    upgrade_all(db)
    assert v0035_durable_outbox.downgrade(db) is None
    assert "evt_outbox" in set(sa_inspect(db.engine).get_table_names())
    db.close()


def test_every_outbox_index_carries_the_evt_prefix():
    """NS-2: certification checks Billing index names against every table in the process, so
    an unprefixed index here could fail a blocking Epic 9 control from outside Billing."""
    names = {ix.name for ix in DurableOutboxRow.__table__.indexes}
    assert names, "evt_outbox is expected to declare indexes"
    assert all(n.startswith("ix_evt_") for n in names), names


def test_create_all_builds_every_table_the_package_owns(tmp_path):
    """Retargeted at M10.7 (owner ruling D-8). `create_all()` must build **exactly** the tables
    the package owns through M10.7 — asserted as equality, not containment, so a table can
    neither go missing on a fresh database nor appear without being owned by a migration.

    The per-migration tuples stay distinct: `0034` still owns only `evt_event`, `0035` still
    owns only `evt_outbox`, and `0036` owns the M10.7 addition. That separation is what keeps an
    applied migration's scope fixed after the fact.
    """
    from events.persistence import OUTBOX_FAILURE_TABLES

    db = EventDatabase(f"sqlite:///{tmp_path / 'fresh.db'}")
    db.create_all()

    tables = set(sa_inspect(db.engine).get_table_names())
    assert tables == {"evt_event", "evt_outbox", "evt_outbox_failure"}
    assert {t.__tablename__ for t in ALL_EVENT_TABLES} == tables
    assert ALL_EVENT_TABLES == EVENT_TABLES + OUTBOX_TABLES + OUTBOX_FAILURE_TABLES

    # The ownership split, unchanged by M10.7.
    assert [t.__tablename__ for t in EVENT_TABLES] == ["evt_event"]
    assert [t.__tablename__ for t in OUTBOX_TABLES] == ["evt_outbox"]
    assert [t.__tablename__ for t in OUTBOX_FAILURE_TABLES] == ["evt_outbox_failure"]
    db.close()


def test_create_all_touches_no_other_planes_tables(tmp_path):
    db = EventDatabase(f"sqlite:///{tmp_path / 'scoped.db'}")
    db.create_all()

    tables = set(sa_inspect(db.engine).get_table_names())
    assert all(t.startswith("evt_") for t in tables), tables
    db.close()


# ── the spine satisfies both ports ──────────────────────────────────────────────


def test_the_spine_implements_both_ports(spine):
    from events.ports import DurableEventLog

    assert isinstance(spine, DurableEventLog)
    assert isinstance(spine, DurableOutbox)


def test_the_spine_round_trips_the_envelope_verbatim(spine):
    event = make_event("e-1", seq=9)
    spine.append(event)
    assert spine.read_all()[0] == event


def test_the_pure_event_log_is_unchanged_and_writes_no_outbox_row(spine):
    """`SqlDurableEventLog` (M10.2) is untouched: a caller wanting history without delivery
    state is not forced to acquire an outbox."""
    from events.persistence import SqlDurableEventLog

    log = SqlDurableEventLog(spine._db, clock=FrozenClock())
    log.append(make_event("log-only", seq=1))

    assert log.count() == 1
    assert spine.count_entries() == 0
    assert spine.get("log-only") is None


def test_reads_are_delegated_and_ordered_by_seq(spine):
    for event_id, seq in (("c", 3), ("a", 1), ("b", 2)):
        spine.append(make_event(event_id, seq=seq))

    assert [e.id for e in spine.read_all()] == ["a", "b", "c"]
    assert [e.id for e in spine.read_session("s-1")] == ["a", "b", "c"]


def test_no_sqlmodel_type_escapes_the_persistence_package(spine):
    """The port speaks `core.events.Event` and `OutboxEntry`; a row must never leak out."""
    spine.append(make_event("e-1", seq=1))
    assert isinstance(spine.read_all()[0], Event)
    assert isinstance(spine.get("e-1"), OutboxEntry)
    assert not isinstance(spine.get("e-1"), DurableOutboxRow)


# ── end-to-end: bus seam over the real database ─────────────────────────────────


def test_bus_publishes_land_in_both_tables_through_the_real_spine(tmp_path):
    from events import InProcessEventBus

    db = EventDatabase(f"sqlite:///{tmp_path / 'e2e.db'}")
    db.create_all()
    spine = SqlDurableEventSpine(db, clock=FrozenClock())
    bus = InProcessEventBus(durable=spine)
    delivered: list[str] = []
    bus.subscribe(lambda e: delivered.append(e.id))

    bus.publish(make_event("e-1", seq=0))
    bus.publish(make_event("e-2", seq=0))

    assert delivered == ["e-1", "e-2"]
    assert [e.seq for e in spine.read_all()] == [1, 2]  # the bus's stamps, persisted verbatim
    assert [e.event_id for e in spine.read_pending()] == ["e-1", "e-2"]
    db.close()


def test_rows_written_through_the_bus_use_the_session_the_spine_owns(tmp_path):
    """Both rows land in one transaction on the spine's own `EventDatabase` — never another
    plane's (ADR-0034 DS5)."""
    from events import InProcessEventBus

    db = EventDatabase(f"sqlite:///{tmp_path / 'session.db'}")
    db.create_all()
    spine = SqlDurableEventSpine(db, clock=FrozenClock())
    InProcessEventBus(durable=spine).publish(make_event("e-1", seq=0))

    rows = db.session.exec(select(DurableOutboxRow)).all()
    assert [r.event_id for r in rows] == ["e-1"]
    db.close()


# ── M10.12 operator surfaces (ADR-0042 HD7/HD8/HD9) ─────────────────────────────
#
# Appended additively (HD14): nothing above is modified, and the deny-list guard at
# `test_the_outbox_port_declares_no_retry_or_recovery_methods` keeps its rule unchanged — the
# two counts pass it because they are counts, not controls.


def _drive(spine, event_id, state: OutboxState) -> None:
    """Move one appended entry to `state` using only ratified transitions."""
    if state is OutboxState.PENDING:
        return
    if state is OutboxState.DELIVERED:
        spine.mark_delivered(event_id)
        return
    if state is OutboxState.QUARANTINED:
        spine.record_failure(event_id, DeliveryOutcome.PERMANENT_FAILURE, error="fatal")
        return
    spine.begin_attempt(event_id)
    spine.record_failure(event_id, DeliveryOutcome.TRANSIENT_FAILURE, error="flaky")
    if state is OutboxState.RETRYING:
        return
    if state is OutboxState.DEAD_LETTERED:
        # `EXHAUST` is only an edge out of `RETRYING`, so the ladder is entered first and spent
        # on the second failure under a two-rung policy.
        spine.begin_attempt(event_id)
        spine.record_failure(
            event_id,
            DeliveryOutcome.TRANSIENT_FAILURE,
            error="flaky",
            policy=RetryPolicy(max_attempts=2),
        )
        return
    raise AssertionError(f"unsupported state {state!r}")


def _populate(spine) -> None:
    """One entry in each of the five outbox states, so every count is proven against a
    population containing all of them rather than only its own."""
    for n, state in enumerate(OutboxState, start=1):
        spine.append(make_event(f"e-{n}", seq=n))
        _drive(spine, f"e-{n}", state)


def test_the_operator_counts_are_zero_on_an_empty_outbox(spine):
    assert spine.count_dead_lettered() == 0
    assert spine.count_quarantined() == 0


def test_each_operator_count_counts_only_its_own_state(spine):
    """HV1/HV2 — correct across **every** outbox state. A count that ignored its filter would
    return 5 here, and one that matched the wrong state would return 0."""
    _populate(spine)

    assert spine.count_entries() == 5
    assert spine.count_pending() == 1
    assert spine.count_dead_lettered() == 1
    assert spine.count_quarantined() == 1


def test_the_counts_track_transitions(spine):
    """Point-in-time (HD8): the answer follows the outbox rather than being cached."""
    spine.append(make_event("e-1", seq=1))
    assert spine.count_quarantined() == 0

    _drive(spine, "e-1", OutboxState.QUARANTINED)
    assert spine.count_quarantined() == 1
    assert spine.count_dead_lettered() == 0


def test_the_counts_are_totals_not_session_scoped(spine):
    """HD8 — `evt_outbox` carries no session column, so a total is the only honest answer; the
    count must aggregate across sessions rather than silently reporting one."""
    for n, session in ((1, "s-a"), (2, "s-b")):
        spine.append(make_event(f"e-{n}", seq=n, session_id=session))
        _drive(spine, f"e-{n}", OutboxState.QUARANTINED)

    assert spine.count_quarantined() == 2


def test_counting_mutates_nothing(spine):
    """HV3 / HI1 / HI2 — a count is a query. Every row is byte-identical afterwards, and the
    counts are stable across repeated calls."""
    _populate(spine)
    before = {
        r.event_id: (r.state, r.attempt_count, r.next_attempt_at, r.quarantine_reason)
        for r in spine._db.session.exec(select(DurableOutboxRow)).all()
    }
    events_before = [e.id for e in spine.read_all()]

    for _ in range(3):
        assert (spine.count_dead_lettered(), spine.count_quarantined()) == (1, 1)

    after = {
        r.event_id: (r.state, r.attempt_count, r.next_attempt_at, r.quarantine_reason)
        for r in spine._db.session.exec(select(DurableOutboxRow)).all()
    }
    assert after == before, "counting changed outbox state"
    assert [e.id for e in spine.read_all()] == events_before, "counting changed the event log"


def test_the_counts_do_not_appear_on_the_event_log_port():
    """HD13 — `DurableEventLog` keeps its five methods; operator state is the outbox's."""
    from events.ports import DurableEventLog

    surface = {n for n in dir(DurableEventLog) if not n.startswith("_")}
    assert surface == {"append", "append_many", "read_session", "read_all", "count"}


def test_the_operator_counts_do_not_trip_the_existing_deny_list():
    """HV4 — the M10.7/M10.8 deny-list guard above still passes unmodified, and it must: a method
    named bare `quarantine` would be operator control, while `count_quarantined` is a count."""
    methods = {n for n in dir(DurableOutbox) if not n.startswith("_")}
    assert {"count_dead_lettered", "count_quarantined"} <= methods
    assert not (methods & {"dead_letter", "quarantine", "redrive", "release", "drain"})
