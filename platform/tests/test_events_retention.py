"""Retention tests (M10.5) — ADR-0041's five guards, executed against the real implementation.

Retention is the **only** sanctioned deletion path in the repository (ADR-0032 ED9/EV4), so the
thing worth testing is mostly what it *refuses*: an unconfigured deployment, an event that is
too recent or too old, an undelivered event, a terminally-failed one, and one with no metering
attestation must all survive. RT8 calls default-deny *"the behaviour, not the fallback"*, and
these are the assertions that make that claim checkable.

Everything runs against `SqlRetentionStore` over a real `EventDatabase` and real `evt_outbox`
state produced by `SqlDurableEventSpine` — not a fake store. A hand-rolled double would let the
suite agree with an implementation that never touched a database. Hermetic throughout: SQLite,
an injected clock, no network, no credentials, no vendor service, no production wiring (RT9).
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from core.events import Event, EventSource, EventType
from core.policies import InvalidPolicy, RetryPolicy
from events import (
    DRAINED_STATES,
    DeliveryOutcome,
    DurableEventLog,
    EventRetention,
    MeteredWatermark,
    NullMeteredWatermark,
    OutboxState,
    RetentionPolicy,
    RetentionWindow,
    drained,
)
from events.persistence import (
    EventDatabase,
    SqlDurableEventLog,
    SqlDurableEventSpine,
    SqlRetentionStore,
)
from events.persistence.models import DurableEventRow, DurableOutboxRow

# ── the timeline ────────────────────────────────────────────────────────────────
#
# One policy for the whole suite, so every test reads against the same window rather than
# re-deriving one. At NOW the authorized window is [890.0, 990.0).

NOW = 1000.0
MAX_AGE_S = 10.0
WINDOW_SPAN_S = 100.0
POLICY = RetentionPolicy(max_age_s=MAX_AGE_S, window_span_s=WINDOW_SPAN_S)

WINDOW_START = NOW - MAX_AGE_S - WINDOW_SPAN_S  # 890.0
WINDOW_END = NOW - MAX_AGE_S  # 990.0
INSIDE = 900.0  # comfortably within the window
TOO_RECENT = 995.0  # newer than the recency cliff
TOO_OLD = 800.0  # older than the window's floor


# ── helpers ─────────────────────────────────────────────────────────────────────


def _event(n: int, *, session_id: str = "sess-1") -> Event:
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


class _Watermark(MeteredWatermark):
    """An attestation covering an explicit set of events — the injected-port shape (RT6)."""

    def __init__(self, *covered: str) -> None:
        self._covered = set(covered)

    def covers(self, event_id: str) -> bool:
        return event_id in self._covered


class _RaisingWatermark(MeteredWatermark):
    """An attestation that cannot answer. RT8: unavailable is *not* permission."""

    def __init__(self) -> None:
        self.calls = 0

    def covers(self, event_id: str) -> bool:
        self.calls += 1
        raise RuntimeError("metering attestation unavailable")


class _BrokenStore:
    """A store whose read fails. Retention must retain rather than propagate (RT8)."""

    def candidates(self, window):
        raise RuntimeError("database unavailable")

    def delete(self, event_ids):  # pragma: no cover — must never be reached
        raise AssertionError("delete must not be attempted when selection failed")


@pytest.fixture
def db() -> EventDatabase:
    database = EventDatabase("sqlite://")
    database.create_all()
    yield database
    database.close()


@pytest.fixture
def store(db: EventDatabase) -> SqlRetentionStore:
    return SqlRetentionStore(db)


def _record(
    db: EventDatabase,
    n: int,
    *,
    at: float,
    state: OutboxState | None = OutboxState.DELIVERED,
) -> str:
    """Durably record event `n` at `recorded_at == at`, then drive its outbox to `state`.

    `state=None` records the event through the **log only**, so it has no `evt_outbox` row at
    all — the "delivery state cannot be read" case, which must be retained.
    """
    event = _event(n)
    if state is None:
        SqlDurableEventLog(db, clock=lambda: at).append(event)
        return event.id

    spine = SqlDurableEventSpine(db, clock=lambda: at)
    spine.append(event)
    if state is OutboxState.PENDING:
        return event.id
    if state is OutboxState.DELIVERED:
        spine.mark_delivered(event.id)
        return event.id
    if state is OutboxState.QUARANTINED:
        spine.record_failure(event.id, DeliveryOutcome.PERMANENT_FAILURE, error="fatal")
        return event.id
    if state is OutboxState.RETRYING:
        spine.begin_attempt(event.id)
        spine.record_failure(event.id, DeliveryOutcome.TRANSIENT_FAILURE, error="flaky")
        return event.id
    if state is OutboxState.DEAD_LETTERED:
        # `EXHAUST` is only an edge out of `RETRYING` (OUTBOX_LIFECYCLE), so the ladder has to
        # actually be entered before it can be exhausted — a one-rung policy on the *second*
        # failure is what reaches the dead letter.
        spine.begin_attempt(event.id)
        spine.record_failure(event.id, DeliveryOutcome.TRANSIENT_FAILURE, error="flaky")
        spine.begin_attempt(event.id)
        spine.record_failure(
            event.id,
            DeliveryOutcome.TRANSIENT_FAILURE,
            error="flaky",
            policy=RetryPolicy(max_attempts=1),
        )
        return event.id
    raise AssertionError(f"unsupported state {state!r}")


def _retention(store, *, policy=POLICY, watermark=None, clock=None) -> EventRetention:
    return EventRetention(store, watermark, policy, clock=clock)


def _ids(db: EventDatabase) -> set[str]:
    return {row.event_id for row in db.session.exec(_select_events()).all()}


def _select_events():
    from sqlmodel import select

    return select(DurableEventRow)


# ── A. configuration: unconfigured deletes nothing (RT7.1) ──────────────────────


def test_no_configured_policy_deletes_nothing(db, store):
    """The headline ED9 property: *a deployment that configures nothing deletes nothing*."""
    _record(db, 1, at=INSIDE)
    retention = EventRetention(store, _Watermark("evt-1"), None)

    assert retention.configured is False
    assert retention.window(NOW) is None
    assert retention.deletable(NOW) == ()
    assert retention.sweep(now=NOW) == 0
    assert _ids(db) == {"evt-1"}


def test_retention_has_no_default_policy(db, store):
    """A default period here would quietly delete history in every deployment that never
    thought about retention. The constructor's default must be `None`, not a policy."""
    assert EventRetention(store).policy is None
    assert EventRetention(store).configured is False


