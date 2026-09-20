"""Outbox recovery and restart re-dispatch (M10.8) — the executable form of ADR-0037.

The first tests in this repository in which a durable event is actually *delivered*. M10.6 proved
it could be persisted and M10.7 proved a failure could be decided; here the two finally meet, so
these tests are as much about what recovery **refuses** to touch as about what it delivers.

Every transport is a fake and every clock is frozen (ADR-0034 DS11): nothing here sleeps, reads a
wall clock, opens a socket or starts a thread. Restart durability uses file-backed SQLite under
`tmp_path`, never in-memory (Epic 10 plan §1.10).
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from core.events import Event, EventSource, EventType
from core.fsm import TerminalStateError
from core.policies import RetryPolicy
from events.persistence import EventDatabase, SqlDurableEventSpine
from events.ports import DeliveryOutcome, DeliveryTransport, OutboxState
from events.recovery import OutboxRecovery, default_classifier


def code_of(path: str) -> str:
    """Executable source only — docstrings and comments stripped.

    `recovery.py` documents at length that it runs no worker, no loop and no timer. Matching raw
    text would flag the prose that exists to prevent the very thing being checked.
    """
    tree = ast.parse(pathlib.Path(path).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


class FrozenClock:
    """Deterministic injected clock. Never ambient, never `time.time()`."""

    def __init__(self, start: float = 1000.0, step: float = 0.0) -> None:
        self.now = start
        self.step = step

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value


def make_event(event_id: str, *, seq: int = 1, session_id: str = "s-1") -> Event:
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
        data={},
        metadata={},
    )


# ── fake transports (test-only; ADR-0037 RC9 ships none in production) ──────


class RecordingTransport(DeliveryTransport):
    """Succeeds, and remembers what it was handed."""

    def __init__(self) -> None:
        self.delivered: list[Event] = []

    def deliver(self, event: Event) -> None:
        self.delivered.append(event)


class FailingTransport(DeliveryTransport):
    """Always raises. `exc_type` chooses what the classifier will see."""

    def __init__(self, exc_type: type[Exception] = RuntimeError) -> None:
        self.exc_type = exc_type
        self.attempts: list[str] = []

    def deliver(self, event: Event) -> None:
        self.attempts.append(event.id)
        raise self.exc_type("transport unavailable")


class CountingTransport(DeliveryTransport):
    """Succeeds, counting calls — for `limit` and termination proofs."""

    def __init__(self) -> None:
        self.calls = 0

    def deliver(self, event: Event) -> None:
        self.calls += 1


PERMANENT = staticmethod(lambda exc: DeliveryOutcome.PERMANENT_FAILURE)


@pytest.fixture
def db():
    database = EventDatabase()
    database.create_all()
    yield database
    database.close()


@pytest.fixture
def spine(db):
    return SqlDurableEventSpine(db, clock=FrozenClock())


def recovery(spine, transport, classifier=None, *, clock=None):
    return OutboxRecovery(
        spine, spine, transport, classifier, clock=clock or FrozenClock()
    )


def seeded(spine, event_id: str = "e-1", *, seq: int = 1) -> str:
    spine.append(make_event(event_id, seq=seq))
    return event_id


# ── A. clean PENDING recovery ───────────────────────────────────────────────


def test_a_pending_entry_is_recovered_and_delivered(spine):
    """(1) The whole point of M10.8: a persisted, never-attempted event finally goes out."""
    seeded(spine)
    transport = RecordingTransport()

    assert recovery(spine, transport).recover(now=1000.0) == 1

    assert [e.id for e in transport.delivered] == ["e-1"]
    assert spine.get("e-1").state is OutboxState.DELIVERED


def test_the_delivered_envelope_is_the_one_in_the_durable_log(spine):
    """Recovery reads `evt_event`, not a reconstruction of its own: the outbox holds no payload."""
    original = make_event("e-1", seq=7, session_id="s-9")
    spine.append(original)
    transport = RecordingTransport()

    recovery(spine, transport).recover(now=1000.0)

    assert transport.delivered == [original]


def test_recovering_an_empty_outbox_attempts_nothing(spine):
    transport = RecordingTransport()
    assert recovery(spine, transport).recover(now=1000.0) == 0
    assert transport.delivered == []


# ── B. due / not-due RETRYING ───────────────────────────────────────────────


def retrying(spine, event_id="e-1", *, seq=1):
    """Drive one entry into RETRYING with `next_attempt_at == 1030.0`."""
    seeded(spine, event_id, seq=seq)
    spine.begin_attempt(event_id)
    spine.record_failure(event_id, DeliveryOutcome.TRANSIENT_FAILURE, error="boom")
    entry = spine.get(event_id)
    assert entry.state is OutboxState.RETRYING
    return entry


def test_b_a_due_retrying_entry_is_recovered(spine):
    """(2)"""
    entry = retrying(spine)
    transport = RecordingTransport()

    assert recovery(spine, transport).recover(now=entry.next_attempt_at + 1) == 1
    assert spine.get("e-1").state is OutboxState.DELIVERED


def test_a_retrying_entry_due_exactly_now_is_recovered(spine):
    """The boundary is inclusive — `due()` filters `next_attempt_at <= now`."""
    entry = retrying(spine)
    transport = RecordingTransport()

    assert recovery(spine, transport).recover(now=entry.next_attempt_at) == 1
    assert spine.get("e-1").state is OutboxState.DELIVERED


def test_c_a_future_retrying_entry_is_left_completely_untouched(spine):
    """(3) Not attempted, not counted, and — the part that matters — not mutated."""
    before = retrying(spine)
    transport = RecordingTransport()

    assert recovery(spine, transport).recover(now=before.next_attempt_at - 1) == 0

    after = spine.get("e-1")
    assert transport.delivered == []
    assert (after.state, after.attempt_count, after.next_attempt_at) == (
        before.state,
        before.attempt_count,
        before.next_attempt_at,
    )


# ── C. terminal states are never selected ───────────────────────────────────


def test_d_a_delivered_entry_is_never_selected(spine):
    """(4)"""
    seeded(spine)
    spine.mark_delivered("e-1")
    transport = RecordingTransport()

    assert recovery(spine, transport).recover(now=9_999.0) == 0
    assert transport.delivered == []


def test_e_a_dead_lettered_entry_is_never_selected(spine):
    """(5) Exhaustion is terminal, and M10.8 opens no redrive path (RC8/RL3)."""
    seeded(spine)
    # `EXHAUST` is reachable only from `RETRYING` (ADR-0036 RL2), so the first transient failure
    # enters the ladder and the second — at `max_attempts` — exhausts it.
    policy = RetryPolicy(max_attempts=2, base_delay_s=1.0, multiplier=2.0, max_delay_s=10.0)
    for _ in range(2):
        spine.begin_attempt("e-1")
        spine.record_failure("e-1", DeliveryOutcome.TRANSIENT_FAILURE, error="x", policy=policy)
    assert spine.get("e-1").state is OutboxState.DEAD_LETTERED

    transport = RecordingTransport()
    assert recovery(spine, transport).recover(now=9_999.0) == 0
    assert transport.delivered == []


def test_f_a_quarantined_entry_is_never_selected(spine):
    """(6) Deterministically futile stays futile — no automatic restart (RC7/RC8)."""
    seeded(spine)
    spine.record_failure("e-1", DeliveryOutcome.PERMANENT_FAILURE, error="bad payload")
    assert spine.get("e-1").state is OutboxState.QUARANTINED

    transport = RecordingTransport()
    assert recovery(spine, transport).recover(now=9_999.0) == 0
    assert transport.delivered == []


def test_a_table_of_only_terminal_entries_performs_zero_writes(spine):
    """RV4 — recovery over finished work is a no-op, not a refusal it has to catch."""
    seeded(spine, "e-1", seq=1)
    spine.mark_delivered("e-1")
    seeded(spine, "e-2", seq=2)
    spine.record_failure("e-2", DeliveryOutcome.PERMANENT_FAILURE, error="x")
    snapshot = {i: spine.get(i) for i in ("e-1", "e-2")}

    assert recovery(spine, RecordingTransport()).recover(now=9_999.0) == 0
    assert {i: spine.get(i) for i in ("e-1", "e-2")} == snapshot


def test_terminal_entries_are_absent_from_candidates(spine):
    """The selection queries exclude them structurally — not a guard recovery has to remember."""
    seeded(spine, "e-1", seq=1)
    spine.mark_delivered("e-1")
    seeded(spine, "e-2", seq=2)

    picked = recovery(spine, RecordingTransport()).candidates(9_999.0)
    assert [e.event_id for e in picked] == ["e-2"]


# ── D. ordering ─────────────────────────────────────────────────────────────


def test_g_candidates_are_ordered_by_seq_then_event_id(spine):
    """(7) Deterministic across calls and restarts — and across the pending/due union."""
    for eid, seq in (("e-c", 3), ("e-a", 1), ("e-b", 2)):
        seeded(spine, eid, seq=seq)

    picked = recovery(spine, RecordingTransport()).candidates(9_999.0)
    assert [e.event_id for e in picked] == ["e-a", "e-b", "e-c"]


def test_ordering_holds_across_the_pending_and_retrying_union(spine):
    """A merged sweep must be ordered as a whole, not merely within each half."""
    seeded(spine, "e-4", seq=4)  # PENDING
    retrying(spine, "e-2", seq=2)  # RETRYING, due at 1030.0
    seeded(spine, "e-6", seq=6)  # PENDING

    picked = recovery(spine, RecordingTransport()).candidates(2_000.0)
    assert [e.event_id for e in picked] == ["e-2", "e-4", "e-6"]


def test_event_id_breaks_ties_at_equal_seq(spine):
    seeded(spine, "e-z", seq=5)
    seeded(spine, "e-a", seq=5)

    picked = recovery(spine, RecordingTransport()).candidates(9_999.0)
    assert [e.event_id for e in picked] == ["e-a", "e-z"]


def test_delivery_happens_in_candidate_order(spine):
    for eid, seq in (("e-c", 3), ("e-a", 1), ("e-b", 2)):
        seeded(spine, eid, seq=seq)
    transport = RecordingTransport()

    recovery(spine, transport).recover(now=9_999.0)
    assert [e.id for e in transport.delivered] == ["e-a", "e-b", "e-c"]


# ── E. limit ────────────────────────────────────────────────────────────────


def test_h_limit_none_processes_every_eligible_candidate(spine):
    """(8)"""
    for n in range(5):
        seeded(spine, f"e-{n}", seq=n)
    transport = CountingTransport()

    assert recovery(spine, transport).recover(now=9_999.0, limit=None) == 5
    assert transport.calls == 5


def test_i_a_finite_limit_processes_at_most_that_many(spine):
    """(9)"""
    for n in range(5):
        seeded(spine, f"e-{n}", seq=n)
    transport = CountingTransport()

    assert recovery(spine, transport).recover(now=9_999.0, limit=2) == 2
    assert transport.calls == 2


def test_a_limit_takes_the_first_candidates_in_order(spine):
    """`limit` is only meaningful because the order is total — RC4."""
    for eid, seq in (("e-c", 3), ("e-a", 1), ("e-b", 2)):
        seeded(spine, eid, seq=seq)
    transport = RecordingTransport()

    recovery(spine, transport).recover(now=9_999.0, limit=2)
    assert [e.id for e in transport.delivered] == ["e-a", "e-b"]


def test_a_limit_larger_than_the_backlog_is_harmless(spine):
    seeded(spine)
    assert recovery(spine, CountingTransport()).recover(now=9_999.0, limit=100) == 1


@pytest.mark.parametrize("bad", [0, -1, -100])
def test_a_non_positive_limit_is_refused_loudly(spine, bad):
    """RC12 — silently returning 0 would look like "nothing was due" and hide a caller's bug."""
    with pytest.raises(ValueError, match="positive integer"):
        recovery(spine, RecordingTransport()).recover(now=9_999.0, limit=bad)


