"""Durable event log tests (M10.2) — ADR-0032 semantics, ADR-0034 structure.

Covers database initialization, migration `0034`, schema creation, append (one, many,
duplicate), deterministic ordering, envelope round-trip fidelity, the injected clock seam,
transaction behaviour, **restart durability against file-backed SQLite**, the append-only
invariant, and the guarantee that the existing Event Bus and event contract are unchanged.

The restart tests use `tmp_path` deliberately (ADR-0034 DS10 / alternative C6): an
in-memory SQLite database is pinned to its `EventDatabase` for that object's lifetime, so a
"restart" against it cannot fail — the test would pass while proving nothing. Closing a
file-backed database and reopening the same path is the only shape that tests durability.
"""

from __future__ import annotations

import itertools

import pytest

from core.events import Event, EventSource, EventType
from events import DurableEventLog, InProcessEventBus
from events.persistence import EventDatabase, SqlDurableEventLog
from events.persistence.migrations import CHAIN_STARTS_AFTER, MIGRATIONS, upgrade_all
from events.persistence.models import EVENT_TABLES, DurableEventRow

# ── helpers ─────────────────────────────────────────────────────────────────────


def _event(
    n: int,
    *,
    session_id: str = "sess-1",
    type: EventType = EventType.TURN_STARTED,
    data: dict | None = None,
    metadata: dict | None = None,
) -> Event:
    """A fully-populated envelope — every field set, so round-trip tests actually
    exercise the mapping instead of comparing mostly-default objects."""
    return Event(
        id=f"evt-{n}",
        type=type,
        ts=100.0 + n,
        seq=n,
        session_id=session_id,
        source=EventSource.TURN_ENGINE,
        org_id="org-1",
        conversation_id="conv-1",
        correlation_id="corr-1",
        turn=n,
        data=data if data is not None else {"k": n, "nested": {"a": [1, 2]}},
        metadata=metadata if metadata is not None else {"m": "v"},
    )


def _fixed_clock(start: float = 500.0):
    counter = itertools.count()
    return lambda: start + next(counter)


@pytest.fixture
def db() -> EventDatabase:
    database = EventDatabase("sqlite://")
    database.create_all()
    yield database
    database.close()


@pytest.fixture
def log(db: EventDatabase) -> SqlDurableEventLog:
    return SqlDurableEventLog(db, clock=_fixed_clock())


def _file_db(tmp_path) -> EventDatabase:
    return EventDatabase(f"sqlite:///{tmp_path / 'events.db'}")


# ── 1. database initialization ──────────────────────────────────────────────────


def test_database_initializes_and_exposes_a_session():
    database = EventDatabase("sqlite://")
    database.create_all()
    assert database.session is not None
    assert database.url == "sqlite://"
    database.close()


def test_database_accepts_a_file_backed_sqlalchemy_url(tmp_path):
    database = _file_db(tmp_path)
    database.create_all()
    assert (tmp_path / "events.db").exists()
    database.close()


def test_events_plane_owns_its_database_and_imports_no_other():
    """ADR-0034 DS5 — it is `EventDatabase`, not the Control Plane's or Billing's."""
    import events.persistence.database as module

    source = module.__file__ and open(module.__file__).read() or ""
    assert "api.persistence" not in source
    assert "billing.persistence" not in source


# ── 2. migration 0034 registration + 3. schema creation ─────────────────────────


def test_migration_0034_is_registered():
    versions = [m.VERSION for m in MIGRATIONS]
    assert versions == ["0034", "0035"]
    assert MIGRATIONS[0].DESCRIPTION


def test_migration_chain_continues_the_billing_chain():
    """One global sequence: 0001–0012 (api) → 0013–0033 (billing) → 0034 (events)."""
    from billing.persistence.migrations import MIGRATIONS as BILLING_MIGRATIONS

    billing_versions = [m.VERSION for m in BILLING_MIGRATIONS]
    assert billing_versions[-1] == CHAIN_STARTS_AFTER == "0033"
    assert int(MIGRATIONS[0].VERSION) == int(CHAIN_STARTS_AFTER) + 1


def test_migration_chain_is_ordered_and_contiguous():
    versions = [m.VERSION for m in MIGRATIONS]
    assert versions == sorted(versions)
    for previous, nxt in zip(versions[:-1], versions[1:], strict=True):
        assert int(nxt) == int(previous) + 1