def test_policy_rejects_invalid_configuration():
    """`core.policies` convention — validated at construction, no silent config."""
    with pytest.raises(InvalidPolicy):
        RetentionPolicy(max_age_s=-1.0, window_span_s=10.0)
    with pytest.raises(InvalidPolicy):
        RetentionPolicy(max_age_s=10.0, window_span_s=0.0)


@pytest.mark.parametrize("span", [float("inf"), float("-inf"), float("nan")])
def test_a_non_finite_span_cannot_open_the_bottom_of_the_window(span):
    """RT7.2 — the window must be **closed**.

    `+inf` is the case this guard exists for: it passes a bare `> 0` check and would produce
    `RetentionWindow(start=-inf, ...)`, which every past instant satisfies — "delete everything
    older than X" reintroduced through configuration rather than through code.
    """
    with pytest.raises(InvalidPolicy, match="finite"):
        RetentionPolicy(max_age_s=10.0, window_span_s=span)


@pytest.mark.parametrize("age", [float("inf"), float("-inf"), float("nan")])
def test_a_non_finite_max_age_is_also_rejected(age):
    """The recency cliff has to be a real instant too. `-inf`/NaN fail `max_age_s >= 0`;
    `+inf` produces an empty window, which `RetentionWindow` refuses."""
    with pytest.raises((InvalidPolicy, ValueError)):
        RetentionPolicy(max_age_s=age, window_span_s=10.0).window(NOW)