def test_a_refused_limit_attempts_nothing(spine):
    seeded(spine)
    transport = RecordingTransport()
    with pytest.raises(ValueError):
        recovery(spine, transport).recover(now=9_999.0, limit=0)
    assert transport.delivered == []
    assert spine.get("e-1").state is OutboxState.PENDING


# ── F. attempt protocol ─────────────────────────────────────────────────────


def test_j_a_pending_entrys_first_attempt_is_attempt_one(spine):
    """(10)"""
    seeded(spine)
    recovery(spine, FailingTransport()).recover(now=1000.0)
    assert spine.get("e-1").attempt_count == 1


def test_k_the_attempt_is_counted_before_the_outcome_is_known(spine):
    """(11) RL8 — asserted from *inside* the transport, mid-attempt, where it is provable."""
    seeded(spine)
    seen: list[int] = []

    class Observing(DeliveryTransport):
        def deliver(self, event: Event) -> None:
            seen.append(spine.get(event.id).attempt_count)

    recovery(spine, Observing()).recover(now=1000.0)
    assert seen == [1], "attempt_count must already be 1 while delivery is in flight"


def test_a_successful_attempt_records_no_failure_history(spine):
    seeded(spine)
    recovery(spine, RecordingTransport()).recover(now=1000.0)
    assert spine.failures("e-1") == ()