def test_migration_creates_the_schema_on_an_empty_database(tmp_path):
    from sqlalchemy import inspect

    database = _file_db(tmp_path)
    applied = upgrade_all(database)
    assert applied == ("0034", "0035")
    assert "evt_event" in set(inspect(database.engine).get_table_names())
    database.close()


def test_migration_is_idempotent(tmp_path):
    database = _file_db(tmp_path)
    upgrade_all(database)
    upgrade_all(database)  # must not raise — create-if-absent
    database.close()


def test_downgrade_is_a_no_op_and_keeps_the_table(tmp_path):
    from sqlalchemy import inspect

    database = _file_db(tmp_path)
    upgrade_all(database)
    MIGRATIONS[0].downgrade(database)
    assert "evt_event" in set(inspect(database.engine).get_table_names())
    database.close()


def test_every_table_is_evt_prefixed():
    """ADR-0034 DS9 — never `cp_`, never `cp_billing_`."""
    for table in EVENT_TABLES:
        assert table.__tablename__.startswith("evt_")
        assert not table.__tablename__.startswith("cp_")


def test_every_index_name_is_evt_prefixed():
    """Certification control NS-2 checks Billing's index names against *every* table in
    the process, so an unprefixed index here could fail a blocking Epic 9 control from
    outside Billing entirely."""
    for table in EVENT_TABLES:
        for index in table.__table__.indexes:
            assert index.name.startswith("ix_evt_"), index.name


# ── 4/5. append one, append many ────────────────────────────────────────────────


def test_append_one_event(log: SqlDurableEventLog):
    assert log.append(_event(1)) is True
    assert log.count() == 1


def test_append_many_events(log: SqlDurableEventLog):
    assert log.append_many([_event(1), _event(2), _event(3)]) == 3
    assert log.count() == 3


def test_append_many_is_one_transaction_and_records_none_on_failure(log: SqlDurableEventLog):
    def _explode():
        yield _event(1)
        raise RuntimeError("producer blew up mid-batch")

    with pytest.raises(RuntimeError):
        log.append_many(_explode())
    assert log.count() == 0, "a failed batch must record none of its events"


def test_append_is_idempotent_on_event_identity(log: SqlDurableEventLog):
    """Delivery is at-least-once (ADR-0032 ED2) — a redelivered append is ordinary
    traffic, not an error."""
    assert log.append(_event(1)) is True
    assert log.append(_event(1)) is False
    assert log.count() == 1


def test_append_many_deduplicates_within_a_batch(log: SqlDurableEventLog):
    assert log.append_many([_event(1), _event(1), _event(2)]) == 2
    assert log.count() == 2


# ── 6. deterministic ordering ───────────────────────────────────────────────────


def test_read_session_orders_by_seq_regardless_of_append_order(log: SqlDurableEventLog):
    log.append_many([_event(3), _event(1), _event(2)])
    assert [e.seq for e in log.read_session("sess-1")] == [1, 2, 3]


def test_read_all_is_deterministic_across_repeated_calls(log: SqlDurableEventLog):
    log.append_many([_event(n) for n in (5, 2, 9, 1)])
    first = [e.id for e in log.read_all()]
    assert first == [e.id for e in log.read_all()]
    assert first == ["evt-1", "evt-2", "evt-5", "evt-9"]


def test_sessions_are_isolated_in_reads(log: SqlDurableEventLog):
    log.append(_event(1, session_id="a"))
    log.append(_event(2, session_id="b"))
    assert [e.session_id for e in log.read_session("a")] == ["a"]
    assert log.count() == 2


# ── 7/8. identity + payload preservation (round-trip fidelity) ──────────────────


def test_round_trip_preserves_the_whole_envelope(log: SqlDurableEventLog):
    original = _event(7)
    log.append(original)
    (restored,) = log.read_session("sess-1")
    assert restored == original, "round-trip must return an equal envelope"


def test_round_trip_preserves_enum_types_not_just_their_values(log: SqlDurableEventLog):
    log.append(_event(1, type=EventType.SESSION_ENDED))
    (restored,) = log.read_session("sess-1")
    assert isinstance(restored.type, EventType)
    assert isinstance(restored.source, EventSource)


