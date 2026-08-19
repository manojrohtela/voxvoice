"""Retry ladder, dead-letter and quarantine (M10.7) — the executable form of ADR-0036.

Two halves. The **pure** half exercises `OutboxLadder` and `OUTBOX_LIFECYCLE` with no database
at all: they are deterministic functions and a database would only slow the proof down. The
**persisted** half proves the transitions, their evidence and their atomicity against SQLite,
with restart durability on a file-backed database under `tmp_path` (ADR-0034 DS10).

No test sleeps or reads a wall clock. Backoff is asserted **by value** against a frozen clock,
which is possible only because ADR-0036 RL7 forbids jitter.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from core.events import Event, EventSource, EventType
from core.fsm import InvalidTransition, StateMachine, TerminalStateError
from core.policies import RetryPolicy
from events.ladder import DELIVERY_RETRY_POLICY, LadderDecision, OutboxLadder
from events.persistence import (
    DurableOutboxFailureRow,
    DurableOutboxRow,
    EventDatabase,
    SqlDurableEventSpine,
)
from events.ports import (
    OUTBOX_LIFECYCLE,
    TERMINAL_OUTBOX_STATES,
    DeliveryOutcome,
    OutboxState,
    OutboxTrigger,
)


def code_of(path: str) -> str:
    """Executable source only — docstrings and comments stripped.

    The "no worker, no sleep" assertions below are about what the code *does*, and every one of
    these modules documents at length what it deliberately does **not** do. Matching raw text
    would flag the prose that exists to prevent the very thing being checked.
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
    """Deterministic injected clock (ADR-0034 DS11). Never ambient, never `time.time()`."""

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


@pytest.fixture
def spine():
    db = EventDatabase()
    db.create_all()
    yield SqlDurableEventSpine(db, clock=FrozenClock())
    db.close()


def seeded(spine, event_id: str = "e-1", *, seq: int = 1):
    """One durably-appended event with its PENDING outbox row."""
    spine.append(make_event(event_id, seq=seq))
    return event_id


# ══════════════════════════════════════════════════════════════════════════════
# The lifecycle (ADR-0036 RL2)
# ══════════════════════════════════════════════════════════════════════════════


def machine(state: OutboxState) -> StateMachine:
    return StateMachine(OUTBOX_LIFECYCLE, state, clock=lambda: 0.0)


LEGAL = [
    (OutboxState.PENDING, OutboxTrigger.DELIVER, OutboxState.DELIVERED),
    (OutboxState.PENDING, OutboxTrigger.FAIL_TRANSIENT, OutboxState.RETRYING),
    (OutboxState.PENDING, OutboxTrigger.FAIL_PERMANENT, OutboxState.QUARANTINED),
    (OutboxState.RETRYING, OutboxTrigger.DELIVER, OutboxState.DELIVERED),
    (OutboxState.RETRYING, OutboxTrigger.FAIL_TRANSIENT, OutboxState.RETRYING),
    (OutboxState.RETRYING, OutboxTrigger.EXHAUST, OutboxState.DEAD_LETTERED),
    (OutboxState.RETRYING, OutboxTrigger.FAIL_PERMANENT, OutboxState.QUARANTINED),
]


@pytest.mark.parametrize(("source", "trigger", "target"), LEGAL)
def test_every_legal_transition_fires(source, trigger, target):
    assert machine(source).fire(trigger) is target


def test_the_lifecycle_has_exactly_the_ratified_edges():
    """No edge exists that ADR-0036 RL2 did not authorize — asserted as an exact set, so an
    extra transition cannot be added without this failing."""
    edges = {
        (s, t, OUTBOX_LIFECYCLE.get(s, t).target)
        for s in OUTBOX_LIFECYCLE.states
        for t in OUTBOX_LIFECYCLE.triggers_from(s)
    }
    assert edges == set(LEGAL)


def test_states_are_exactly_the_five_ratified():
    assert OUTBOX_LIFECYCLE.states == frozenset(OutboxState)
    assert {s.value for s in OutboxState} == {
        "pending", "retrying", "delivered", "dead_lettered", "quarantined",
    }


def test_every_state_is_reachable():
    """The M9.16-R1 lesson the Collections lifecycle records: a published state the machine
    cannot enter reads to integrators as a supported feature."""
    reachable = {OutboxState.PENDING}
    reachable |= {OUTBOX_LIFECYCLE.get(s, t).target
                  for s in OUTBOX_LIFECYCLE.states
                  for t in OUTBOX_LIFECYCLE.triggers_from(s)}
    assert reachable == frozenset(OutboxState)