# ── G. ladder integration (M10.7 semantics, unchanged) ──────────────────────


def test_l_a_transient_failure_follows_the_m107_ladder(spine):
    """(12) Backoff asserted by value — possible only because RL7 forbids jitter."""
    seeded(spine)
    recovery(spine, FailingTransport()).recover(now=1000.0)

    entry = spine.get("e-1")
    assert entry.state is OutboxState.RETRYING
    assert entry.next_attempt_at == 1030.0


def test_m_a_permanent_failure_quarantines_without_consuming_the_ladder(spine):
    """(13) ED8 — quarantine does not spend an attempt."""
    seeded(spine)
    spine.begin_attempt("e-1")  # attempt 1 already made by some earlier caller
    before = spine.get("e-1").attempt_count

    recovery(spine, FailingTransport(), PERMANENT).recover(now=1000.0)

    entry = spine.get("e-1")
    assert entry.state is OutboxState.QUARANTINED
    # recovery's own begin_attempt incremented once; the *failure* consumed nothing beyond it
    assert entry.attempt_count == before + 1
    assert entry.quarantine_reason


def test_n_exhaustion_reaches_dead_letter_at_exactly_max_attempts(spine):
    """(14) Bounded, and bounded at the configured number — ED7."""
    seeded(spine)
    transport = FailingTransport()
    driver = recovery(spine, transport)
    maximum = spine.ladder.policy.max_attempts

    states = []
    for _ in range(maximum + 2):
        driver.recover(now=9_999.0)
        states.append(spine.get("e-1").state)

    assert states[maximum - 1] is OutboxState.DEAD_LETTERED
    assert states[maximum - 2] is OutboxState.RETRYING
    assert spine.get("e-1").attempt_count == maximum
    # once terminal it stops being selected, so the transport is never called again
    assert len(transport.attempts) == maximum