def test_a_finite_positive_span_is_still_accepted():
    """The guard must reject the pathological values without narrowing the legitimate range."""
    assert RetentionPolicy(max_age_s=0.0, window_span_s=1e-9).window(NOW).end == NOW
    assert RetentionPolicy(max_age_s=10.0, window_span_s=1e9).window(NOW).end == NOW - 10.0


# ── B. the closed window (RT7.2) ────────────────────────────────────────────────


def test_the_window_is_closed_at_both_ends_and_derived_from_the_policy():
    window = POLICY.window(NOW)
    assert (window.start, window.end) == (WINDOW_START, WINDOW_END)


def test_an_event_newer_than_the_recency_cliff_is_retained(db, store):
    _record(db, 1, at=TOO_RECENT)
    assert _retention(store, watermark=_Watermark("evt-1")).sweep(now=NOW) == 0
    assert _ids(db) == {"evt-1"}


def test_an_event_older_than_the_window_floor_is_retained(db, store):
    """The window is closed, not 'everything older than now' — so an ancient event outside the
    span is out of scope, and a sweep's reach is a stated quantity rather than an emergent one."""
    _record(db, 1, at=TOO_OLD)
    assert _retention(store, watermark=_Watermark("evt-1")).sweep(now=NOW) == 0
    assert _ids(db) == {"evt-1"}


def test_the_window_boundary_is_half_open(db, store):
    """`[start, end)` — the start instant is inside, the end instant is not, so adjacent windows
    tile exactly and a boundary event belongs to one sweep rather than two or none."""
    _record(db, 1, at=WINDOW_START)
    _record(db, 2, at=WINDOW_END)
    retention = _retention(store, watermark=_Watermark("evt-1", "evt-2"))

    assert retention.deletable(NOW) == ("evt-1",)
    assert retention.sweep(now=NOW) == 1
    assert _ids(db) == {"evt-2"}


def test_an_empty_or_inverted_window_is_rejected():
    with pytest.raises(ValueError):
        RetentionWindow(start=100.0, end=100.0)
    with pytest.raises(ValueError):
        RetentionWindow(start=200.0, end=100.0)


def test_the_injected_clock_controls_the_window(db, store):
    """DS11. The same event is deleted or kept purely on what the clock says — no wall-clock
    read anywhere on the path, which is what makes a sweep reproducible in a test."""
    _record(db, 1, at=INSIDE)
    retention = _retention(store, watermark=_Watermark("evt-1"), clock=lambda: NOW - 500.0)

    # At the injected clock, the window is [390, 490) — the event is far too new.
    assert retention.sweep() == 0
    assert _ids(db) == {"evt-1"}

    # The same instance, told a different `now`, deletes it.
    assert retention.sweep(now=NOW) == 1
    assert _ids(db) == set()


def test_a_sweep_without_a_clock_or_an_explicit_now_is_a_loud_error(db, store):
    with pytest.raises(ValueError, match="no clock injected"):
        _retention(store, watermark=_Watermark("evt-1")).sweep()


# ── C. the drained guard: DELIVERED only (RT7.3, RT1, RT3) ──────────────────────


def test_a_delivered_metered_in_window_event_is_deleted(db, store):
    """The one case that actually deletes — all five guards satisfied."""
    _record(db, 1, at=INSIDE, state=OutboxState.DELIVERED)
    assert _retention(store, watermark=_Watermark("evt-1")).sweep(now=NOW) == 1
    assert _ids(db) == set()


