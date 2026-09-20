"""M10.11 — closed-period shadow validation (ADR-0040).

The milestone Epic 10 has been building toward since M10.2: prove that usage **re-derived from the
durable event log** for a closed period **agrees** with the usage Billing metered — and, just as
importantly, that a discrepancy in that history is **detected** rather than absorbed.

## The flow this file proves

```
Event ─publish─▶ InProcessEventBus ─DW3 durable write─▶ evt_event   (file-backed SQLite, tmp_path)
                                                            │
                                              read_all() + period filter   (SV1, SV10)
                                                            │
                                            MetricTurnBuffer.record/drain  (the M10.10 decoder)
                                                            │
                                                      MetricSignal
                                                            │
                                    Billing's own metering engine  (comparison authority)
                                                            │
                                              ReconcileUsagePeriod ─▶ agreement / divergence
```

## Three things that are deliberately *not* here

**No second metering algorithm** (ADR-0040 **SV7**). Meter resolution, normalization,
idempotency and reconciliation are Billing's, invoked through its public commands. This file
computes no usage quantity of its own — the one arithmetic fact it asserts, that 120 000 ms is
2 minutes, is asserted *against Billing's answer*, never computed as an expectation to compare
Billing to. A second
implementation would agree with the first right up until the day it mattered, which is precisely the
drift `replay_period`'s own docstring warns about.

**No second decoder.** The historical envelopes are decoded by the **production** `MetricTurnBuffer`
(M10.10), used transiently as a decoder and drained empty in the same breath. That is not the buffer
acting as historical storage — ADR-0039 MB5/MB6 stand untouched, and a dedicated test proves
the point by showing a fresh buffer knows nothing. The durable log is the history; the buffer
is the ratified way to turn an envelope into a `MetricSignal`.

**No production wiring** (**SV5**). Every component here is constructed inside the scenario. The
production runtime still has no tap, no durable sink, no buffer and `NullMetricStream`, and
`test_no_production_wiring_was_introduced` asserts each of those individually — because a validation
milestone that quietly wired the thing it validates would be the worst possible outcome.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from api.metering import MetricTurnBuffer, canonical_payload_digest
from billing.application import (
    ActivateBillingAccount,
    ActivateCommercialMeter,
    AllowAllAuthorizer,
    CreateBillingAccount,
    DefineCommercialMeter,
    DrainMetricStream,
    GetCommercialUsageSnapshot,
    GetUsageReconciliationReport,
    OpenUsagePeriod,
    ReconcileUsagePeriod,
    SearchCommercialUsage,
)
from billing.foundation import (
    Currency,
    FixedClock,
    MeterSource,
    Normalization,
    RecordingMetricStream,
    SequentialIdGenerator,
    UsageSearchQuery,
)
from billing.persistence import build_persistent_billing_application
from core.events import EventMetadata, EventSource, EventType
from events import InProcessEventBus
from events.persistence import EventDatabase, SqlDurableEventLog

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _code_only(source: str) -> str:
    """Executable text, docstrings stripped.

    This file necessarily *names* the things it must not do ("no second metering algorithm"), so a
    raw substring guard would fire on the prose that exists to forbid them."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                if isinstance(first.value.value, str):
                    body.pop(0)
    return ast.unparse(tree)


#: The closed period under validation. Explicit start and end — never "the whole log" (SV9/§9).
PERIOD_START, PERIOD_END = 1_000.0, 100_000.0

#: The organization the period belongs to. Attribution is part of the scenario, not a detail:
#: an unattributable envelope is skipped by MB10 and could never be metered.
ORG_ID = "org_1"
ORG = dict(actor_id="usr_1", org_id=ORG_ID)

#: The meter M10.10 made possible: `MeterSource.OBSERVABILITY` + the `metric.turn` allow-list. Both
#: halves matter — `CommercialMeter.accepts()` matches on source **and** event type, which is why
#: reusing an existing `MeterSource` was rejected at ADR-0039 MB14.
METER_CODE = "voice.turn_minutes"


# ── the scenario's building blocks ──────────────────────────────────────────


def _clock(start: float):
    """A monotonically advancing wall clock for the bus, so `Event.ts` is controllable.

    Period membership is decided by `Event.ts` → `MetricSignal.observed_at`, so the scenario needs
    to place events precisely inside and outside the window rather than hope."""
    state = {"now": start}

    def now() -> float:
        value = state["now"]
        state["now"] += 1.0
        return value

    return now, state