def test_terminal_states_are_delivered_dead_lettered_quarantined():
    assert TERMINAL_OUTBOX_STATES == frozenset(
        {OutboxState.DELIVERED, OutboxState.DEAD_LETTERED, OutboxState.QUARANTINED}
    )
    assert OUTBOX_LIFECYCLE.terminal_states() == TERMINAL_OUTBOX_STATES


@pytest.mark.parametrize("state", sorted(TERMINAL_OUTBOX_STATES))
@pytest.mark.parametrize("trigger", sorted(OutboxTrigger))
def test_no_terminal_state_can_transition(state, trigger):
    with pytest.raises(TerminalStateError):
        machine(state).fire(trigger)


def test_retrying_can_never_return_to_pending():
    """`PENDING` means *never attempted*. An edge back would erase attempt history and let a
    bounded ladder restart forever — exactly what ED7 forbids."""
    assert OutboxState.PENDING not in {
        OUTBOX_LIFECYCLE.get(OutboxState.RETRYING, t).target
        for t in OUTBOX_LIFECYCLE.triggers_from(OutboxState.RETRYING)
    }


def test_pending_cannot_be_exhausted_without_entering_the_ladder():
    with pytest.raises(InvalidTransition):
        machine(OutboxState.PENDING).fire(OutboxTrigger.EXHAUST)


def test_there_is_no_redrive_out_of_dead_lettered():
    """ADR-0036 RL3 — explicitly a non-goal, asserted so it cannot arrive by accident."""
    assert OUTBOX_LIFECYCLE.triggers_from(OutboxState.DEAD_LETTERED) == frozenset()


def test_there_is_no_operator_quarantine_transition():
    """ADR-0036 RL4 — no `DEAD_LETTERED → QUARANTINED`, no operator-driven edge."""
    assert OUTBOX_LIFECYCLE.triggers_from(OutboxState.QUARANTINED) == frozenset()
    assert OUTBOX_LIFECYCLE.get(OutboxState.DEAD_LETTERED, OutboxTrigger.FAIL_PERMANENT) is None


def test_quarantine_is_reachable_without_entering_the_ladder():
    """ED8: quarantine does not consume the retry ladder — expressed structurally."""
    assert (
        OUTBOX_LIFECYCLE.get(OutboxState.PENDING, OutboxTrigger.FAIL_PERMANENT).target
        is OutboxState.QUARANTINED
    )


# ══════════════════════════════════════════════════════════════════════════════
# The pure ladder (ADR-0036 RL6/RL8/RL9)
# ══════════════════════════════════════════════════════════════════════════════


def test_the_delivery_policy_is_the_owner_approved_configuration():
    assert DELIVERY_RETRY_POLICY.max_attempts == 5
    assert DELIVERY_RETRY_POLICY.base_delay_s == 30.0
    assert DELIVERY_RETRY_POLICY.multiplier == 2.0
    assert DELIVERY_RETRY_POLICY.max_delay_s == 3600.0


def test_the_ladder_reuses_core_retry_policy():
    """No second retry-policy type: two sources of truth for backoff is one too many."""
    assert isinstance(DELIVERY_RETRY_POLICY, RetryPolicy)
    assert isinstance(OutboxLadder().policy, RetryPolicy)


def test_backoff_is_exponential_and_deterministic():
    ladder = OutboxLadder()
    assert [ladder.delay_for(a) for a in range(1, 6)] == [30.0, 60.0, 120.0, 240.0, 480.0]
    assert [ladder.delay_for(a) for a in range(1, 6)] == [ladder.delay_for(a) for a in range(1, 6)]


def test_backoff_is_capped_by_max_delay():
    """The cap the delivery ladder configures but never reaches at five attempts — proven with
    a policy that does reach it, rather than assumed."""
    ladder = OutboxLadder(RetryPolicy(max_attempts=20, base_delay_s=30.0, multiplier=2.0,
                                      max_delay_s=3600.0))
    assert ladder.delay_for(8) == 3600.0
    assert ladder.delay_for(19) == 3600.0


def test_there_is_no_jitter():
    """ADR-0036 RL7 — identical inputs give identical outputs, so a ladder decision is
    replay-safe and can be asserted by value."""
    ladder = OutboxLadder()
    first = [ladder.decide(DeliveryOutcome.TRANSIENT_FAILURE, attempt_count=n, now=100.0)
             for n in range(1, 5)]
    second = [ladder.decide(DeliveryOutcome.TRANSIENT_FAILURE, attempt_count=n, now=100.0)
              for n in range(1, 5)]
    assert first == second