@pytest.mark.parametrize(
    "state",
    [
        OutboxState.PENDING,
        OutboxState.RETRYING,
        OutboxState.DEAD_LETTERED,
        OutboxState.QUARANTINED,
    ],
)
def test_every_non_delivered_state_is_retained(db, store, state):
    """RT3. `DEAD_LETTERED` and `QUARANTINED` are terminal but they mean delivery **failed** —
    deleting them would destroy the operator's evidence exactly when it is needed, so terminal
    is emphatically not drained."""
    _record(db, 1, at=INSIDE, state=state)
    assert _retention(store, watermark=_Watermark("evt-1")).sweep(now=NOW) == 0
    assert _ids(db) == {"evt-1"}


def test_an_event_with_no_outbox_row_is_retained(db, store):
    """Delivery state that cannot be read is not 'nothing to deliver, therefore safe' (RT8)."""
    _record(db, 1, at=INSIDE, state=None)
    assert store.candidates(POLICY.window(NOW))[0].outbox_state is None
    assert _retention(store, watermark=_Watermark("evt-1")).sweep(now=NOW) == 0
    assert _ids(db) == {"evt-1"}


def test_an_unrecognized_outbox_state_is_retained(db, store):
    """A state added additively (AR-32) by a later milestone must not become deletable merely
    because this build cannot name it."""
    _record(db, 1, at=INSIDE, state=OutboxState.PENDING)
    row = db.session.get(DurableOutboxRow, "evt-1")
    row.state = "some_future_state"
    db.session.add(row)
    db.session.commit()

    assert store.candidates(POLICY.window(NOW))[0].outbox_state is None
    assert _retention(store, watermark=_Watermark("evt-1")).sweep(now=NOW) == 0
    assert _ids(db) == {"evt-1"}


def test_drained_is_exactly_delivered():
    assert DRAINED_STATES == frozenset({OutboxState.DELIVERED})
    assert drained(OutboxState.DELIVERED) is True
    for state in OutboxState:
        if state is not OutboxState.DELIVERED:
            assert drained(state) is False
    assert drained(None) is False


# ── D. the metered guard (RT7.4, RT6) ───────────────────────────────────────────


def test_a_missing_attestation_retains_everything(db, store):
    """No watermark supplied ⇒ `NullMeteredWatermark` ⇒ covers nothing. Default-deny by
    construction, not by a branch someone could forget."""
    _record(db, 1, at=INSIDE, state=OutboxState.DELIVERED)
    assert EventRetention(store, None, POLICY).sweep(now=NOW) == 0
    assert _ids(db) == {"evt-1"}


def test_the_null_attestation_covers_nothing():
    assert NullMeteredWatermark().covers("evt-1") is False
    assert NullMeteredWatermark().covers("anything-at-all") is False


def test_an_uncovered_event_is_retained(db, store):
    """An attestation that answers, but answers *no*."""
    _record(db, 1, at=INSIDE, state=OutboxState.DELIVERED)
    assert _retention(store, watermark=_Watermark("evt-other")).sweep(now=NOW) == 0
    assert _ids(db) == {"evt-1"}


def test_an_attestation_that_cannot_answer_retains(db, store):
    """RT8 — 'fails to evaluate for any reason' means retain, and the failure must not escape
    into the caller either: a sweep that raised would be an incident where a kept event is
    merely storage."""
    _record(db, 1, at=INSIDE, state=OutboxState.DELIVERED)
    watermark = _RaisingWatermark()

    assert _retention(store, watermark=watermark).sweep(now=NOW) == 0
    assert watermark.calls == 1, "the attestation was actually consulted"
    assert _ids(db) == {"evt-1"}


def test_a_failing_candidate_read_retains_everything(db, store):
    """A store that cannot answer deletes nothing — and never reaches `delete`."""
    retention = EventRetention(_BrokenStore(), _Watermark("evt-1"), POLICY)
    assert retention.deletable(NOW) == ()
    assert retention.sweep(now=NOW) == 0


def test_the_watermark_port_is_minimal():
    """RR6 — anything richer than 'is this covered?' would be a second metering algorithm. No
    quantity, no meter code, no period."""
    assert set(MeteredWatermark.__abstractmethods__) == {"covers"}
    surface = {n for n in dir(MeteredWatermark) if not n.startswith("_")}
    assert surface == {"covers"}