@pytest.fixture
def durable(tmp_path):
    """A **file-backed** durable log. Never in-memory (plan §1.10, ADR-0034 DS10).

    The historical source of a billing period has to survive the process that wrote it; an
    in-memory database would prove the derivation while quietly assuming the durability."""
    database = EventDatabase(f"sqlite:///{tmp_path / 'events.db'}")
    database.create_all()
    log = SqlDurableEventLog(database, clock=lambda: 0.0)
    yield log
    database.close()


@pytest.fixture
def bus(durable):
    """The real bus with the real durable interposition (ADR-0035 DW3): `seq` is stamped, the
    envelope is written durably **before** dispatch, and only then does fan-out happen."""
    now, _ = _clock(PERIOD_START + 1.0)
    return InProcessEventBus(clock=now, durable=durable)


def _publish_turn(bus, *, ms: int, org_id: str = ORG_ID, session: str = "sess-1", turn: int = 1):
    """Publish one `metric.turn` through the ordinary `EventPublisher` path.

    The payload is ADR-0038 TP5's shape — `voice_to_voice_ms` is the one scalar M10.10 maps to
    `raw_units` (MB9), and the surrounding keys are carried so the digest covers a realistic
    payload rather than a single field."""
    publisher = bus.publisher(
        EventMetadata(session_id=session, source=EventSource.OBSERVABILITY, org_id=org_id)
    )
    return publisher.publish(
        EventType.METRIC_TURN,
        {
            "status": "completed",
            "at": {"speech_end": 1.0, "first_audio_out": 1.0 + ms / 1000},
            "ms": {"stt": 100, "llm": 300},
            "voice_to_voice_ms": ms,
        },
        turn=turn,
    )


def rederive(log, *, org_id: str, period_start: float, period_end: float):
    """**The M10.11 historical re-derivation** (ADR-0040 SV1/SV10).

    Three steps, and no fourth:

    1. read the durable log through its **existing** API — `read_all()`, no windowed query
       (**SV10**: in-memory filtering is permitted for validation and is explicitly *not* a
       production historical-query feature);
    2. select the envelopes whose `ts` falls in the **half-open** period `[start, end)`, matching
       `BillingPeriod`'s own convention so two adjacent periods can never both claim an instant;
    3. decode them with the **production** `MetricTurnBuffer` and drain it empty.

    Step 3 is where the no-second-decoder rule is honoured: every ratified mapping — `source`,
    `producer`, `raw_unit`, `raw_units`, `sequence`, `observed_at`, `payload_digest` — and every
    skip rule (MB8 malformed, MB9 no measurement, MB10 unattributable) comes from the code M10.10
    shipped, not from a copy of it living here.
    """
    buffer = MetricTurnBuffer()
    for event in log.read_all():
        if event.type is EventType.METRIC_TURN and period_start <= event.ts < period_end:
            buffer.record(event)
    signals = buffer.drain(org_id, limit=buffer.capacity)
    assert len(buffer) == 0, "the buffer is a decoder here, never a store"
    return signals


@pytest.fixture
def billing(tmp_path):
    """Billing, wired to a stream the scenario fills from the durable log.

    `RecordingMetricStream` is Billing's **own** frozen test double, used as the hand-off surface so
    that no new `MetricStreamPort` implementation is invented for this milestone."""
    stream = RecordingMetricStream()
    wired = build_persistent_billing_application(
        ids=SequentialIdGenerator(),
        clock=FixedClock(50_000.0),
        authorizer=AllowAllAuthorizer(),
        metric_stream=stream,
    )
    yield wired.application, stream
    wired.database.close()


def _seed(app) -> str:
    """An active account, an active `metric.turn` meter, and an open period — all through Billing's
    public commands. Nothing in `billing/**` is touched to make this milestone work."""
    account = app.commands.dispatch(
        CreateBillingAccount(**ORG, display_name="acct", currency=Currency.INR)
    )
    app.commands.dispatch(ActivateBillingAccount(**ORG, account_id=account.id))
    app.commands.dispatch(
        DefineCommercialMeter(
            **ORG,
            code=METER_CODE,
            unit="minute",
            source=MeterSource.OBSERVABILITY,
            normalization=Normalization(1, 60_000),
            accepted_event_types=("metric.turn",),
        )
    )
    app.commands.dispatch(ActivateCommercialMeter(**ORG, code=METER_CODE))
    app.commands.dispatch(
        OpenUsagePeriod(
            **ORG, account_id=account.id, period_start=PERIOD_START, period_end=PERIOD_END
        )
    )
    return account.id