def test_success_decides_delivered():
    d = OutboxLadder().decide(DeliveryOutcome.SUCCESS, attempt_count=1, now=100.0)
    assert d == LadderDecision(OutboxTrigger.DELIVER, OutboxState.DELIVERED)
    assert d.next_attempt_at is None


def test_permanent_failure_decides_quarantined_at_any_attempt_count():
    ladder = OutboxLadder()
    for n in (0, 1, 4, 5, 99):
        d = ladder.decide(DeliveryOutcome.PERMANENT_FAILURE, attempt_count=n, now=100.0)
        assert d.state is OutboxState.QUARANTINED
        assert d.next_attempt_at is None  # a terminal entry is never due


def test_transient_failure_schedules_the_next_attempt():
    d = OutboxLadder().decide(DeliveryOutcome.TRANSIENT_FAILURE, attempt_count=1, now=100.0)
    assert d.state is OutboxState.RETRYING
    assert d.next_attempt_at == 130.0  # 100 + delay_for(1)


def test_the_ladder_is_bounded_and_exhausts_at_exactly_max_attempts():
    """ED7: infinite retry is prohibited."""
    ladder = OutboxLadder()
    for n in range(1, 5):
        assert ladder.decide(DeliveryOutcome.TRANSIENT_FAILURE,
                             attempt_count=n, now=0.0).state is OutboxState.RETRYING
    for n in (5, 6, 50):
        assert ladder.decide(DeliveryOutcome.TRANSIENT_FAILURE,
                             attempt_count=n, now=0.0).state is OutboxState.DEAD_LETTERED


def test_attempts_remaining_never_goes_negative():
    ladder = OutboxLadder()
    assert ladder.attempts_remaining(0) == 5
    assert ladder.attempts_remaining(5) == 0
    assert ladder.attempts_remaining(99) == 0


def test_the_ladder_performs_no_io_and_never_sleeps():
    """ADR-0036 RL9, asserted statically — the property that keeps M10.7 from becoming the
    M10.8 recovery driver."""
    body = code_of("events/ladder.py")
    for token in ("sleep", "time.time", "while ", "thread", "Thread", "asyncio",
                  "session", "commit", "httpx", "requests", "socket", "random"):
        assert token not in body, f"events/ladder.py executes {token}"


# ══════════════════════════════════════════════════════════════════════════════
# Persisted transitions + evidence (ADR-0036 RL8/RL12/RL13)
# ══════════════════════════════════════════════════════════════════════════════


def test_a_new_entry_starts_with_no_attempts(spine):
    seeded(spine)
    entry = spine.get("e-1")
    assert entry.attempt_count == 0
    assert entry.next_attempt_at is None
    assert entry.state is OutboxState.PENDING


def test_begin_attempt_increments_before_the_outcome_is_known(spine):
    """ADR-0036 RL8 — counting afterwards would let a crash mid-attempt grant a free retry."""
    seeded(spine)
    assert spine.begin_attempt("e-1").attempt_count == 1
    assert spine.begin_attempt("e-1").attempt_count == 2


def test_begin_attempt_on_an_unknown_entry_is_none(spine):
    assert spine.begin_attempt("nope") is None


def test_first_transient_failure_enters_retrying_with_the_scheduled_instant(spine):
    seeded(spine)
    spine.begin_attempt("e-1")
    entry = spine.record_failure("e-1", DeliveryOutcome.TRANSIENT_FAILURE, error="boom")

    assert entry.state is OutboxState.RETRYING
    assert entry.attempt_count == 1
    assert entry.next_attempt_at == 1030.0  # frozen 1000.0 + delay_for(1)
    assert entry.dead_lettered_at is None and entry.quarantined_at is None


def test_the_full_ladder_runs_to_dead_letter_at_exactly_max_attempts(spine):
    seeded(spine)
    scheduled = []
    for _ in range(4):
        spine.begin_attempt("e-1")
        scheduled.append(
            spine.record_failure("e-1", DeliveryOutcome.TRANSIENT_FAILURE).next_attempt_at
        )
    assert scheduled == [1030.0, 1060.0, 1120.0, 1240.0]

    spine.begin_attempt("e-1")  # attempt 5
    entry = spine.record_failure("e-1", DeliveryOutcome.TRANSIENT_FAILURE, error="final")

    assert entry.state is OutboxState.DEAD_LETTERED
    assert entry.attempt_count == 5
    assert entry.dead_lettered_at == 1000.0
    assert entry.next_attempt_at is None