def test_o_success_reaches_delivered(spine):
    """(15)"""
    seeded(spine)
    recovery(spine, RecordingTransport()).recover(now=1000.0)
    assert spine.get("e-1").state is OutboxState.DELIVERED


def test_p_failure_history_is_preserved_across_attempts(spine):
    """(16) One row per failed attempt, ordered, never rewritten by recovery."""
    seeded(spine)
    driver = recovery(spine, FailingTransport())
    for _ in range(3):
        driver.recover(now=9_999.0)

    failures = spine.failures("e-1")
    assert [f.attempt for f in failures] == [1, 2, 3]
    assert all(f.outcome is DeliveryOutcome.TRANSIENT_FAILURE for f in failures)
    assert all("transport unavailable" in f.error for f in failures)


def test_recovery_never_rewrites_existing_history(spine):
    seeded(spine)
    driver = recovery(spine, FailingTransport())
    driver.recover(now=9_999.0)
    first = spine.failures("e-1")[0]

    driver.recover(now=9_999.0)
    assert spine.failures("e-1")[0] == first


def test_a_recovered_transient_failure_can_still_succeed_later(spine):
    """RETRYING -> DELIVERED is a ratified edge; recovery uses it, and adds none."""
    seeded(spine)
    recovery(spine, FailingTransport()).recover(now=1000.0)
    assert spine.get("e-1").state is OutboxState.RETRYING

    transport = RecordingTransport()
    assert recovery(spine, transport).recover(now=9_999.0) == 1
    assert spine.get("e-1").state is OutboxState.DELIVERED