# ── E. independence, and the blast radius of a deletion ─────────────────────────


def test_events_are_evaluated_independently(db, store):
    """Five events, one deletable. A guard that leaked across candidates would show up here as
    either an over-deletion or a missed one."""
    _record(db, 1, at=INSIDE, state=OutboxState.DELIVERED)  # deletable
    _record(db, 2, at=INSIDE, state=OutboxState.PENDING)  # un-drained
    _record(db, 3, at=TOO_RECENT, state=OutboxState.DELIVERED)  # too new
    _record(db, 4, at=INSIDE, state=OutboxState.DELIVERED)  # not covered
    _record(db, 5, at=INSIDE, state=OutboxState.QUARANTINED)  # terminal failure

    retention = _retention(store, watermark=_Watermark("evt-1", "evt-2", "evt-3", "evt-5"))
    assert retention.deletable(NOW) == ("evt-1",)
    assert retention.sweep(now=NOW) == 1
    assert _ids(db) == {"evt-2", "evt-3", "evt-4", "evt-5"}


def test_deletion_does_not_mutate_any_surviving_event(db, store):
    """The sanctioned exception removes rows; it never rewrites one (ED5 has no update path)."""
    _record(db, 1, at=INSIDE, state=OutboxState.DELIVERED)
    _record(db, 2, at=INSIDE, state=OutboxState.PENDING)
    before = db.session.get(DurableEventRow, "evt-2")
    snapshot = (before.event_id, before.seq, before.type, before.recorded_at, dict(before.data))

    assert _retention(store, watermark=_Watermark("evt-1")).sweep(now=NOW) == 1

    after = db.session.get(DurableEventRow, "evt-2")
    assert (after.event_id, after.seq, after.type, after.recorded_at, dict(after.data)) == snapshot


def test_retention_deletes_the_event_row_only_and_leaves_the_outbox_entry(db, store):
    """RT7 sanctions deleting *an `evt_event` row*, and the exception is read no wider than it
    is written — `DurableOutbox.count_entries` already anticipates the two counts diverging
    'only if retention ever removed one and not the other'."""
    _record(db, 1, at=INSIDE, state=OutboxState.DELIVERED)
    assert _retention(store, watermark=_Watermark("evt-1")).sweep(now=NOW) == 1

    assert db.session.get(DurableEventRow, "evt-1") is None
    entry = db.session.get(DurableOutboxRow, "evt-1")
    assert entry is not None and entry.state == OutboxState.DELIVERED.value


def test_a_deleted_event_is_gone_from_the_ordered_reads(db, store):
    _record(db, 1, at=INSIDE, state=OutboxState.DELIVERED)
    _record(db, 2, at=INSIDE, state=OutboxState.PENDING)
    _retention(store, watermark=_Watermark("evt-1")).sweep(now=NOW)

    log = SqlDurableEventLog(db)
    assert [e.id for e in log.read_all()] == ["evt-2"]
    assert [e.id for e in log.read_session("sess-1")] == ["evt-2"]
    assert log.count() == 1


def test_sweeping_twice_is_idempotent(db, store):
    _record(db, 1, at=INSIDE, state=OutboxState.DELIVERED)
    retention = _retention(store, watermark=_Watermark("evt-1"))

    assert retention.sweep(now=NOW) == 1
    assert retention.sweep(now=NOW) == 0
    assert _ids(db) == set()


def test_selection_is_a_query_and_deletes_nothing(db, store):
    """`deletable` must be safe to call alone — it is what makes the predicate independently
    testable, and a selection with a side effect would not be."""
    _record(db, 1, at=INSIDE, state=OutboxState.DELIVERED)
    retention = _retention(store, watermark=_Watermark("evt-1"))

    assert retention.deletable(NOW) == ("evt-1",)
    assert retention.deletable(NOW) == ("evt-1",)
    assert _ids(db) == {"evt-1"}