def test_a_dead_lettered_entry_is_terminal(spine):
    seeded(spine)
    for _ in range(5):
        spine.begin_attempt("e-1")
        spine.record_failure("e-1", DeliveryOutcome.TRANSIENT_FAILURE)
    assert spine.get("e-1").state is OutboxState.DEAD_LETTERED

    with pytest.raises(TerminalStateError):
        spine.record_failure("e-1", DeliveryOutcome.TRANSIENT_FAILURE)
    with pytest.raises(TerminalStateError):
        spine.mark_delivered("e-1")
    with pytest.raises(TerminalStateError):
        spine.begin_attempt("e-1")


def test_permanent_failure_goes_straight_to_quarantine(spine):
    seeded(spine)
    spine.begin_attempt("e-1")
    entry = spine.record_failure(
        "e-1", DeliveryOutcome.PERMANENT_FAILURE, error="malformed envelope"
    )

    assert entry.state is OutboxState.QUARANTINED
    assert entry.quarantined_at == 1000.0
    assert entry.quarantine_reason == "malformed envelope"
    assert entry.next_attempt_at is None


def test_permanent_failure_does_not_consume_the_retry_ladder(spine):
    """ED8 — quarantine does not consume the ladder."""
    seeded(spine)
    spine.begin_attempt("e-1")
    spine.begin_attempt("e-1")
    entry = spine.record_failure("e-1", DeliveryOutcome.PERMANENT_FAILURE, error="poison")
    assert entry.attempt_count == 2  # unchanged by the quarantine


def test_permanent_failure_from_pending_needs_no_attempt_at_all(spine):
    seeded(spine)
    entry = spine.record_failure("e-1", DeliveryOutcome.PERMANENT_FAILURE, error="unknown type")
    assert entry.state is OutboxState.QUARANTINED
    assert entry.attempt_count == 0


def test_a_quarantined_entry_is_terminal(spine):
    seeded(spine)
    spine.record_failure("e-1", DeliveryOutcome.PERMANENT_FAILURE, error="poison")
    with pytest.raises(TerminalStateError):
        spine.record_failure("e-1", DeliveryOutcome.TRANSIENT_FAILURE)
    with pytest.raises(TerminalStateError):
        spine.mark_delivered("e-1")


def test_a_retrying_entry_can_still_be_delivered(spine):
    seeded(spine)
    spine.begin_attempt("e-1")
    spine.record_failure("e-1", DeliveryOutcome.TRANSIENT_FAILURE)

    assert spine.mark_delivered("e-1") is True
    entry = spine.get("e-1")
    assert entry.state is OutboxState.DELIVERED
    assert entry.next_attempt_at is None  # no longer due for anything


def test_record_failure_rejects_success(spine):
    seeded(spine)
    with pytest.raises(ValueError, match="use mark_delivered"):
        spine.record_failure("e-1", DeliveryOutcome.SUCCESS)


def test_record_failure_on_an_unknown_entry_is_none(spine):
    assert spine.record_failure("nope", DeliveryOutcome.TRANSIENT_FAILURE) is None


def test_an_injected_policy_bounds_the_ladder_differently(spine):
    """The bound is configuration, not a constant in the transition logic (ED7)."""
    seeded(spine)
    tight = RetryPolicy(max_attempts=2, base_delay_s=5.0, multiplier=2.0, max_delay_s=10.0)

    spine.begin_attempt("e-1")
    first = spine.record_failure("e-1", DeliveryOutcome.TRANSIENT_FAILURE, policy=tight)
    assert first.state is OutboxState.RETRYING
    assert first.next_attempt_at == 1005.0  # the injected base delay, not the delivery ladder's

    spine.begin_attempt("e-1")
    second = spine.record_failure("e-1", DeliveryOutcome.TRANSIENT_FAILURE, policy=tight)
    assert second.state is OutboxState.DEAD_LETTERED  # exhausted two attempts early