# ── H. restart durability (file-backed, never in-memory) ────────────────────


def test_q_restart_preserves_attempt_count(tmp_path):
    """(17)"""
    url = f"sqlite:///{tmp_path/'evt.db'}"
    first = EventDatabase(url)
    first.create_all()
    spine = SqlDurableEventSpine(first, clock=FrozenClock())
    spine.append(make_event("e-1"))
    recovery(spine, FailingTransport()).recover(now=1000.0)
    first.close()

    reopened = EventDatabase(url)
    revived = SqlDurableEventSpine(reopened, clock=FrozenClock())
    assert revived.get("e-1").attempt_count == 1
    reopened.close()


def test_r_restart_preserves_next_attempt_at(tmp_path):
    """(18) An absolute instant (RL14) — a restart must not reset the ladder."""
    url = f"sqlite:///{tmp_path/'evt.db'}"
    first = EventDatabase(url)
    first.create_all()
    spine = SqlDurableEventSpine(first, clock=FrozenClock())
    spine.append(make_event("e-1"))
    recovery(spine, FailingTransport()).recover(now=1000.0)
    first.close()

    reopened = EventDatabase(url)
    revived = SqlDurableEventSpine(reopened, clock=FrozenClock())
    assert revived.get("e-1").next_attempt_at == 1030.0
    reopened.close()


def test_s_restart_preserves_terminal_states(tmp_path):
    """(19)"""
    url = f"sqlite:///{tmp_path/'evt.db'}"
    first = EventDatabase(url)
    first.create_all()
    spine = SqlDurableEventSpine(first, clock=FrozenClock())
    spine.append(make_event("e-1", seq=1))
    spine.append(make_event("e-2", seq=2))
    recovery(spine, RecordingTransport()).recover(now=1000.0, limit=1)
    spine.record_failure("e-2", DeliveryOutcome.PERMANENT_FAILURE, error="bad")
    first.close()

    reopened = EventDatabase(url)
    revived = SqlDurableEventSpine(reopened, clock=FrozenClock())
    assert revived.get("e-1").state is OutboxState.DELIVERED
    assert revived.get("e-2").state is OutboxState.QUARANTINED
    reopened.close()


def test_recovery_after_restart_picks_up_the_pending_backlog(tmp_path):
    """The whole reason M10.8 exists: work persisted before a crash still goes out after it."""
    url = f"sqlite:///{tmp_path/'evt.db'}"
    first = EventDatabase(url)
    first.create_all()
    spine = SqlDurableEventSpine(first, clock=FrozenClock())
    for n in range(3):
        spine.append(make_event(f"e-{n}", seq=n))
    first.close()  # crash: nothing was ever delivered

    reopened = EventDatabase(url)
    revived = SqlDurableEventSpine(reopened, clock=FrozenClock())
    transport = RecordingTransport()
    assert recovery(revived, transport).recover(now=9_999.0) == 3
    assert [e.id for e in transport.delivered] == ["e-0", "e-1", "e-2"]
    reopened.close()