# ── F. the sweep's bounds ───────────────────────────────────────────────────────


def test_a_limit_bounds_the_sweep_deterministically(db, store):
    _record(db, 1, at=INSIDE, state=OutboxState.DELIVERED)
    _record(db, 2, at=INSIDE + 1, state=OutboxState.DELIVERED)
    _record(db, 3, at=INSIDE + 2, state=OutboxState.DELIVERED)
    retention = _retention(store, watermark=_Watermark("evt-1", "evt-2", "evt-3"))

    assert retention.sweep(now=NOW, limit=2) == 2
    assert _ids(db) == {"evt-3"}


def test_a_non_positive_limit_is_loud(db, store):
    """The `OutboxRecovery.recover` rule: a silently-zero sweep is indistinguishable from
    'nothing was eligible' and would hide a caller's bad batch size indefinitely."""
    retention = _retention(store, watermark=_Watermark())
    with pytest.raises(ValueError, match="positive integer"):
        retention.sweep(now=NOW, limit=0)
    with pytest.raises(ValueError, match="positive integer"):
        retention.sweep(now=NOW, limit=-1)


def test_candidates_are_ordered_deterministically(db, store):
    _record(db, 3, at=INSIDE + 2, state=OutboxState.DELIVERED)
    _record(db, 1, at=INSIDE, state=OutboxState.DELIVERED)
    _record(db, 2, at=INSIDE + 1, state=OutboxState.DELIVERED)

    ordered = [c.event_id for c in store.candidates(POLICY.window(NOW))]
    assert ordered == ["evt-1", "evt-2", "evt-3"]


def test_candidates_are_restricted_to_the_window(db, store):
    _record(db, 1, at=INSIDE, state=OutboxState.DELIVERED)
    _record(db, 2, at=TOO_OLD, state=OutboxState.DELIVERED)
    _record(db, 3, at=TOO_RECENT, state=OutboxState.DELIVERED)

    assert [c.event_id for c in store.candidates(POLICY.window(NOW))] == ["evt-1"]


def test_the_policy_re_checks_the_window_even_if_a_store_over_selects(db, store):
    """The guard belongs to the policy, not to whichever store happens to be bound — a store
    that returned too much must still not be able to cause a deletion (RT8)."""

    class _OverSelecting:
        def __init__(self, inner):
            self._inner = inner
            self.deleted: list[str] = []

        def candidates(self, window):
            return self._inner.candidates(RetentionWindow(start=0.0, end=NOW + 1_000.0))

        def delete(self, event_ids):
            self.deleted.extend(event_ids)
            return len(event_ids)

    _record(db, 1, at=TOO_OLD, state=OutboxState.DELIVERED)
    _record(db, 2, at=TOO_RECENT, state=OutboxState.DELIVERED)
    over = _OverSelecting(store)

    assert EventRetention(over, _Watermark("evt-1", "evt-2"), POLICY).sweep(now=NOW) == 0
    assert over.deleted == []


# ── G. boundaries that must not have moved ──────────────────────────────────────


def _events_sources() -> list[pathlib.Path]:
    return sorted(pathlib.Path("events").rglob("*.py"))


def _imported_tops(path: pathlib.Path) -> set[str]:
    tops: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            tops.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            tops.update(a.name.split(".")[0] for a in node.names)
    return tops


def test_events_still_imports_no_billing_anywhere():
    """RT6/RR4 — the whole reason the attestation is an injected port. `events → billing` stays
    forbidden and PL-2 is not widened."""
    for path in _events_sources():
        assert "billing" not in _imported_tops(path), f"{path} imports billing"