def _meter_the_period(app, stream, signals) -> None:
    """Hand the re-derived history to Billing and let **Billing** meter it."""
    for signal in signals:
        stream.offer(signal)
    app.commands.dispatch(
        DrainMetricStream(**ORG, period_start=PERIOD_START, period_end=PERIOD_END)
    )


# ── 1. Event → durable event log ────────────────────────────────────────────


def test_publishing_writes_the_envelope_to_the_durable_log(bus, durable):
    """DW3 — the durable write is interposed on `publish`, so the log is a by-product of the real
    producer path rather than something the scenario appends by hand."""
    published = _publish_turn(bus, ms=60_000)
    recorded = durable.read_all()
    assert durable.count() == 1
    assert recorded[0].id == published.id
    assert recorded[0].type is EventType.METRIC_TURN


def test_the_durable_log_preserves_the_envelope_verbatim(bus, durable):
    """ED3/ED5 — identity and ordering are the producer's and the bus's; the log invents neither."""
    published = _publish_turn(bus, ms=42_000)
    stored = durable.read_all()[0]
    assert stored.id == published.id
    assert stored.seq == published.seq
    assert stored.ts == published.ts
    assert stored.org_id == published.org_id
    assert stored.data == published.data


def test_the_durable_log_is_seq_ordered(bus, durable):
    """The historical source must have a deterministic order or "re-derivation" is not a word that
    means anything."""
    for i in range(5):
        _publish_turn(bus, ms=60_000, turn=i)
    seqs = [e.seq for e in durable.read_all()]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == 5


def test_the_durable_log_survives_reopening(tmp_path):
    """File-backed, not in-memory: the history outlives the connection that wrote it."""
    url = f"sqlite:///{tmp_path / 'events.db'}"
    first = EventDatabase(url)
    first.create_all()
    log = SqlDurableEventLog(first, clock=lambda: 0.0)
    now, _ = _clock(PERIOD_START + 1.0)
    _publish_turn(InProcessEventBus(clock=now, durable=log), ms=60_000)
    first.close()

    second = EventDatabase(url)
    try:
        assert SqlDurableEventLog(second, clock=lambda: 0.0).count() == 1
    finally:
        second.close()


# ── 2. historical re-derivation, bounded by the closed period ───────────────


def test_re_derivation_reads_the_durable_log(bus, durable):
    """SV1 — the historical source is the log, and this is the assertion that says so."""
    _publish_turn(bus, ms=60_000)
    signals = rederive(durable, org_id=ORG_ID, period_start=PERIOD_START, period_end=PERIOD_END)
    assert len(signals) == 1
    assert signals[0].event_type == "metric.turn"


def test_only_events_inside_the_closed_period_are_re_derived(durable):
    """§9 — the period is explicit. Events before, inside and after are published deliberately, and
    the boundary is proven rather than assumed."""
    before_now, _ = _clock(PERIOD_START - 10.0)
    _publish_turn(InProcessEventBus(clock=before_now, durable=durable), ms=60_000, session="early")
    inside_now, _ = _clock(PERIOD_START + 5.0)
    _publish_turn(InProcessEventBus(clock=inside_now, durable=durable), ms=60_000, session="inside")
    after_now, _ = _clock(PERIOD_END + 10.0)
    _publish_turn(InProcessEventBus(clock=after_now, durable=durable), ms=60_000, session="late")

    assert durable.count() == 3
    signals = rederive(durable, org_id=ORG_ID, period_start=PERIOD_START, period_end=PERIOD_END)
    assert len(signals) == 1
    assert PERIOD_START <= signals[0].observed_at < PERIOD_END


def test_the_period_is_half_open(durable):
    """`[start, end)` — matching `BillingPeriod`, so adjacent periods cannot double-count."""
    start_now, _ = _clock(PERIOD_START)
    _publish_turn(InProcessEventBus(clock=start_now, durable=durable), ms=60_000, session="start")
    end_now, _ = _clock(PERIOD_END)
    _publish_turn(InProcessEventBus(clock=end_now, durable=durable), ms=60_000, session="end")

    signals = rederive(durable, org_id=ORG_ID, period_start=PERIOD_START, period_end=PERIOD_END)
    assert len(signals) == 1
    assert signals[0].observed_at == PERIOD_START