# ── I. crash windows and duplicates ─────────────────────────────────────────


def test_t_crash_window_duplicate_delivery_is_permitted(spine):
    """(20) Delivery succeeded, `DELIVERED` never committed — recovery redelivers.

    The duplicate is **correct** under ED2/ED12, not a defect: consumers are idempotent on
    `(event_id, meter_code)`. Simulated by a transport that delivers and then fails the commit.
    """
    seeded(spine)
    transport = RecordingTransport()

    class DeliversThenCrashes(DeliveryTransport):
        def deliver(self, event: Event) -> None:
            transport.deliver(event)  # the consumer really did receive it
            raise RuntimeError("crashed before the DELIVERED commit")

    recovery(spine, DeliversThenCrashes()).recover(now=1000.0)
    assert spine.get("e-1").state is OutboxState.RETRYING  # not DELIVERED

    recovery(spine, transport).recover(now=9_999.0)
    assert [e.id for e in transport.delivered] == ["e-1", "e-1"], "at-least-once, by design"
    assert spine.get("e-1").state is OutboxState.DELIVERED


def test_a_crash_between_begin_attempt_and_outcome_consumes_the_attempt(spine):
    """RL8 — a bound a crash can widen is not a bound."""
    seeded(spine)
    spine.begin_attempt("e-1")  # attempt starts; process dies before any outcome is recorded
    assert spine.get("e-1").attempt_count == 1
    assert spine.failures("e-1") == ()  # no evidence row — RL13 atomicity

    recovery(spine, FailingTransport()).recover(now=1000.0)
    assert spine.get("e-1").attempt_count == 2, "the crashed attempt was not refunded"


def test_a_partially_completed_sweep_keeps_what_it_finished(spine):
    """Entries are independent; an interrupted sweep durably completed what it got to."""
    for n in range(4):
        seeded(spine, f"e-{n}", seq=n)

    class ExplodesOnThird(DeliveryTransport):
        def __init__(self) -> None:
            self.seen = 0

        def deliver(self, event: Event) -> None:
            self.seen += 1
            if self.seen == 3:
                raise KeyboardInterrupt("process killed mid-sweep")

    with pytest.raises(KeyboardInterrupt):
        recovery(spine, ExplodesOnThird()).recover(now=9_999.0)

    assert spine.get("e-0").state is OutboxState.DELIVERED
    assert spine.get("e-1").state is OutboxState.DELIVERED
    assert spine.get("e-3").state is OutboxState.PENDING


# ── J. idempotence and termination ──────────────────────────────────────────


def test_u_recovering_twice_over_delivered_work_is_a_no_op(spine):
    seeded(spine)
    driver = recovery(spine, RecordingTransport())
    assert driver.recover(now=1000.0) == 1
    assert driver.recover(now=1000.0) == 0


def test_v_recover_terminates_and_returns_the_attempted_count(spine):
    """(30) Attempts, not successes — a fully-failing sweep must not look like an empty one."""
    for n in range(3):
        seeded(spine, f"e-{n}", seq=n)

    assert recovery(spine, FailingTransport()).recover(now=1000.0) == 3


def test_candidates_is_a_pure_query(spine):
    """It is a read. Calling it must not deliver, advance or reschedule anything."""
    seeded(spine)
    before = spine.get("e-1")

    driver = recovery(spine, RecordingTransport())
    driver.candidates(9_999.0)
    driver.candidates(9_999.0)

    assert spine.get("e-1") == before


def test_a_missing_envelope_is_reported_loudly(spine, db):
    """The log and the outbox are written in one transaction and cannot diverge — so if they
    have, recovery says so rather than silently skipping durable work."""
    from events.persistence.models import DurableOutboxRow

    db.session.add(
        DurableOutboxRow(
            event_id="orphan", seq=1, state=OutboxState.PENDING.value, created_at=1.0
        )
    )
    db.session.commit()

    with pytest.raises(LookupError, match="no event in the durable log"):
        recovery(spine, RecordingTransport()).recover(now=9_999.0)