def test_round_trip_preserves_nested_payload_and_metadata(log: SqlDurableEventLog):
    payload = {"a": 1, "b": {"c": [1, 2, {"d": "e"}]}, "f": None, "g": True}
    meta = {"x": {"y": "z"}}
    log.append(_event(1, data=payload, metadata=meta))
    (restored,) = log.read_session("sess-1")
    assert restored.data == payload
    assert restored.metadata == meta


def test_empty_payload_and_metadata_round_trip(log: SqlDurableEventLog):
    log.append(_event(1, data={}, metadata={}))
    (restored,) = log.read_session("sess-1")
    assert restored.data == {} and restored.metadata == {}


def test_the_log_never_reassigns_seq(log: SqlDurableEventLog):
    """ADR-0032 ED3 — the bus is the sole sequence authority."""
    log.append_many([_event(41), _event(42)])
    assert [e.seq for e in log.read_all()] == [41, 42]


def test_identity_is_the_envelope_id_not_a_surrogate(db: EventDatabase, log):
    log.append(_event(1))
    row = db.session.get(DurableEventRow, "evt-1")
    assert row is not None and row.event_id == "evt-1"


# ── 9. clock seam ───────────────────────────────────────────────────────────────


def test_recorded_at_comes_from_the_injected_clock(db: EventDatabase):
    log = SqlDurableEventLog(db, clock=lambda: 1234.5)
    log.append(_event(1))
    row = db.session.get(DurableEventRow, "evt-1")
    assert row.recorded_at == 1234.5


def test_the_producers_ts_is_never_overwritten_by_the_log_clock(db: EventDatabase):
    log = SqlDurableEventLog(db, clock=lambda: 9999.0)
    original = _event(1)
    log.append(original)
    (restored,) = log.read_session("sess-1")
    assert restored.ts == original.ts != 9999.0


def test_append_many_stamps_one_recorded_at_for_the_batch(db: EventDatabase):
    log = SqlDurableEventLog(db, clock=_fixed_clock(700.0))
    log.append_many([_event(1), _event(2), _event(3)])
    stamps = {db.session.get(DurableEventRow, f"evt-{n}").recorded_at for n in (1, 2, 3)}
    assert stamps == {700.0}, "one batch is one durable write, so one recorded_at"


def test_no_ambient_clock_is_used_in_persistence():
    """ADR-0034 DS11. `time` may be imported for the production default, but no module
    may call `time.time()` outside the injected-default expression."""
    import pathlib

    for path in pathlib.Path("events/persistence").rglob("*.py"):
        source = path.read_text()
        for line in source.splitlines():
            if "time.time()" in line:
                assert "clock or time.time" in line, f"{path}: ambient clock use: {line.strip()}"


# ── 10. transaction behaviour ───────────────────────────────────────────────────


def test_appends_are_committed_and_visible_to_a_second_session(tmp_path):
    database = _file_db(tmp_path)
    database.create_all()
    SqlDurableEventLog(database, clock=_fixed_clock()).append(_event(1))
    database.close()

    reader = _file_db(tmp_path)
    assert SqlDurableEventLog(reader).count() == 1
    reader.close()


# ── 11/12. restart durability (file-backed SQLite, tmp_path) ────────────────────


def test_restart_durability_preserves_identity_and_order(tmp_path):
    """publish → persist → terminate/recreate → reopen → recover → verify.

    A function call is not a restart: the first `EventDatabase` is closed (session closed,
    engine disposed) and a *new* one is constructed against the same file, which is the
    only way the pooled connection cannot carry state across the boundary."""
    original = [_event(n) for n in (1, 2, 3)]

    first = _file_db(tmp_path)
    first.create_all()
    SqlDurableEventLog(first, clock=_fixed_clock()).append_many(original)
    assert SqlDurableEventLog(first).count() == 3
    first.close()  # terminate

    second = _file_db(tmp_path)  # recreate against the same file
    recovered = SqlDurableEventLog(second).read_all()
    assert list(recovered) == original, "exact envelopes, exact order, across a restart"
    assert [e.id for e in recovered] == ["evt-1", "evt-2", "evt-3"]
    second.close()


def test_restart_durability_survives_repeated_reopen(tmp_path):
    first = _file_db(tmp_path)
    first.create_all()
    SqlDurableEventLog(first, clock=_fixed_clock()).append(_event(1))
    first.close()

    for _ in range(3):
        database = _file_db(tmp_path)
        assert SqlDurableEventLog(database).count() == 1
        database.close()