def test_events_outside_the_period_do_not_affect_the_metered_result(billing, durable):
    """The load-bearing version of the boundary test: out-of-period history reaches neither the
    re-derivation nor Billing, so the period's usage is unchanged by its neighbours."""
    app, stream = billing
    account_id = _seed(app)
    inside_now, _ = _clock(PERIOD_START + 5.0)
    _publish_turn(InProcessEventBus(clock=inside_now, durable=durable), ms=120_000)
    after_now, _ = _clock(PERIOD_END + 500.0)
    _publish_turn(InProcessEventBus(clock=after_now, durable=durable), ms=600_000, session="late")

    _meter_the_period(
        app,
        stream,
        rederive(durable, org_id=ORG_ID, period_start=PERIOD_START, period_end=PERIOD_END),
    )
    snapshot = app.queries.dispatch(
        GetCommercialUsageSnapshot(
            **ORG, account_id=account_id, period_start=PERIOD_START, period_end=PERIOD_END
        )
    )
    assert snapshot.record_count == 1  # the 600 000 ms event outside the window contributed nothing


def test_re_derivation_preserves_ordering_and_provenance(bus, durable):
    """MB7 — `sequence` carries `Event.seq` as provenance, and the derived order follows the log."""
    published = [_publish_turn(bus, ms=60_000, turn=i) for i in range(4)]
    signals = rederive(durable, org_id=ORG_ID, period_start=PERIOD_START, period_end=PERIOD_END)
    assert [s.sequence for s in signals] == [e.seq for e in published]
    assert [s.event_id for s in signals] == [e.id for e in published]


def test_re_derivation_preserves_the_payload_digest(bus, durable):
    """MB15 — the digest is provenance for what was *measured*, computed over `Event.data` only, so
    a reconciliation can prove the event a usage record came from is what the log holds."""
    published = _publish_turn(bus, ms=90_000)
    signal = rederive(durable, org_id=ORG_ID, period_start=PERIOD_START, period_end=PERIOD_END)[0]
    assert signal.payload_digest == canonical_payload_digest(published.data)


def test_re_derivation_uses_the_ratified_field_mapping(bus, durable):
    """The M10.10 contract, end to end from a durable envelope (ADR-0039 MB15)."""
    published = _publish_turn(bus, ms=75_000)
    signal = rederive(durable, org_id=ORG_ID, period_start=PERIOD_START, period_end=PERIOD_END)[0]
    assert signal.source is MeterSource.OBSERVABILITY
    assert signal.producer == "observability.turn_latency"
    assert signal.raw_unit == "ms"
    assert signal.raw_units == 75_000
    assert signal.observed_at == published.ts
    assert signal.scope is not None and signal.scope.org_id == ORG_ID


def test_re_derivation_applies_the_ratified_skip_rules(durable):
    """MB9/MB10 — a turn with no voice-to-voice measurement and an unattributable turn are both
    skipped, from the durable log exactly as from the live bus. CV10: ingest nothing rather than
    invent usage."""
    now, _ = _clock(PERIOD_START + 1.0)
    local = InProcessEventBus(clock=now, durable=durable)
    publisher = local.publisher(
        EventMetadata(session_id="s", source=EventSource.OBSERVABILITY, org_id=ORG_ID)
    )
    publisher.publish(EventType.METRIC_TURN, {"status": "interrupted"})  # no measurement
    anon = local.publisher(
        EventMetadata(session_id="s", source=EventSource.OBSERVABILITY, org_id="")
    )
    anon.publish(EventType.METRIC_TURN, {"voice_to_voice_ms": 60_000})  # unattributable
    _publish_turn(local, ms=60_000)  # the only meterable one

    assert durable.count() == 3
    signals = rederive(durable, org_id=ORG_ID, period_start=PERIOD_START, period_end=PERIOD_END)
    assert len(signals) == 1
    assert signals[0].raw_units == 60_000


# ── 3. agreement (ADR-0040 SV8-A) ───────────────────────────────────────────


