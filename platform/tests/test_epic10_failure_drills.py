"""Epic 10 failure drills (M10.12) — ADR-0042 HD4/HD5/HD6.

Every failure mode below is already implemented and already unit-tested in isolation. What was
never tested is whether they behave correctly **together**: a dead letter that recovery must not
re-select *and* retention must not delete; a quarantine whose evidence must survive a retention
sweep; a durable-write failure that must reach neither the producer nor a subscriber. A drill is
that composition — a scenario crossing milestone boundaries, not a second copy of a unit test
(**HD4**).

**A drill is a test, never a production feature** (HD4/HD5). This module adds no production code,
no failure state, no transition, no trigger and no API. It is **deterministic, hermetic and
caller-driven**: injected clocks, injected classifiers, injected transports, in-memory SQLite, no
network, no credentials, no vendor service, and **no scheduler, thread, timer, polling loop or
sleep** (RC2). `asyncio.run(...)` appears once, to drive a pipeline turn to completion — that is
deterministic execution, not scheduling (**HV7**).

Shape follows `test_epic10_shadow_validation.py` (ADR-0040 SV5/SV7): real contracts and real
implementations, wiring no production component.

The seven authorized modes (**HD6**):

  1. durable-write failure          — ADR-0035 DW4
  2. transient delivery failure     → `RETRYING`
  3. retry exhaustion               → `DEAD_LETTERED` (ED7)
  4. permanent failure              → `QUARANTINED` (ED8)
  5. recovery-path failure          — ADR-0037
  6. observer failure               — ADR-0038 / M10.9
  7. retention retain-on-raise      — ADR-0041 RT8

**HV6 requires deterministic repeated-run outcomes only.** No restart or file-backed durability
assertion appears here — restart re-dispatch is M10.8's, already delivered and tested, and
re-testing it would be the scope creep this milestone's gate exists to prevent.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib

import pytest

from core.events import Event, EventMetadata, EventSource, EventType
from core.policies import RetryPolicy
from events import (
    DeliveryOutcome,
    DeliveryTransport,
    EventRetention,
    InProcessEventBus,
    MeteredWatermark,
    OutboxRecovery,
    OutboxState,
    RetentionPolicy,
)
from events.persistence import EventDatabase, SqlDurableEventSpine, SqlRetentionStore

# ── the timeline ────────────────────────────────────────────────────────────────

NOW = 1000.0
RECORDED_AT = 900.0
RETENTION = RetentionPolicy(max_age_s=10.0, window_span_s=100.0)  # window [890.0, 990.0)


# ── fixtures and doubles ────────────────────────────────────────────────────────


def _event(n: int, *, session_id: str = "drill-1") -> Event:
    return Event(
        id=f"evt-{n}",
        type=EventType.TURN_STARTED,
        ts=float(n),
        seq=n,
        session_id=session_id,
        source=EventSource.TURN_ENGINE,
        org_id="org-1",
        data={"k": n},
    )


class _FailingTransport(DeliveryTransport):
    """Fails every attempt with a caller-classified error. Counts attempts made."""

    def __init__(self, exc: Exception | None = None) -> None:
        self._exc = exc or RuntimeError("transport unavailable")
        self.attempts = 0

    def deliver(self, event: Event) -> None:
        self.attempts += 1
        raise self._exc


class _RecordingTransport(DeliveryTransport):
    def __init__(self) -> None:
        self.delivered: list[str] = []

    def deliver(self, event: Event) -> None:
        self.delivered.append(event.id)


class _CoversAll(MeteredWatermark):
    def covers(self, event_id: str) -> bool:
        return True


class _RaisingWatermark(MeteredWatermark):
    def covers(self, event_id: str) -> bool:
        raise RuntimeError("attestation unavailable")


@pytest.fixture
def db() -> EventDatabase:
    database = EventDatabase("sqlite://")
    database.create_all()
    yield database
    database.close()


@pytest.fixture
def spine(db: EventDatabase) -> SqlDurableEventSpine:
    return SqlDurableEventSpine(db, clock=lambda: RECORDED_AT)


def _permanent(_exc: Exception) -> DeliveryOutcome:
    return DeliveryOutcome.PERMANENT_FAILURE


def _transient(_exc: Exception) -> DeliveryOutcome:
    return DeliveryOutcome.TRANSIENT_FAILURE


def _retention(db: EventDatabase, watermark: MeteredWatermark) -> EventRetention:
    return EventRetention(SqlRetentionStore(db), watermark, RETENTION)


# ── DRILL 1 — durable-write failure (HD6.1, ADR-0035 DW4) ───────────────────────


class _BrokenLog:
    """A durable sink whose append always fails."""

    def append(self, event: Event) -> bool:
        raise RuntimeError("disk full")

    def append_many(self, events):  # pragma: no cover — the bus appends one at a time
        raise RuntimeError("disk full")


def test_drill_a_failed_durable_write_reaches_neither_producer_nor_subscriber(db):
    """DW4 composed with the M4.1 dispatch contract: the producer is not raised into, the event is
    not dispatched, and the failure is visible to an operator — all three, in one scenario."""
    seen: list[Event] = []
    failures: list[tuple[str, str]] = []
    bus = InProcessEventBus(
        clock=lambda: RECORDED_AT,
        durable=_BrokenLog(),
        on_durable_error=lambda event, exc: failures.append((event.id, type(exc).__name__)),
    )
    bus.subscribe(seen.append, types=EventType.TURN_STARTED)
    publisher = bus.publisher(EventMetadata(session_id="drill-1", source=EventSource.TURN_ENGINE))

    published = publisher.publish(EventType.TURN_STARTED, {"k": 1})

    assert published is not None, "the producer receives its envelope, not an exception (AR-31)"
    assert seen == [], "a durably-unwritten event must never be dispatched (DW4)"
    assert [f[0] for f in failures] == [published.id], "the failure is surfaced exactly once"
    assert bus.durable_failures.total == 1


def test_drill_a_failed_durable_write_leaves_neither_row_behind(db, spine):
    """DW1 composed with DW4: the event row and the outbox row share one transaction, so a write
    that fails mid-flight leaves **neither** — no orphan event, no orphan entry, nothing for
    retention or recovery to find later."""
    original = db.session.commit

    def _explode() -> None:
        raise RuntimeError("disk full")

    db.session.commit = _explode
    try:
        with pytest.raises(RuntimeError):
            spine.append(_event(1))
    finally:
        db.session.commit = original

    assert spine.count() == 0, "no orphan evt_event row"
    assert spine.count_entries() == 0, "and no orphan evt_outbox row"


# ── DRILL 2 — transient failure → RETRYING (HD6.2) ──────────────────────────────


def test_drill_a_transient_failure_lands_in_retrying_and_is_not_drained(db, spine):
    """Composition: the ladder's `RETRYING` state must also read as **un-drained** to retention,
    so a retryable event cannot be deleted while it still has attempts left."""
    spine.append(_event(1))
    recovery = OutboxRecovery(spine, spine, _FailingTransport(), _transient, clock=lambda: NOW)
    recovery.recover(now=NOW)

    entry = spine.get("evt-1")
    assert entry.state is OutboxState.RETRYING
    assert entry.attempt_count == 1
    assert entry.next_attempt_at is not None

    assert _retention(db, _CoversAll()).sweep(now=NOW) == 0, "un-drained must survive retention"
    assert spine.count() == 1


# ── DRILL 3 — retry exhaustion → DEAD_LETTERED (HD6.3, ED7) ─────────────────────


#: A two-rung ladder: the first transient failure enters `RETRYING`, the second exhausts it.
#: `EXHAUST` is only an edge out of `RETRYING` (OUTBOX_LIFECYCLE), so the ladder must actually be
#: entered before it can be spent — a one-rung policy would ask for `PENDING --exhaust-->`, which
#: the machine correctly refuses.
DRILL_LADDER = RetryPolicy(max_attempts=2, base_delay_s=30.0)

#: Comfortably past `base_delay_s`, so the `RETRYING` entry is due on the second sweep. An explicit
#: instant, never a real elapsed wait — nothing here sleeps.
AFTER_BACKOFF = NOW + 3_600.0


def _exhaust(spine: SqlDurableEventSpine, transport: DeliveryTransport) -> None:
    """Two caller-driven sweeps: enter the ladder, then exhaust it.

    Each sweep is an explicit call at an explicit instant. Nothing schedules the next one, and the
    backoff is crossed by *passing a later `now`*, not by waiting (RC2, HD5).
    """
    recovery = OutboxRecovery(spine, spine, transport, _transient, clock=lambda: NOW)
    recovery.recover(now=NOW)
    recovery.recover(now=AFTER_BACKOFF)


def test_drill_retry_exhaustion_dead_letters_and_survives_recovery_and_retention(db):
    """The full cross-milestone path: M10.7 exhausts the ladder, M10.8 must then stop selecting the
    entry, and M10.5 must refuse to delete it — a dead letter keeps its evidence (RT3)."""
    spine = SqlDurableEventSpine(db, clock=lambda: RECORDED_AT, policy=DRILL_LADDER)
    spine.append(_event(1))
    transport = _FailingTransport()

    _exhaust(spine, transport)

    assert spine.get("evt-1").state is OutboxState.DEAD_LETTERED
    assert spine.count_dead_lettered() == 1

    # M10.8: a terminal entry is never selected again, so further sweeps attempt nothing.
    attempts_before = transport.attempts
    recovery = OutboxRecovery(spine, spine, transport, _transient, clock=lambda: NOW)
    assert recovery.recover(now=AFTER_BACKOFF) == 0
    assert transport.attempts == attempts_before

    # M10.5: `DEAD_LETTERED` is terminal but **not drained**, so the event stays.
    assert _retention(db, _CoversAll()).sweep(now=NOW) == 0
    assert spine.count() == 1
    assert len(spine.failures("evt-1")) >= 1, "the failure evidence survives"


# ── DRILL 4 — permanent failure → QUARANTINED (HD6.4, ED8) ──────────────────────


def test_drill_a_permanent_failure_quarantines_without_consuming_the_ladder(db, spine):
    """ED8 composed with retention: quarantine is reached without spending retries, and the
    quarantined event and its reason both survive a sweep."""
    spine.append(_event(1))
    transport = _FailingTransport(ValueError("malformed endpoint"))
    OutboxRecovery(spine, spine, transport, _permanent, clock=lambda: NOW).recover(now=NOW)

    entry = spine.get("evt-1")
    assert entry.state is OutboxState.QUARANTINED
    assert entry.attempt_count == 1, "one attempt begun; the ladder itself is not consumed"
    assert entry.quarantine_reason
    assert spine.count_quarantined() == 1

    assert _retention(db, _CoversAll()).sweep(now=NOW) == 0
    assert spine.get("evt-1").quarantine_reason, "the operator's reason is still readable"


# ── DRILL 5 — recovery-path failure (HD6.5) ─────────────────────────────────────


def test_drill_recovery_survives_a_transport_that_always_fails(db, spine):
    """A sweep that delivers nothing still returns the attempts it made — a fully-failing sweep
    must not be indistinguishable from an empty one."""
    for n in (1, 2, 3):
        spine.append(_event(n))
    transport = _FailingTransport()
    recovery = OutboxRecovery(spine, spine, transport, _transient, clock=lambda: NOW)

    assert recovery.recover(now=NOW) == 3
    assert transport.attempts == 3
    assert spine.count_pending() == 0
    assert all(spine.get(f"evt-{n}").state is OutboxState.RETRYING for n in (1, 2, 3))


def test_drill_a_classifier_that_raises_does_not_corrupt_outbox_state(db, spine):
    """The classifier is the caller's taxonomy (RL5). If it raises, the sweep fails loudly and the
    entry keeps a state the machine authorized — never a half-applied transition."""

    def _boom(_exc: Exception) -> DeliveryOutcome:
        raise KeyError("unclassifiable")

    spine.append(_event(1))
    recovery = OutboxRecovery(spine, spine, _FailingTransport(), _boom, clock=lambda: NOW)

    with pytest.raises(KeyError):
        recovery.recover(now=NOW)

    entry = spine.get("evt-1")
    assert entry.state in {OutboxState.PENDING, OutboxState.RETRYING}
    assert entry.attempt_count == 1, "the attempt was counted before delivery (RL8)"


def test_drill_a_delivered_entry_is_never_re_attempted(db, spine):
    """`DELIVERED` is terminal, so a second sweep is a no-op — the property retention relies on
    when it treats `DELIVERED` as the sole drained state (RT1)."""
    spine.append(_event(1))
    transport = _RecordingTransport()
    recovery = OutboxRecovery(spine, spine, transport, _transient, clock=lambda: NOW)

    assert recovery.recover(now=NOW) == 1
    assert recovery.recover(now=NOW) == 0
    assert transport.delivered == ["evt-1"]


# ── DRILL 6 — observer failure (HD6.6) ──────────────────────────────────────────


def test_drill_a_raising_subscriber_does_not_stop_the_other_subscribers(db):
    """Subscriber isolation, composed with durability: a broken consumer breaks neither its peers
    nor the durable record."""
    spine = SqlDurableEventSpine(db, clock=lambda: RECORDED_AT)
    bus = InProcessEventBus(clock=lambda: RECORDED_AT, durable=spine)
    healthy: list[Event] = []

    def _explode(_event: Event) -> None:
        raise RuntimeError("subscriber is broken")

    bus.subscribe(_explode, types=EventType.TURN_STARTED)
    bus.subscribe(healthy.append, types=EventType.TURN_STARTED)
    publisher = bus.publisher(EventMetadata(session_id="drill-1", source=EventSource.TURN_ENGINE))

    publisher.publish(EventType.TURN_STARTED, {"k": 1})

    assert len(healthy) == 1, "a raising subscriber must not starve its peers"
    assert spine.count() == 1, "and must not lose the durable record"


def test_drill_a_broken_publisher_surfaces_at_the_tap_not_inside_the_turn():
    """Where observer failure is *isolated* is the point, and it is not the tap.

    `TurnLatencyTap` deliberately does **not** swallow a publisher error — `RuntimePipeline` is
    what isolates a raising observer, which M10.9 already proves end-to-end
    (`test_a_raising_observer_never_breaks_the_conversation`). Re-driving a pipeline here would
    duplicate that unit test rather than compose anything (HD4), so this drill pins the division
    of responsibility instead: the tap propagates, and **isolation is the pipeline's job**.

    `asyncio.run` drives the coroutine deterministically — execution, not scheduling (HV7).
    """
    from observability.turn_latency import TurnLatencyTap

    class _AngryPublisher:
        def publish(self, *args, **kwargs):
            raise RuntimeError("bus is down")

    ticks = iter([0.0, 0.01, 0.02, 0.03, 0.04])
    tap = TurnLatencyTap(_AngryPublisher(), clock=lambda: next(ticks))

    async def _turn_without_isolation() -> None:
        tap.record("s-1", 1, "speech_end")
        tap.record("s-1", 1, "stt_final")
        tap.complete("s-1", 1, "completed")

    with pytest.raises(RuntimeError, match="bus is down"):
        asyncio.run(_turn_without_isolation())

    # …and the failure arrives only at completion: the intermediate boundaries published nothing,
    # so nothing on the hot path could have raised (ADR-0038 TP10).
    quiet = TurnLatencyTap(_AngryPublisher(), clock=lambda: 0.0)
    quiet.record("s-1", 2, "speech_end")
    quiet.record("s-1", 2, "stt_final")
    assert quiet.in_flight == 1, "boundaries were captured without publishing"


# ── DRILL 7 — retention retain-on-raise (HD6.7, ADR-0041 RT8) ───────────────────


def test_drill_retention_retains_when_the_attestation_cannot_answer(db, spine):
    """RT8 composed with a real delivered event: every other guard passes, and the sweep still
    deletes nothing because one guard could not be evaluated."""
    spine.append(_event(1))
    spine.mark_delivered("evt-1")

    assert _retention(db, _RaisingWatermark()).sweep(now=NOW) == 0
    assert spine.count() == 1, "retain too much, never delete too early"


def test_drill_a_delivered_metered_event_is_the_only_one_deleted(db, spine):
    """The positive control for the four drills above: with all five guards satisfied the event
    *is* deleted, so their retention assertions prove a guard rather than a broken sweep."""
    spine.append(_event(1))
    spine.mark_delivered("evt-1")
    spine.append(_event(2))  # left PENDING

    assert _retention(db, _CoversAll()).sweep(now=NOW) == 1
    assert [e.id for e in spine.read_all()] == ["evt-2"]


# ── HV6 — determinism across repeated runs ──────────────────────────────────────


def _drill_outcome() -> tuple:
    """One complete drill, run end to end, reduced to a comparable value."""
    database = EventDatabase("sqlite://")
    database.create_all()
    try:
        spine = SqlDurableEventSpine(database, clock=lambda: RECORDED_AT, policy=DRILL_LADDER)
        spine.append(_event(1))
        spine.append(_event(2))
        spine.mark_delivered("evt-2")
        _exhaust(spine, _FailingTransport())
        deleted = _retention(database, _CoversAll()).sweep(now=NOW)
        return (
            spine.get("evt-1").state,
            spine.count_dead_lettered(),
            spine.count_quarantined(),
            spine.count_pending(),
            deleted,
            tuple(e.id for e in spine.read_all()),
        )
    finally:
        database.close()


def test_drills_are_deterministic_across_repeated_runs():
    """HV6 — identical outcomes across repeated runs. No wall clock is read anywhere on the path:
    every clock is injected, so the answer cannot depend on when the suite ran."""
    outcomes = [_drill_outcome() for _ in range(5)]
    assert len(set(outcomes)) == 1, f"drill outcome varied across runs: {set(outcomes)}"


def test_the_drill_outcome_is_the_expected_one():
    """Determinism is worthless if the fixed point is wrong; pin the value as well as its
    stability."""
    state, dlq, quarantined, pending, deleted, remaining = _drill_outcome()
    assert state is OutboxState.DEAD_LETTERED
    assert (dlq, quarantined, pending) == (1, 0, 0)
    assert deleted == 1, "only the delivered+metered event is removed"
    assert remaining == ("evt-1",)


# ── HD5 / HV7 — drills stay caller-driven ───────────────────────────────────────


def _scheduling_constructs(path: pathlib.Path) -> set[str]:
    """Scheduling constructs actually *called or imported* by a module.

    Structural, not textual. This guard necessarily **names** the constructs it forbids, so a
    substring scan would match its own token list and its own docstring — the failure mode that
    has now bitten three separate Epic 10 guards. Walking the AST looks at what the module does
    instead of what it says.

    `asyncio.run(...)` is deliberately absent from the forbidden set: driving one turn to
    completion is deterministic execution, not scheduling (**HV7**).
    """
    forbidden_calls = {("time", "sleep"), ("asyncio", "sleep")}
    forbidden_imports = {"threading", "sched", "concurrent"}
    found: set[str] = set()
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            value = node.func.value
            if isinstance(value, ast.Name) and (value.id, node.func.attr) in forbidden_calls:
                found.add(f"{value.id}.{node.func.attr}()")
            if node.func.attr == "start" and isinstance(value, ast.Call):
                inner = value.func
                if isinstance(inner, ast.Name) and inner.id in {"Timer", "Thread"}:
                    found.add(f"{inner.id}().start()")
        elif isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names} & forbidden_imports
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            if node.module.split(".")[0] in forbidden_imports:
                found.add(node.module.split(".")[0])
        elif isinstance(node, ast.While):
            test = node.test
            if isinstance(test, ast.Constant) and test.value is True:
                found.add("while True")
    return found


#: Every file M10.12 adds or modifies — the exact scope HV7 permits this check to cover.
#: Pre-existing modules outside this list are **not** in scope, which is what stops the guard
#: failing against `runtime/**`'s legitimate `asyncio` use.
M10_12_FILES = (
    "tests/test_epic10_failure_drills.py",
    "events/ports.py",
    "events/persistence/spine.py",
    "tests/test_events_boundary.py",
    "tests/test_events_outbox.py",
)


def test_no_m10_12_file_introduces_a_scheduling_construct():
    """HV7, scoped to M10.12's own artifacts (ADR-0042, corrected wording)."""
    for name in M10_12_FILES:
        found = _scheduling_constructs(pathlib.Path(name))
        assert not found, f"{name} introduces scheduling construct(s): {sorted(found)}"


def test_the_drills_do_use_asyncio_run():
    """The permission in HV7 is load-bearing, so prove it is exercised rather than theoretical —
    if no drill ever drives a turn, the carve-out should be withdrawn, not kept 'just in case'."""
    tree = ast.parse(pathlib.Path(__file__).read_text())
    runs = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "run"
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "asyncio"
    ]
    assert runs, "no asyncio.run(...) present; the HV7 carve-out is unused"


def test_the_drills_add_no_production_module():
    """HI4 — drills are test-only. M10.12 adds exactly two port methods and their implementation;
    it adds no module under any production package."""
    for package in ("events", "api", "runtime", "observability", "core"):
        for path in pathlib.Path(package).rglob("*.py"):
            assert "drill" not in path.name, f"a drill leaked into production: {path}"