def _code_only(path: str) -> str:
    """The module's **code**, with docstrings and comments stripped.

    The prose in these modules necessarily *names* what they must not use — "no Billing import",
    "`MetricTurnBuffer` is never consulted" — so a raw substring scan would match the very
    sentences that promise the guarantee. Re-emitting the AST without docstrings drops comments
    with them, leaving only what actually executes.
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
    return ast.unparse(ast.fix_missing_locations(tree))


def test_retention_names_no_billing_symbol():
    """A code-level check beside the import check: no repository, no database, no meter."""
    for name in ("events/retention.py", "events/persistence/retention.py"):
        source = _code_only(name)
        for token in (
            "billing",
            "BillingDatabase",
            "UsageRecord",
            "MeterSource",
            "CommercialMeter",
            "meter_code",
        ):
            assert token not in source, f"{name} mentions {token}"


def test_retention_does_not_depend_on_the_metric_buffer(db, store):
    """RT4 — `MetricTurnBuffer.drain` is destructive and its buffer is bounded and in-memory
    (ADR-0039 MB3/MB5), so it cannot answer a durable question about the past. Retention must
    not reference it at all."""
    for name in ("events/retention.py", "events/persistence/retention.py"):
        source = _code_only(name)
        for token in ("MetricTurnBuffer", "MetricStreamPort", "metric_stream", "api.metering"):
            assert token not in source, f"{name} references {token}"


def test_retention_starts_no_scheduler_thread_or_timer():
    """RT9/RC2 — caller-driven. *When* retention runs is a deployment concern."""
    for name in ("events/retention.py", "events/persistence/retention.py"):
        source = _code_only(name)
        for token in ("threading", "asyncio", "sleep", "Timer", "schedule", "while True"):
            assert token not in source, f"{name} contains {token}"


def test_the_durable_log_contract_is_unchanged():
    """RT11 / stop-condition: retention gets its own port precisely so no holder of the log
    acquires a removal path."""
    assert set(DurableEventLog.__abstractmethods__) == {
        "append",
        "append_many",
        "read_session",
        "read_all",
        "count",
    }
    surface = {n for n in dir(DurableEventLog) if not n.startswith("_")}
    assert surface == {"append", "append_many", "read_session", "read_all", "count"}


def test_the_durable_log_implementation_still_has_no_deletion_path():
    surface = {n for n in dir(SqlDurableEventLog) if not n.startswith("_")}
    forbidden = {n for n in surface if any(w in n for w in ("delete", "remove", "purge", "expire"))}
    assert not forbidden, forbidden


def test_the_spine_acquired_no_deletion_path():
    surface = {n for n in dir(SqlDurableEventSpine) if not n.startswith("_")}
    forbidden = {n for n in surface if any(w in n for w in ("delete", "remove", "purge", "expire"))}
    assert not forbidden, forbidden


def test_retention_requires_no_migration():
    """RT11 — the window scan and the drained read run against the M10.2/M10.6 schema."""
    from events.persistence.migrations import MIGRATIONS

    assert tuple(m.VERSION for m in MIGRATIONS) == ("0034", "0035", "0036")
    columns = set(DurableEventRow.__table__.columns.keys())
    assert "recorded_at" in columns
    assert "state" in set(DurableOutboxRow.__table__.columns.keys())


def test_the_orchestrator_is_still_not_wired_to_retention():
    """RT9 — M10.5 delivers the mechanism, not a deployment that runs it."""
    source = pathlib.Path("runtime/orchestrator/orchestrator.py").read_text()
    for token in ("EventRetention", "SqlRetentionStore", "RetentionPolicy", "retention"):
        assert token not in source, f"the orchestrator references {token}"


def test_no_production_module_constructs_retention():
    """The mechanism exists and nothing runs it — the DW10/RL17/RC16 posture, preserved."""
    offenders = []
    for path in sorted(pathlib.Path(".").glob("*/**/*.py")):
        parts = path.parts
        if parts[0] in {"tests", "voicebot", ".venv"} or "events" in parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in {"EventRetention", "SqlRetentionStore"}:
                    offenders.append(str(path))
    assert not offenders, f"retention is constructed in production code: {offenders}"