def test_a_single_attempt_policy_is_refused_loudly_rather_than_mis_transitioning(spine):
    """A boundary of the ratified lifecycle, asserted rather than discovered in production.

    `EXHAUST` is reachable **only from `RETRYING`** (ADR-0036 RL2): exhausting a ladder you never
    entered is not a state this machine models. So a `max_attempts=1` policy — where the very
    first failure would have to go `PENDING → DEAD_LETTERED` — has no edge, and `core.fsm`
    refuses it **loudly** instead of inventing one.

    The owner-approved delivery policy (`max_attempts=5`) never reaches this: its first failure
    is always `PENDING → RETRYING`. The behaviour is recorded here so the limit is visible and
    fails safe rather than silently.
    """
    seeded(spine)
    degenerate = RetryPolicy(max_attempts=1, base_delay_s=5.0, multiplier=2.0, max_delay_s=10.0)
    spine.begin_attempt("e-1")

    with pytest.raises(InvalidTransition):
        spine.record_failure("e-1", DeliveryOutcome.TRANSIENT_FAILURE, policy=degenerate)

    # Nothing was written: the refusal happens before any row is touched.
    assert spine.get("e-1").state is OutboxState.PENDING
    assert spine.failures("e-1") == ()


# ══════════════════════════════════════════════════════════════════════════════
# Failure history (ADR-0036 RL12)
# ══════════════════════════════════════════════════════════════════════════════


def test_one_history_row_per_failed_attempt(spine):
    seeded(spine)
    for _ in range(3):
        spine.begin_attempt("e-1")
        spine.record_failure("e-1", DeliveryOutcome.TRANSIENT_FAILURE, error="net down")

    failures = spine.failures("e-1")
    assert [f.attempt for f in failures] == [1, 2, 3]
    assert all(f.outcome is DeliveryOutcome.TRANSIENT_FAILURE for f in failures)
    assert all(f.error == "net down" for f in failures)
    assert all(f.occurred_at == 1000.0 for f in failures)


def test_history_survives_into_dead_letter(spine):
    """ED8: a dead-lettered entry keeps its failure history."""
    seeded(spine)
    for _ in range(5):
        spine.begin_attempt("e-1")
        spine.record_failure("e-1", DeliveryOutcome.TRANSIENT_FAILURE, error="net down")

    assert spine.get("e-1").state is OutboxState.DEAD_LETTERED
    assert len(spine.failures("e-1")) == 5


def test_a_successful_delivery_creates_no_failure_history(spine):
    seeded(spine)
    spine.begin_attempt("e-1")
    spine.mark_delivered("e-1")
    assert spine.failures("e-1") == ()


def test_history_is_ordered_and_scoped_to_one_event(spine):
    seeded(spine, "a", seq=1)
    seeded(spine, "b", seq=2)
    for event_id in ("a", "b", "a"):
        spine.begin_attempt(event_id)
        spine.record_failure(event_id, DeliveryOutcome.TRANSIENT_FAILURE)

    assert [f.attempt for f in spine.failures("a")] == [1, 2]
    assert [f.attempt for f in spine.failures("b")] == [1]


def test_the_failure_history_port_has_no_update_or_delete(spine):
    """Append-only: history that can be edited is not evidence."""
    from events.ports import DurableOutbox

    methods = {n for n in dir(DurableOutbox) if not n.startswith("_")}
    assert not (methods & {"update_failure", "delete_failure", "clear_failures", "purge"})


def test_failure_history_lives_off_the_event_log(spine):
    """ED5 — no failure column may appear on `evt_event`."""
    from events.persistence import DurableEventRow

    columns = set(DurableEventRow.__table__.columns.keys())
    assert not (columns & {"attempt_count", "outcome", "error", "state", "next_attempt_at"})


# ══════════════════════════════════════════════════════════════════════════════
# Atomicity (ADR-0036 RL13)
# ══════════════════════════════════════════════════════════════════════════════


def test_transition_and_history_are_one_transaction(spine, monkeypatch):
    seeded(spine)
    spine.begin_attempt("e-1")
    session = spine._db.session
    monkeypatch.setattr(session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("db down")))

    with pytest.raises(RuntimeError, match="db down"):
        spine.record_failure("e-1", DeliveryOutcome.TRANSIENT_FAILURE, error="x")

    monkeypatch.undo()
    # Neither half landed: no history row, and the state never moved.
    assert spine.failures("e-1") == ()
    assert spine.get("e-1").state is OutboxState.PENDING
    assert spine.get("e-1").next_attempt_at is None