def test_appends_after_a_restart_continue_the_same_log(tmp_path):
    first = _file_db(tmp_path)
    first.create_all()
    SqlDurableEventLog(first, clock=_fixed_clock()).append(_event(1))
    first.close()

    second = _file_db(tmp_path)
    SqlDurableEventLog(second, clock=_fixed_clock()).append(_event(2))
    assert [e.id for e in SqlDurableEventLog(second).read_all()] == ["evt-1", "evt-2"]
    second.close()


def test_idempotent_append_holds_across_a_restart(tmp_path):
    first = _file_db(tmp_path)
    first.create_all()
    SqlDurableEventLog(first, clock=_fixed_clock()).append(_event(1))
    first.close()

    second = _file_db(tmp_path)
    assert SqlDurableEventLog(second).append(_event(1)) is False
    assert SqlDurableEventLog(second).count() == 1
    second.close()


# ── 13/14. append-only invariant — no update, no delete path ────────────────────


def test_the_port_exposes_no_update_or_delete_method():
    """ADR-0032 ED5. Retention (ED9) arrives at M10.5 with the guarantees that make it
    safe; until then there is no removal path at all, which is the strongest form of the
    invariant."""
    surface = {name for name in dir(DurableEventLog) if not name.startswith("_")}
    assert surface == {"append", "append_many", "read_session", "read_all", "count"}


def test_the_implementation_exposes_no_update_or_delete_method():
    surface = {name for name in dir(SqlDurableEventLog) if not name.startswith("_")}
    forbidden = {n for n in surface if any(w in n for w in ("update", "delete", "remove", "purge"))}
    assert not forbidden, forbidden


def test_no_sql_delete_or_update_statement_exists_in_persistence():
    import pathlib

    for path in pathlib.Path("events/persistence").rglob("*.py"):
        source = path.read_text().lower()
        for token in ("session.delete", "session.merge", ".update(", "delete("):
            assert token not in source, f"{path} contains a mutation path: {token}"


def test_reappending_a_recorded_event_does_not_mutate_the_stored_row(db: EventDatabase):
    log = SqlDurableEventLog(db, clock=lambda: 111.0)
    log.append(_event(1, data={"original": True}))
    SqlDurableEventLog(db, clock=lambda: 222.0).append(_event(1, data={"tampered": True}))

    row = db.session.get(DurableEventRow, "evt-1")
    assert row.data == {"original": True}, "an immutable fact must not be rewritten"
    assert row.recorded_at == 111.0


# ── 16/17. existing bus + event contract unchanged ──────────────────────────────


def test_the_event_bus_behaviour_is_unchanged_by_m10_2():
    """M10.2 adds storage beside the bus, never inside it (DL-0017: no producer change)."""
    seen: list[Event] = []
    bus = InProcessEventBus(clock=lambda: 1.0, id_factory=lambda: "fixed")
    bus.subscribe(seen.append)
    published = bus.publish(_event(1))

    assert published.seq == 1, "the bus is still the sequence authority"
    assert len(seen) == 1 and seen[0].id == "evt-1"
    assert bus.subscriber_count == 1
    bus.shutdown()


def test_the_bus_has_no_durable_dependency():
    bus = InProcessEventBus()
    assert not hasattr(bus, "log")
    assert not hasattr(bus, "durable_log")
    bus.shutdown()


def test_the_event_envelope_contract_is_unchanged():
    """ADR-0032 EV1/EV2 — no field added or removed, no EventType value changed."""
    fields = set(Event.__dataclass_fields__)
    assert fields == {
        "id", "type", "ts", "seq", "session_id", "source", "org_id",
        "conversation_id", "correlation_id", "turn", "data", "metadata",
    }
    assert EventType.COST_UPDATED.value == "cost.updated"
    assert EventType.TURN_STARTED.value == "turn.started"


def test_no_metric_event_type_exists_yet():
    """M10.2 scope guard: the `metric.*` vocabulary is M10.8a, not this milestone."""
    metric_members = [m for m in EventType if m.value.startswith("metric.")]
    assert metric_members == [], metric_members


def test_the_sql_log_satisfies_the_port():
    assert issubclass(SqlDurableEventLog, DurableEventLog)