# ── K. clock and classifier injection ───────────────────────────────────────


def test_the_injected_clock_supplies_now_when_it_is_omitted(spine):
    seeded(spine)
    retrying(spine, "e-2", seq=2)  # due at 1030.0

    driver = recovery(spine, RecordingTransport(), clock=FrozenClock(start=1000.0))
    assert driver.recover() == 1  # only the PENDING one; e-2 is not yet due


def test_recovery_without_a_clock_or_now_is_a_loud_caller_error(spine):
    driver = OutboxRecovery(spine, spine, RecordingTransport())
    with pytest.raises(ValueError, match="no clock injected"):
        driver.recover()


def test_the_default_classifier_treats_unknown_failures_as_transient():
    """The conservative direction: quarantine is terminal and permanent misclassification is
    unrecoverable, while transient misclassification ends at DEAD_LETTERED (ED7)."""
    assert default_classifier(RuntimeError("?")) is DeliveryOutcome.TRANSIENT_FAILURE


def test_the_classifier_is_the_callers_taxonomy(spine):
    """RL5 — `events/` never inspects a transport exception type."""
    seeded(spine)

    def classify(exc: Exception) -> DeliveryOutcome:
        return (
            DeliveryOutcome.PERMANENT_FAILURE
            if isinstance(exc, ValueError)
            else DeliveryOutcome.TRANSIENT_FAILURE
        )

    recovery(spine, FailingTransport(ValueError), classify).recover(now=1000.0)
    assert spine.get("e-1").state is OutboxState.QUARANTINED


# ── L. static boundary assertions (ADR-0037 RV13-RV19) ──────────────────────

M108_SOURCES = ("events/recovery.py",)


def test_w_no_worker_scheduler_timer_or_loop_exists():
    """(22) The structural reason recovery stays caller-driven. Executable lines only."""
    banned = (
        "sleep(", "threading", "Thread(", "asyncio", "schedule(", "Timer(",
        "while True", "APScheduler", "cron", "create_task", "run_in_executor",
        "ThreadPoolExecutor", "multiprocessing", "signal.",
    )
    for path in M108_SOURCES:
        body = code_of(path)
        for token in banned:
            assert token not in body, f"{path} executes {token}"


def test_x_no_claim_lease_lock_or_exclusivity_mechanism_exists():
    """(21) ADR-0037 RC11 — concurrency is an accepted non-claim, and this keeps it honest."""
    for path in M108_SOURCES:
        body = code_of(path).lower()
        for token in (
            "for update", "with_for_update", "advisory_lock", "select_for_update",
            "rlock", "semaphore", "claim", "lease", "owner_id", "worker_id",
        ):
            assert token not in body, f"{path} uses {token}"
        # `\block\b` on its own, so the match is a lock and not the tail of `clock`.
        assert not re.search(r"\block\b", body), f"{path} takes a lock"


def test_y_recovery_imports_no_storage_library(spine):
    """(24) It is a driver over ports, not a persistence module — AR-10/AR-15, ADR-0034 DS3/DS4."""
    for path in M108_SOURCES:
        body = code_of(path)
        for token in ("sqlmodel", "sqlalchemy", "events.persistence", "EventDatabase", "session"):
            assert token not in body, f"{path} references {token}"


def test_z_no_concrete_delivery_transport_ships_in_production(spine):
    """(23) ADR-0037 RC9 / owner ruling F-2."""
    for path in M108_SOURCES:
        body = code_of(path)
        assert "DeliveryTransport)" not in body, f"{path} implements DeliveryTransport"
        for token in ("httpx", "requests", "urllib", "http.client", "socket", "aiohttp"):
            assert token not in body, f"{path} performs network I/O"