def test_a_rolled_back_quarantine_leaves_no_evidence(spine, monkeypatch):
    seeded(spine)
    session = spine._db.session
    monkeypatch.setattr(session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("db down")))

    with pytest.raises(RuntimeError):
        spine.record_failure("e-1", DeliveryOutcome.PERMANENT_FAILURE, error="poison")

    monkeypatch.undo()
    assert spine.get("e-1").state is OutboxState.PENDING
    assert spine.get("e-1").quarantine_reason == ""
    assert spine.failures("e-1") == ()


def test_the_event_row_is_untouched_by_every_ladder_transition(spine):
    seeded(spine)
    before = spine.read_all()[0]
    spine.begin_attempt("e-1")
    spine.record_failure("e-1", DeliveryOutcome.TRANSIENT_FAILURE, error="x")
    spine.record_failure("e-1", DeliveryOutcome.PERMANENT_FAILURE, error="y")
    assert spine.read_all()[0] == before


# ══════════════════════════════════════════════════════════════════════════════
# due(now) — a query, never an action (ADR-0036 RL1)
# ══════════════════════════════════════════════════════════════════════════════


def test_due_returns_only_entries_whose_backoff_elapsed(spine):
    seeded(spine, "a", seq=1)
    seeded(spine, "b", seq=2)
    for event_id in ("a", "b"):
        spine.begin_attempt(event_id)
        spine.record_failure(event_id, DeliveryOutcome.TRANSIENT_FAILURE)
    # both scheduled for 1030.0
    assert spine.due(1029.9) == ()
    assert [e.event_id for e in spine.due(1030.0)] == ["a", "b"]


def test_due_is_deterministic_and_ordered_by_seq(spine):
    for event_id, seq in (("z", 3), ("a", 1), ("m", 2)):
        seeded(spine, event_id, seq=seq)
        spine.begin_attempt(event_id)
        spine.record_failure(event_id, DeliveryOutcome.TRANSIENT_FAILURE)

    order = [e.event_id for e in spine.due(9999.0)]
    assert order == ["a", "m", "z"]
    assert [e.event_id for e in spine.due(9999.0)] == order


def test_due_excludes_pending_and_terminal_entries(spine):
    seeded(spine, "pending-one", seq=1)
    seeded(spine, "delivered-one", seq=2)
    spine.mark_delivered("delivered-one")
    seeded(spine, "quarantined-one", seq=3)
    spine.record_failure("quarantined-one", DeliveryOutcome.PERMANENT_FAILURE, error="poison")

    assert spine.due(9999.0) == ()


def test_due_does_not_change_anything(spine):
    """It is a read. Calling it must not deliver, advance, or reschedule."""
    seeded(spine)
    spine.begin_attempt("e-1")
    spine.record_failure("e-1", DeliveryOutcome.TRANSIENT_FAILURE)
    before = spine.get("e-1")

    spine.due(9999.0)
    spine.due(9999.0)

    assert spine.get("e-1") == before
    assert len(spine.failures("e-1")) == 1


# ══════════════════════════════════════════════════════════════════════════════
# Restart durability (ADR-0034 DS10)
# ══════════════════════════════════════════════════════════════════════════════


def test_ladder_state_survives_a_restart(tmp_path):
    url = f"sqlite:///{tmp_path / 'ladder.db'}"
    db = EventDatabase(url)
    db.create_all()
    spine = SqlDurableEventSpine(db, clock=FrozenClock())
    spine.append(make_event("e-1", seq=1))
    spine.begin_attempt("e-1")
    spine.begin_attempt("e-1")
    spine.record_failure("e-1", DeliveryOutcome.TRANSIENT_FAILURE, error="net down")
    db.close()

    reopened = EventDatabase(url)
    revived = SqlDurableEventSpine(reopened, clock=FrozenClock())
    entry = revived.get("e-1")

    assert entry.state is OutboxState.RETRYING
    assert entry.attempt_count == 2  # the ladder did not reset
    assert entry.next_attempt_at == 1060.0  # an absolute instant, not recomputed
    assert [f.attempt for f in revived.failures("e-1")] == [2]
    reopened.close()


def test_terminal_states_survive_a_restart(tmp_path):
    url = f"sqlite:///{tmp_path / 'terminal.db'}"
    db = EventDatabase(url)
    db.create_all()
    spine = SqlDurableEventSpine(db, clock=FrozenClock())
    spine.append(make_event("dead", seq=1))
    spine.append(make_event("quar", seq=2))
    for _ in range(5):
        spine.begin_attempt("dead")
        spine.record_failure("dead", DeliveryOutcome.TRANSIENT_FAILURE)
    spine.record_failure("quar", DeliveryOutcome.PERMANENT_FAILURE, error="poison")
    db.close()

    reopened = EventDatabase(url)
    revived = SqlDurableEventSpine(reopened, clock=FrozenClock())
    assert revived.get("dead").state is OutboxState.DEAD_LETTERED
    assert revived.get("dead").dead_lettered_at == 1000.0
    assert revived.get("quar").state is OutboxState.QUARANTINED
    assert revived.get("quar").quarantine_reason == "poison"
    assert len(revived.failures("dead")) == 5
    reopened.close()