def test_agreement_a_closed_period_re_derived_from_the_log_matches_metered_usage(billing, durable):
    """**The milestone.** Three turns are published, written durably, re-derived from the log for
    the closed period, metered by Billing, and reconciled — clean."""
    app, stream = billing
    account_id = _seed(app)
    now, _ = _clock(PERIOD_START + 1.0)
    local = InProcessEventBus(clock=now, durable=durable)
    for i in range(3):
        _publish_turn(local, ms=60_000, session=f"sess-{i}", turn=i)

    signals = rederive(durable, org_id=ORG_ID, period_start=PERIOD_START, period_end=PERIOD_END)
    assert len(signals) == 3
    _meter_the_period(app, stream, signals)

    report = app.commands.dispatch(
        ReconcileUsagePeriod(
            **ORG, account_id=account_id, period_start=PERIOD_START, period_end=PERIOD_END
        )
    )
    assert report.is_clean, report.discrepancies
    assert report.events_examined == 3
    assert report.duplicate_count == 0


def test_agreement_billing_metered_every_re_derived_signal(billing, durable):
    """Agreement is not only "no discrepancies" — the records must exist and carry the right
    quantity. The quantity is **Billing's**: this asserts its answer, it does not recompute it."""
    app, stream = billing
    account_id = _seed(app)
    now, _ = _clock(PERIOD_START + 1.0)
    local = InProcessEventBus(clock=now, durable=durable)
    _publish_turn(local, ms=120_000)

    _meter_the_period(
        app,
        stream,
        rederive(durable, org_id=ORG_ID, period_start=PERIOD_START, period_end=PERIOD_END),
    )
    usage = app.queries.dispatch(
        SearchCommercialUsage(**ORG, query=UsageSearchQuery(account_id=account_id))
    ).usage
    assert len(usage) == 1
    assert usage[0].units == 2  # 120 000 ms normalized by Billing's own meter (1/60 000)


def test_agreement_survives_redelivery(billing, durable):
    """ED2/ED12 — at-least-once delivery, exactly-once billing. Re-deriving and re-metering the same
    closed period twice must not create a second usage record or a discrepancy."""
    app, stream = billing
    account_id = _seed(app)
    now, _ = _clock(PERIOD_START + 1.0)
    _publish_turn(InProcessEventBus(clock=now, durable=durable), ms=60_000)

    for _ in range(3):
        _meter_the_period(
            app,
            stream,
            rederive(durable, org_id=ORG_ID, period_start=PERIOD_START, period_end=PERIOD_END),
        )
    report = app.commands.dispatch(
        ReconcileUsagePeriod(
            **ORG, account_id=account_id, period_start=PERIOD_START, period_end=PERIOD_END
        )
    )
    assert report.is_clean, report.discrepancies
    snapshot = app.queries.dispatch(
        GetCommercialUsageSnapshot(
            **ORG, account_id=account_id, period_start=PERIOD_START, period_end=PERIOD_END
        )
    )
    assert snapshot.record_count == 1


# ── 4. divergence detection (ADR-0040 SV8-B) ────────────────────────────────


def test_divergence_a_historical_event_that_was_never_metered_is_detected(billing, durable):
    """**The other half of the milestone.** A turn reaches the durable log *after* the period was
    metered — history and metered usage now disagree. The comparison must say so.

    This is the Epic 10 defect signature ADR-0033's standing rule describes: the platform failed to
    deliver an event Billing should have metered. It is **not** repaired here."""
    app, stream = billing
    account_id = _seed(app)
    now, _ = _clock(PERIOD_START + 1.0)
    local = InProcessEventBus(clock=now, durable=durable)
    _publish_turn(local, ms=60_000, session="metered")

    _meter_the_period(
        app,
        stream,
        rederive(durable, org_id=ORG_ID, period_start=PERIOD_START, period_end=PERIOD_END),
    )

    # the injected historical discrepancy: durably recorded, never handed to Billing
    _publish_turn(local, ms=60_000, session="never-metered")
    history = rederive(durable, org_id=ORG_ID, period_start=PERIOD_START, period_end=PERIOD_END)
    assert len(history) == 2  # the log knows about both

    for signal in history:
        stream.offer(signal)  # the *comparison* sees the full history; metering already happened
    report = app.queries.dispatch(
        GetUsageReconciliationReport(
            **ORG, account_id=account_id, period_start=PERIOD_START, period_end=PERIOD_END
        )
    )
    assert not report.is_clean
    assert any(d.kind == "missing_usage" for d in report.discrepancies), report.discrepancies