def test_recovery_does_not_import_runtime_or_upward(spine):
    """(25) Dependency direction unchanged: `core <- events <- runtime`."""
    for path in M108_SOURCES:
        tree = ast.parse(pathlib.Path(path).read_text())
        tops = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                tops |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                tops.add(node.module.split(".")[0])
        assert not (tops & {"runtime", "api", "billing", "providers", "replay", "observability"})


def test_no_retention_metric_or_replay_implementation_entered_m108():
    """(29) M10.5, M10.10 and the frozen `MetricStreamPort` stay untouched — RC14/RC16."""
    for path in M108_SOURCES:
        body = code_of(path)
        for token in (
            "def purge", "def expire", "def apply_retention", "metric.", "MetricStream",
            "MetricSignal", "def replay", "def drain",
        ):
            assert token not in body, f"{path} contains {token}"


def test_aa_webhooks_is_still_byte_for_byte_unchanged():
    """(26) RL10 — M10.8 creates no second delivery mechanism (blueprint R-11)."""
    source = pathlib.Path("events/webhooks.py").read_text()
    assert len(source.splitlines()) == 58
    lowered = source.lower()
    for token in ("outboxrecovery", "recover(", "deliverytransport", "outbox_"):
        assert token not in lowered


def test_ab_the_runtime_acquired_nothing_from_m108():
    """(25) DW10 / RL17 / RC16, **retargeted at M11.12** — the milestone that wires the sink.

    `"durable"` was dropped from the forbidden list because M11.12 legitimately gives the runtime
    an injected, opaque durable *attachment*; every other token is kept, and they are the ones
    that matter. The property is *the runtime acquired nothing* — no recovery machinery, no
    delivery transport, no outbox, no database — and that is still true: it holds a reference it
    was handed and knows the name of none of them."""
    for token in ("OutboxRecovery", "events.recovery", "DeliveryTransport",
                  "EventDatabase", "DurableOutbox"):
        for path in pathlib.Path("runtime").rglob("*.py"):
            assert token not in path.read_text(), f"{path} mentions {token}"


def test_the_orchestrator_constructs_exactly_one_bus():
    """**Retargeted at M11.12**: the bus is no longer plain — it carries the injected sink this
    milestone was authorized to supply. That there is still exactly **one** bus, and that the
    runtime names no storage class, is what this guard now pins (see `test_events_boundary`)."""
    source = pathlib.Path("runtime/orchestrator/orchestrator.py").read_text()
    assert source.count("InProcessEventBus(") == 1
    assert "EventDatabase" not in source


def test_ac_m108_added_no_migration(spine):
    """(28) RC15 — every column recovery reads already exists."""
    from events.persistence.migrations import MIGRATIONS

    assert tuple(m.VERSION for m in MIGRATIONS) == ("0034", "0035", "0036")
    assert not list(pathlib.Path("events/persistence/migrations").glob("v0037*"))


def test_ad_evt_event_is_unchanged(spine):
    """(27) The append-only log gained no delivery state and no update path — ED5."""
    from events.persistence.models import DurableEventRow

    assert DurableEventRow.__tablename__ == "evt_event"
    columns = set(DurableEventRow.model_fields)
    for forbidden in ("state", "attempt_count", "next_attempt_at", "delivered_at"):
        assert forbidden not in columns


def test_ae_m108_added_no_fsm_transition():
    """(RC6) The seven ratified edges were exactly sufficient."""
    from events.ports import OUTBOX_LIFECYCLE, TERMINAL_OUTBOX_STATES

    edges = sum(len(OUTBOX_LIFECYCLE.triggers_from(st)) for st in OUTBOX_LIFECYCLE.states)
    assert edges == 7
    assert TERMINAL_OUTBOX_STATES == frozenset(
        {OutboxState.DELIVERED, OutboxState.DEAD_LETTERED, OutboxState.QUARANTINED}
    )


def test_terminal_entries_still_refuse_mutation_loudly(spine):
    """RC7 — the guard is independent of recovery's good behaviour."""
    seeded(spine)
    spine.mark_delivered("e-1")
    with pytest.raises(TerminalStateError):
        spine.begin_attempt("e-1")