# ══════════════════════════════════════════════════════════════════════════════
# Migration 0036 (ADR-0036 RL11)
# ══════════════════════════════════════════════════════════════════════════════


def test_migration_0036_is_registered_after_0035():
    from events.persistence.migrations import MIGRATIONS

    assert [m.VERSION for m in MIGRATIONS] == ["0034", "0035", "0036"]


def test_earlier_migrations_still_own_only_their_own_tables():
    from events.persistence import EVENT_TABLES, OUTBOX_FAILURE_TABLES, OUTBOX_TABLES
    from events.persistence.migrations import (
        v0034_durable_event_log,
        v0035_durable_outbox,
        v0036_outbox_retry_ladder,
    )

    assert v0034_durable_event_log.TABLES == EVENT_TABLES
    assert v0035_durable_outbox.TABLES == OUTBOX_TABLES
    assert v0036_outbox_retry_ladder.TABLES == OUTBOX_FAILURE_TABLES


def test_migration_0036_upgrades_a_0035_era_database(tmp_path):
    """The ALTER path: a database created at the 0035 schema must gain the columns without
    losing its rows."""
    from sqlalchemy import inspect, text

    from events.persistence.migrations import v0036_outbox_retry_ladder

    url = f"sqlite:///{tmp_path / 'upgrade.db'}"
    db = EventDatabase(url)
    # Build the **0035-era** schema by hand — using today's model would create the post-0036
    # table and the migration would have nothing to do, so the test would prove nothing.
    with db.engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE evt_outbox ("
                 "event_id VARCHAR NOT NULL PRIMARY KEY, seq INTEGER NOT NULL, "
                 "state VARCHAR NOT NULL, created_at FLOAT NOT NULL, delivered_at FLOAT)")
        )
        connection.execute(
            text("INSERT INTO evt_outbox (event_id, seq, state, created_at) "
                 "VALUES ('old', 1, 'pending', 5.0)")
        )

    v0036_outbox_retry_ladder.upgrade(db)

    columns = {c["name"] for c in inspect(db.engine).get_columns("evt_outbox")}
    assert {"attempt_count", "next_attempt_at", "dead_lettered_at",
            "quarantined_at", "quarantine_reason"} <= columns
    assert "evt_outbox_failure" in set(inspect(db.engine).get_table_names())

    # The pre-existing row survived and was backfilled with the documented default.
    row = db.session.get(DurableOutboxRow, "old")
    assert row.state == "pending" and row.attempt_count == 0 and row.next_attempt_at is None
    db.close()


def test_migration_0036_is_idempotent(tmp_path):
    from events.persistence.migrations import upgrade_all, v0036_outbox_retry_ladder

    db = EventDatabase(f"sqlite:///{tmp_path / 'idem36.db'}")
    assert upgrade_all(db) == ("0034", "0035", "0036")
    v0036_outbox_retry_ladder.upgrade(db)  # must not raise
    v0036_outbox_retry_ladder.upgrade(db)

    spine = SqlDurableEventSpine(db, clock=FrozenClock())
    spine.append(make_event("e-1", seq=1))
    spine.begin_attempt("e-1")
    assert spine.record_failure("e-1", DeliveryOutcome.TRANSIENT_FAILURE).attempt_count == 1
    db.close()


def test_migration_0036_downgrade_is_a_documented_no_op(tmp_path):
    from sqlalchemy import inspect

    from events.persistence.migrations import upgrade_all, v0036_outbox_retry_ladder

    db = EventDatabase(f"sqlite:///{tmp_path / 'down36.db'}")
    upgrade_all(db)
    assert v0036_outbox_retry_ladder.downgrade(db) is None
    assert "evt_outbox_failure" in set(inspect(db.engine).get_table_names())
    db.close()


def test_every_new_index_carries_the_evt_prefix():
    """NS-2 — certification checks Billing index names against every table in the process."""
    names = {ix.name for ix in DurableOutboxFailureRow.__table__.indexes}
    names |= {ix.name for ix in DurableOutboxRow.__table__.indexes}
    assert names and all(n.startswith("ix_evt_") for n in names), names