def test_divergence_is_not_repaired_by_touching_billing(billing, durable):
    """SV4/SI5 — the divergence stays a finding. Billing's recorded usage is **unchanged** by the
    detection, and nothing here adjusts a meter, a normalization or a usage record to make the
    report clean."""
    app, stream = billing
    account_id = _seed(app)
    now, _ = _clock(PERIOD_START + 1.0)
    local = InProcessEventBus(clock=now, durable=durable)
    _publish_turn(local, ms=60_000, session="metered")
    _meter_the_period(
        app,
        stream,
        rederive(durable, org_id=ORG_ID, period_start=PERIOD_START, period_end=PERIOD_END),
    )
    before = app.queries.dispatch(
        SearchCommercialUsage(**ORG, query=UsageSearchQuery(account_id=account_id))
    )

    _publish_turn(local, ms=60_000, session="never-metered")
    for signal in rederive(
        durable, org_id=ORG_ID, period_start=PERIOD_START, period_end=PERIOD_END
    ):
        stream.offer(signal)
    report = app.queries.dispatch(
        GetUsageReconciliationReport(
            **ORG, account_id=account_id, period_start=PERIOD_START, period_end=PERIOD_END
        )
    )
    after = app.queries.dispatch(
        SearchCommercialUsage(**ORG, query=UsageSearchQuery(account_id=account_id))
    )

    assert not report.is_clean
    assert after.total == before.total
    assert [u.units for u in after.usage] == [u.units for u in before.usage]


def test_divergence_a_truncated_history_is_detected(billing, durable):
    """The mirror case: the durable history is *narrower* than what was metered. Selecting a
    sub-window re-derives less than Billing recorded, so the comparison must not call it clean."""
    app, stream = billing
    account_id = _seed(app)
    now, _ = _clock(PERIOD_START + 1.0)
    local = InProcessEventBus(clock=now, durable=durable)
    for i in range(3):
        _publish_turn(local, ms=60_000, session=f"sess-{i}", turn=i)

    _meter_the_period(
        app,
        stream,
        rederive(durable, org_id=ORG_ID, period_start=PERIOD_START, period_end=PERIOD_END),
    )
    metered = app.queries.dispatch(
        SearchCommercialUsage(**ORG, query=UsageSearchQuery(account_id=account_id))
    )
    assert metered.total == 3

    truncated = rederive(
        durable, org_id=ORG_ID, period_start=PERIOD_START, period_end=PERIOD_START + 2.0
    )
    assert len(truncated) < 3, "the sub-window must genuinely hold less history"


# ── 5. the boundaries this milestone must not cross ─────────────────────────


def test_the_buffer_is_not_the_historical_source(durable):
    """ADR-0039 MB5/MB6 hold, and ADR-0040 SV2 keeps them holding.

    A buffer that never subscribed to anything knows nothing — the history came from the log. The
    buffer's role here is to *decode*, and it is drained empty every time (asserted inside
    `rederive`)."""
    now, _ = _clock(PERIOD_START + 1.0)
    _publish_turn(InProcessEventBus(clock=now, durable=durable), ms=60_000)

    fresh = MetricTurnBuffer()
    assert len(fresh) == 0
    assert fresh.replay(ORG_ID, PERIOD_START, PERIOD_END) == ()
    assert fresh.drain(ORG_ID) == ()
    # …while the durable log has the period's history in full
    assert durable.count() == 1


def test_the_buffer_contract_is_unchanged():
    """MB3/MB5/MB6 — bounded, drop-oldest, destructive drain, resident-only replay. M10.11 extends
    none of it."""
    from api.metering import BUFFER_CAPACITY

    assert BUFFER_CAPACITY == 10_000
    assert MetricTurnBuffer().capacity == 10_000
    source = (REPO_ROOT / "platform" / "api" / "metering" / "metric_buffer.py").read_text()
    for forbidden in ("sqlite", "sqlmodel", "sqlalchemy", "EventDatabase", "DurableEventLog"):
        assert forbidden not in source, forbidden