def test_the_outbox_carries_exactly_the_ratified_columns():
    assert set(DurableOutboxRow.__table__.columns.keys()) == {
        "event_id", "seq", "state", "created_at", "delivered_at",
        "attempt_count", "next_attempt_at",
        "dead_lettered_at", "quarantined_at", "quarantine_reason",
    }


def test_no_last_error_column_on_the_outbox():
    """ADR-0036 RL11 — failure history is the authoritative evidence; `last_error` would keep
    only the newest and quietly become a second, lossier source of truth."""
    assert "last_error" not in DurableOutboxRow.__table__.columns


def test_no_claim_lease_or_worker_columns_anywhere():
    forbidden = {"claimed_at", "claimed_by", "lease_expires_at", "locked_by", "worker_id",
                 "consumer_id", "owner", "visible_at"}
    for table in (DurableOutboxRow, DurableOutboxFailureRow):
        assert not (set(table.__table__.columns.keys()) & forbidden), table.__tablename__


def test_the_failure_table_uses_a_surrogate_key_and_no_foreign_key():
    assert list(DurableOutboxFailureRow.__table__.primary_key.columns.keys()) == ["id"]
    assert not DurableOutboxFailureRow.__table__.foreign_keys


# ══════════════════════════════════════════════════════════════════════════════
# Boundary: M10.7 is not the recovery worker (ADR-0036 RL1/RL9/RL10)
# ══════════════════════════════════════════════════════════════════════════════


M107_SOURCES = ("events/ladder.py", "events/ports.py", "events/persistence/spine.py",
                "events/persistence/models.py",
                "events/persistence/migrations/v0036_outbox_retry_ladder.py")


def test_m107_ships_no_worker_scheduler_or_loop():
    """The structural reason recovery stays M10.8. Comments naming these are fine; code is not,
    so this checks executable lines only."""
    banned = ("sleep(", "threading", "Thread(", "asyncio", "schedule(", "Timer(",
              "while True", "APScheduler", "cron")
    for path in M107_SOURCES:
        body = code_of(path)
        for token in banned:
            assert token not in body, f"{path} executes {token}"


def test_m107_never_claims_or_locks_rows():
    for path in M107_SOURCES:
        body = code_of(path).lower()
        for token in ("for update", "with_for_update", "advisory_lock", "select_for_update"):
            assert token not in body, f"{path} executes {token}"


def test_m107_ships_no_concrete_delivery_transport():
    """ADR-0036 RL9 — a contract only. A concrete transport would need a driver, and the driver
    is M10.8."""
    from events.ports import DeliveryTransport

    assert DeliveryTransport.__abstractmethods__ == frozenset({"deliver"})
    for path in M107_SOURCES:
        assert "DeliveryTransport)" not in code_of(path), f"{path} implements DeliveryTransport"


def test_webhooks_is_untouched_and_has_no_retry_mechanism():
    """ADR-0036 RL10 — no second competing delivery mechanism (blueprint R-11)."""
    source = pathlib.Path("events/webhooks.py").read_text()
    assert len(source.splitlines()) == 58
    lowered = source.lower()
    for token in ("retry", "backoff", "attempt_count", "dead_letter", "quarantine", "outbox_"):
        assert token not in lowered.replace("retry + dead-letter is an m4 deliverable", "")


def test_runtime_acquired_nothing_from_m107():
    """ADR-0036 RL17 — the production runtime is still not wired to the durable sink."""
    for token in ("OutboxLadder", "DeliveryOutcome", "SqlDurableEventSpine", "EventDatabase",
                  "durable", "RetryPolicy("):
        for path in pathlib.Path("runtime").rglob("*.py"):
            assert token not in path.read_text(), f"{path} mentions {token}"


def test_no_retention_or_metric_implementation_entered_m107():
    """M10.5 and M10.10 remain untouched."""
    for path in M107_SOURCES:
        body = code_of(path)
        for token in ("def purge", "def expire", "def apply_retention", "metric.", "MetricStream"):
            assert token not in body, f"{path} contains {token}"


def test_the_ladder_and_ports_stay_free_of_storage_libraries():
    for path in ("events/ladder.py", "events/ports.py"):
        body = code_of(path)
        for token in ("sqlmodel", "sqlalchemy", "events.persistence"):
            assert token not in body, f"{path} imports {token}"