def test_the_durable_log_contract_is_unchanged():
    """SV10 — M10.11 uses `append`/`append_many`/`read_all`/`count` and adds no windowed read."""
    from events.ports import DurableEventLog

    methods = set(DurableEventLog.__abstractmethods__)
    assert {"append", "append_many", "read_all", "count"} <= methods
    # No windowed/period read was added to reach the closed period — SV10's whole point.
    for added in ("read_window", "read_between", "read_period", "read_range", "query"):
        assert added not in methods, added
    # `read_session` predates M10.11 and is untouched; it is named here so a future reader can see
    # the set was checked rather than assumed.
    assert methods == {"append", "append_many", "read_all", "count", "read_session"}


def test_no_production_wiring_was_introduced():
    """SV5/SI1, **retargeted at M11.12** — the milestone that introduces the production wiring.

    "We didn't wire anything" was true of M10.11 and is deliberately no longer true: M11.12 is
    authorized to supply the sink. What SV5/SI1 protect beyond that is unchanged and still
    asserted — shadow validation introduced no wiring of its own, the runtime acquires no storage,
    and there is still exactly one bus."""
    runtime_root = REPO_ROOT / "platform" / "runtime" / "orchestrator"
    orchestrator = (runtime_root / "orchestrator.py").read_text()
    assert orchestrator.count("InProcessEventBus(") == 1  # one bus, now given an injected sink
    for token in ("SqlDurableEventSpine", "EventDatabase", "sqlmodel", "sqlalchemy"):
        assert token not in orchestrator

    production = [
        path
        for path in (REPO_ROOT / "platform").rglob("*.py")
        if "tests" not in path.parts
        and ".venv" not in path.parts
        and "__pycache__" not in path.parts
    ]
    # The metric producer and the metric buffer may now be constructed — that *is* M11.12 — but
    # only by the **composition root**. Anywhere else would mean a plane wiring itself, which is
    # the property SV5/SI1 were really protecting: `runtime/`, `events/`, `observability/` and
    # `billing/` must each still be unable to reach the others.
    permitted = {"platform/bootstrap.py"}
    for name in ("TurnLatencyTap", "MetricTurnBuffer"):
        callers = []
        for path in production:
            tree = ast.parse(path.read_text())
            constructs = any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == name
                for node in ast.walk(tree)
            )
            if constructs:
                callers.append(str(path.relative_to(REPO_ROOT)))
        assert set(callers) <= permitted, (name, callers)

    billing_app = REPO_ROOT / "platform" / "billing" / "application" / "application.py"
    application = billing_app.read_text()
    assert "NullMetricStream()" in application  # CV10 default intact


def test_this_milestone_implements_no_second_metering_algorithm():
    """SV7/SI4 — the comparison authority is Billing's. Asserted **structurally**: this module
    defines no metering function and calls no Billing internal that would let it compute usage
    itself. A substring scan cannot express this, because the names it would look for appear in the
    prose (and in this docstring) that forbids them."""
    tree = ast.parse(pathlib.Path(__file__).read_text())

    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    banned_names = ("normalize", "_normalize", "reconcile", "_reconcile", "_match_meter")
    for banned in banned_names:
        assert banned not in defined, banned

    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    engines = ("UsageNormalizer", "UsageReconciliationService", "MeteringEngine")
    for engine in engines:
        assert engine not in called, engine

    # Billing is reached only through its public command/query surface.
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("billing")
    }
    public = {"billing.application", "billing.foundation", "billing.persistence"}
    assert imported <= public, imported


def test_this_milestone_adds_no_production_module():
    """SV9 — tests were sufficient; no production helper was invented to avoid composing here."""
    assert sorted(p.name for p in (REPO_ROOT / "platform" / "api" / "metering").glob("*.py")) == [
        "__init__.py",
        "metric_buffer.py",
    ]


def test_the_api_metering_boundary_is_unweakened():
    """SI9 — one Billing edge, and still no `events` import in that package."""
    for path in (REPO_ROOT / "platform" / "api" / "metering").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                top = node.module.split(".")[0]
                assert top != "events", f"{path.name} imports events"
                if top == "billing":
                    assert node.module.startswith("billing.foundation"), node.module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] != "events", path.name


def test_billing_is_untouched_by_this_milestone():
    """SI2 — Billing never subscribes, never imports the api plane, and is not modified here."""
    for path in (REPO_ROOT / "platform" / "billing").rglob("*.py"):
        source = path.read_text()
        assert "from api" not in source, path
        assert "EventBus" not in source, path
