"""The platform metric-stream buffer (M10.10) — ADR-0039 + Amendment 1.

M10.9 shipped a producer nothing consumed. These tests prove the consumer: that it subscribes
in-process to `metric.turn` **only**, decodes into Billing's frozen `MetricSignal` with the exact
ratified field mapping, stays bounded, counts its four rejection paths separately, and hands
signals to Billing through the frozen `MetricStreamPort` without Billing ever touching the bus.

The negative assertions carry most of the weight here, because the risks are all things that would
*look* fine. A subscriber that performs I/O still passes a functional test — but it runs inside the
conversation coroutine under a 1.0 s budget (**I1**). A `raw_units = 0` for an unmeasured turn still
produces a valid `MetricSignal` — but it is fabricated usage on a revenue path. A `MeterSource` of
`RUNTIME_SESSION` still decodes — and would let `CommercialMeter.accepts()` fold turn latency into
an unrelated commercial meter. So the static guards and the skip-path tests are not ceremony; they
are the milestone.
"""

from __future__ import annotations

import json
import logging
import pathlib
import subprocess

import pytest

from api.metering import (
    BUFFER_CAPACITY,
    METRIC_TURN_FILTER,
    PRODUCER,
    RAW_UNIT,
    BufferCounters,
    MetricTurnBuffer,
    canonical_payload_digest,
)
from billing.foundation.catalog import MeterSource
from billing.foundation.domain import Scope
from billing.foundation.metering_factories import MetricSignal, MetricStreamPort
from core.events import Event, EventMetadata, EventSource, EventType
from events import InProcessEventBus

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE_DIR = pathlib.Path("api/metering")


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, cwd=REPO_ROOT
    ).stdout.strip()


def _event(
    *,
    org_id: str = "org-1",
    seq: int = 1,
    ts: float = 1_000.0,
    data: dict | None = None,
    type: EventType = EventType.METRIC_TURN,
    id: str = "evt-1",
) -> Event:
    """One `metric.turn` envelope, shaped exactly as `EventPublisher` stamps it."""
    return Event(
        id=id,
        type=type,
        ts=ts,
        seq=seq,
        session_id="sess-1",
        source=EventSource.OBSERVABILITY,
        org_id=org_id,
        data={"status": "completed", "voice_to_voice_ms": 800} if data is None else data,
    )


@pytest.fixture
def buffer() -> MetricTurnBuffer:
    return MetricTurnBuffer()


# ── A. the MetricStreamPort implementation ──────────────────────────────────


def test_the_buffer_is_a_metric_stream_port():
    """MS4/MS6 — the platform implements Billing's port from outside `billing/**`."""
    assert issubclass(MetricTurnBuffer, MetricStreamPort)
    assert isinstance(MetricTurnBuffer(), MetricStreamPort)


def test_the_buffer_satisfies_the_abstract_contract(buffer):
    """An ABC with unimplemented methods cannot be instantiated — so this also proves both
    abstract methods are concrete."""
    assert callable(buffer.drain)
    assert callable(buffer.replay)
    assert not getattr(MetricTurnBuffer, "__abstractmethods__", frozenset())


def test_the_buffer_lives_outside_billing():
    """MS6 / CV1 — the implementation is under `platform/api/`, not in the frozen plane."""
    import api.metering.metric_buffer as module

    assert "/api/metering/" in module.__file__
    assert "/billing/" not in module.__file__


def test_the_port_exposes_no_write_method(buffer):
    """ADR-0023 §6 — Billing is structurally incapable of producing a metric, so the seam it
    pulls through must not grow an emit/publish/write door either."""
    for forbidden in ("emit", "publish", "write", "send", "push"):
        assert not hasattr(buffer, forbidden), forbidden


# ── B. subscription ─────────────────────────────────────────────────────────


def test_the_filter_is_exactly_metric_turn():
    assert METRIC_TURN_FILTER == frozenset({EventType.METRIC_TURN})


def test_the_filter_is_a_frozenset_not_a_bare_member():
    """`EventRegistry.matching` evaluates `event.type in f`. With a bare `StrEnum` member that is
    a *substring* test, which is a correctness accident waiting to happen; with a frozenset it is
    set membership."""
    assert isinstance(METRIC_TURN_FILTER, frozenset)


def test_subscribing_registers_exactly_one_handler(buffer):
    bus = InProcessEventBus()
    buffer.subscribe(bus)
    assert bus.subscriber_count == 1


def test_the_buffer_receives_a_published_metric_turn(buffer):
    bus = InProcessEventBus()
    buffer.subscribe(bus)
    publisher = bus.publisher(
        EventMetadata(session_id="s-1", source=EventSource.OBSERVABILITY, org_id="org-1")
    )
    publisher.publish(EventType.METRIC_TURN, {"status": "completed", "voice_to_voice_ms": 700})
    assert len(buffer) == 1


def test_the_buffer_ignores_every_other_event_type(buffer):
    """The filter is the guard: unrelated traffic must never reach the decoder."""
    bus = InProcessEventBus()
    buffer.subscribe(bus)
    publisher = bus.publisher(
        EventMetadata(session_id="s-1", source=EventSource.RUNTIME, org_id="org-1")
    )
    for other in (
        EventType.TURN_COMPLETED,
        EventType.SESSION_STARTED,
        EventType.COST_UPDATED,
        EventType.TOOL_METRICS_UPDATED,
    ):
        publisher.publish(other, {"voice_to_voice_ms": 900})
    assert len(buffer) == 0
    assert buffer.counters == BufferCounters()


def test_the_buffer_creates_no_bus_of_its_own(buffer):
    """MB1 — it subscribes to a bus it is handed; it owns no bus lifecycle."""
    bus = InProcessEventBus()
    buffer.subscribe(bus)
    publisher = bus.publisher(
        EventMetadata(session_id="s-1", source=EventSource.OBSERVABILITY, org_id="org-1")
    )
    publisher.publish(EventType.METRIC_TURN, {"voice_to_voice_ms": 5})
    assert len(buffer) == 1  # arrived on *that* bus, not a second one


def test_subscribe_returns_the_bus_handle(buffer):
    bus = InProcessEventBus()
    handle = buffer.subscribe(bus)
    bus.unsubscribe(handle)
    assert bus.subscriber_count == 0


# ── C. decoding: the ratified field mapping (MB15) ──────────────────────────


def test_source_is_the_observability_meter_source(buffer):
    """**B-1.** Not `RUNTIME_SESSION` and not `CONTROL_PLANE_API`: `CommercialMeter.accepts()`
    matches on source plus an allow-list that is empty ⇒ *any* type from that source, so reusing
    an existing member would silently fold turn latency into an unrelated commercial meter."""
    buffer.record(_event())
    signal = buffer.drain("org-1")[0]
    assert signal.source is MeterSource.OBSERVABILITY
    assert signal.source.value == "observability"


def test_producer_is_the_module_qualified_producer(buffer):
    buffer.record(_event())
    assert buffer.drain("org-1")[0].producer == "observability.turn_latency"
    assert PRODUCER == "observability.turn_latency"


def test_raw_unit_is_milliseconds(buffer):
    buffer.record(_event())
    assert buffer.drain("org-1")[0].raw_unit == "ms"
    assert RAW_UNIT == "ms"


def test_raw_units_comes_only_from_voice_to_voice_ms(buffer):
    buffer.record(_event(data={"voice_to_voice_ms": 842, "ms": {"stt": 90, "llm": 300}}))
    assert buffer.drain("org-1")[0].raw_units == 842


def test_sequence_carries_event_seq(buffer):
    buffer.record(_event(seq=77))
    assert buffer.drain("org-1")[0].sequence == 77


def test_observed_at_is_the_event_timestamp(buffer):
    buffer.record(_event(ts=1_712_000_000.5))
    assert buffer.drain("org-1")[0].observed_at == 1_712_000_000.5


def test_observed_at_is_never_an_insertion_or_drain_time(buffer):
    """MB16 — the tap's monotonic clock and the buffer's own timing must not leak into
    `observed_at`; a wall-clock envelope instant is what Billing's lateness arithmetic needs."""
    buffer.record(_event(ts=10.0))
    first = buffer.replay("org-1", 0.0, 1e12)[0].observed_at
    second = buffer.drain("org-1")[0].observed_at
    assert first == second == 10.0


def test_scope_derives_from_the_envelope_org_id(buffer):
    buffer.record(_event(org_id="org-xyz"))
    assert buffer.drain("org-xyz")[0].scope == Scope(org_id="org-xyz")


def test_event_id_and_type_are_preserved(buffer):
    buffer.record(_event(id="evt-abc"))
    signal = buffer.drain("org-1")[0]
    assert signal.event_id == "evt-abc"
    assert signal.event_type == "metric.turn"


def test_received_at_is_left_for_billing(buffer):
    """Lateness is *Billing's* observation — `MeteringFactory.source_event` fills this from its
    own injected clock, so the buffer must not pre-empt it."""
    buffer.record(_event())
    assert buffer.drain("org-1")[0].received_at == 0.0


def test_the_decoded_signal_is_billings_own_type(buffer):
    buffer.record(_event())
    assert isinstance(buffer.drain("org-1")[0], MetricSignal)


def test_no_meter_code_is_resolved_or_carried(buffer):
    """MB18 — meter resolution stays Billing's, from `event_type` against configured meters."""
    buffer.record(_event())
    signal = buffer.drain("org-1")[0]
    assert not hasattr(signal, "meter_code")
    assert "meter_code" not in _module_code()["metric_buffer.py"]


def test_the_signal_converts_to_billings_source_event(buffer):
    """The decoded shape must be usable by the frozen ingestion path, not merely well-formed."""
    buffer.record(_event(seq=9))
    event = buffer.drain("org-1")[0].to_source_event()
    assert event.source is MeterSource.OBSERVABILITY
    assert event.sequence == 9
    assert event.event_type == "metric.turn"


# ── D. the payload digest (MB15) ────────────────────────────────────────────


def test_the_digest_is_a_full_sha256_hexdigest():
    digest = canonical_payload_digest({"a": 1})
    assert len(digest) == 64
    assert int(digest, 16) >= 0


def test_the_digest_matches_the_canonical_rules_exactly():
    data = {"b": 2, "a": {"z": 1, "y": [3, 4]}}
    import hashlib

    expected = hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    assert canonical_payload_digest(data) == expected


def test_the_digest_is_insertion_order_independent():
    """Deterministic representation: two structurally equal payloads must never disagree."""
    assert canonical_payload_digest({"a": 1, "b": 2}) == canonical_payload_digest({"b": 2, "a": 1})


def test_the_digest_is_deterministic_across_calls():
    data = {"status": "completed", "ms": {"llm": 120}}
    assert canonical_payload_digest(data) == canonical_payload_digest(data)


def test_the_digest_preserves_non_ascii_without_escaping():
    """`ensure_ascii=False` is part of the canonical form, so the digest is over the text itself."""
    import hashlib

    data = {"note": "café"}
    assert canonical_payload_digest(data) == hashlib.sha256('{"note":"café"}'.encode()).hexdigest()


def test_changing_the_payload_changes_the_digest(buffer):
    buffer.record(_event(id="a", data={"voice_to_voice_ms": 100}))
    buffer.record(_event(id="b", data={"voice_to_voice_ms": 101}))
    first, second = buffer.drain("org-1")
    assert first.payload_digest != second.payload_digest


def test_envelope_fields_do_not_affect_the_digest(buffer):
    """The digest covers `Event.data` **only** — `seq`, `ts`, `id` and metadata are excluded, so
    provenance stays stable under everything the envelope stamps."""
    payload = {"status": "completed", "voice_to_voice_ms": 250}
    buffer.record(_event(id="a", seq=1, ts=1.0, data=dict(payload)))
    buffer.record(_event(id="b", seq=999, ts=98765.4, data=dict(payload)))
    first, second = buffer.drain("org-1")
    assert first.payload_digest == second.payload_digest


def test_the_digest_ignores_envelope_metadata(buffer):
    """`Event.metadata` is not payload and must not move the digest."""
    payload = {"voice_to_voice_ms": 300}
    plain = _event(id="a", data=dict(payload))
    tagged = Event(
        id="b",
        type=EventType.METRIC_TURN,
        ts=1_000.0,
        seq=2,
        session_id="sess-1",
        source=EventSource.OBSERVABILITY,
        org_id="org-1",
        data=dict(payload),
        metadata={"trace": "abc"},
    )
    buffer.record(plain)
    buffer.record(tagged)
    first, second = buffer.drain("org-1")
    assert first.payload_digest == second.payload_digest


def test_billings_private_digest_id_is_not_reused():
    """Different inputs, different purpose, truncated output — borrowing it would couple this seam
    to a private Billing implementation detail."""
    source = (REPO_ROOT / "platform" / MODULE_DIR / "metric_buffer.py").read_text()
    assert "_digest_id" in source  # named only in the docstring that rejects it
    assert "from billing.foundation.metering_factories import _digest_id" not in source
    assert "_digest_id(" not in source


# ── E. capacity and overflow (MB3) ──────────────────────────────────────────


def test_capacity_is_exactly_ten_thousand():
    assert BUFFER_CAPACITY == 10_000
    assert MetricTurnBuffer().capacity == 10_000


def test_capacity_is_a_literal_not_a_setting():
    """M10.10 has no configuration surface. A capacity that can be tuned at runtime is a capacity
    nobody can reason about, and none of these lookups may appear."""
    source = _module_code()["metric_buffer.py"]
    for forbidden in ("os.environ", "getenv", "Settings", "config.", "load_dotenv"):
        assert forbidden not in source, forbidden


def test_the_buffer_never_exceeds_capacity():
    small = MetricTurnBuffer(capacity=5)
    for i in range(50):
        small.record(_event(id=f"e{i}", seq=i))
    assert len(small) == 5


def test_overflow_drops_the_oldest_and_keeps_the_newest():
    small = MetricTurnBuffer(capacity=3)
    for i in range(5):
        small.record(_event(id=f"e{i}", seq=i))
    kept = [s.event_id for s in small.drain("org-1")]
    assert kept == ["e2", "e3", "e4"]


def test_the_dropped_counter_counts_every_drop():
    small = MetricTurnBuffer(capacity=2)
    for i in range(7):
        small.record(_event(id=f"e{i}", seq=i))
    assert small.counters.dropped == 5


def test_the_full_buffer_still_accepts_new_signals():
    """Drop-oldest, not drop-newest: the freshest evidence must survive an outage."""
    small = MetricTurnBuffer(capacity=1)
    small.record(_event(id="old"))
    small.record(_event(id="new"))
    assert [s.event_id for s in small.drain("org-1")] == ["new"]


def test_overflow_warns_once_not_once_per_drop(caplog):
    """A log line per drop would make the overflow guard itself an unbounded hot-path cost."""
    small = MetricTurnBuffer(capacity=2)
    with caplog.at_level(logging.WARNING, logger="voxvoice.api.metering.metric_buffer"):
        for i in range(200):
            small.record(_event(id=f"e{i}", seq=i))
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert small.counters.dropped == 198


def test_overflow_is_reported_at_warning(caplog):
    small = MetricTurnBuffer(capacity=1)
    with caplog.at_level(logging.WARNING, logger="voxvoice.api.metering.metric_buffer"):
        small.record(_event(id="a"))
        small.record(_event(id="b"))
    assert any("full" in r.message for r in caplog.records)


# ── F. malformed envelopes (MB8) ────────────────────────────────────────────


@pytest.mark.parametrize(
    "event",
    [
        pytest.param(_event(id=""), id="no-event-id"),
        pytest.param(_event(seq="1"), id="non-integer-seq"),
        pytest.param(_event(seq=True), id="bool-seq"),
        pytest.param(_event(ts="yesterday"), id="non-numeric-ts"),
        pytest.param(_event(data=["not", "a", "mapping"]), id="non-mapping-data"),
        pytest.param(_event(data={"voice_to_voice_ms": "800"}), id="non-integer-measurement"),
        pytest.param(_event(data={"voice_to_voice_ms": True}), id="bool-measurement"),
        pytest.param(_event(type=EventType.TURN_COMPLETED), id="wrong-type"),
    ],
)
def test_a_malformed_envelope_is_dropped_and_counted(event, buffer):
    buffer.record(event)
    assert len(buffer) == 0
    assert buffer.counters.malformed == 1


def test_a_malformed_envelope_is_logged(caplog, buffer):
    with caplog.at_level(logging.WARNING, logger="voxvoice.api.metering.metric_buffer"):
        buffer.record(_event(seq="nope"))
    assert any("undecodable" in r.message for r in caplog.records)


def test_an_unserialisable_payload_is_malformed_not_a_crash(buffer):
    """The digest is computed inside the decode, so a payload JSON cannot render is a decode
    failure — and must still not raise."""
    buffer.record(_event(data={"voice_to_voice_ms": 10, "bad": object()}))
    assert buffer.counters.malformed == 1
    assert len(buffer) == 0


def test_the_subscriber_never_raises_into_the_bus(buffer):
    """MB4 totality. `EventDispatcher` isolates exceptions, but this buffer must not rely on it."""
    for event in (_event(seq="x"), _event(org_id=""), _event(data={}), _event(data=["x"])):
        buffer.record(event)  # must simply return
    assert len(buffer) == 0


def test_a_malformed_event_does_not_reach_the_dispatchers_isolation(caplog, buffer):
    """If the dispatcher had to isolate us, it would log *"subscriber raised (isolated)"*. The
    absence of that line is the proof that the buffer handled it itself."""
    bus = InProcessEventBus()
    buffer.subscribe(bus)
    with caplog.at_level(logging.WARNING):
        bus.publish(_event(data=["not", "a", "mapping"]))
    assert not any("isolated" in r.getMessage() for r in caplog.records)
    assert buffer.counters.malformed == 1


# ── G. no measurement (MB9) ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({"status": "interrupted"}, id="key-absent"),
        pytest.param({"status": "interrupted", "voice_to_voice_ms": None}, id="key-none"),
    ],
)
def test_a_turn_without_a_measurement_produces_no_signal(data, buffer):
    """ADR-0038 TP5 makes `voice_to_voice_ms` optional — an interrupted turn or one with no VAD
    has no complete voice-to-voice interval, and inventing one would be fabricated usage."""
    buffer.record(_event(data=data))
    assert len(buffer) == 0
    assert buffer.counters.missing_measurement == 1


def test_a_missing_measurement_is_not_malformed(buffer):
    """MB8 says so explicitly: a missing *optional* key is normal operation, not a decode fault."""
    buffer.record(_event(data={"status": "interrupted"}))
    assert buffer.counters.malformed == 0
    assert buffer.counters.missing_measurement == 1


def test_a_missing_measurement_logs_at_debug(caplog, buffer):
    with caplog.at_level(logging.DEBUG, logger="voxvoice.api.metering.metric_buffer"):
        buffer.record(_event(data={"status": "interrupted"}))
    records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert records and "voice_to_voice_ms" in records[0].getMessage()
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_no_zero_units_are_ever_fabricated(buffer):
    buffer.record(_event(data={"status": "interrupted"}))
    assert buffer.drain("org-1") == ()


def test_a_genuine_zero_measurement_is_still_ingested(buffer):
    """`0` from the payload is a real measurement; only a *fabricated* zero is forbidden."""
    buffer.record(_event(data={"voice_to_voice_ms": 0}))
    assert buffer.drain("org-1")[0].raw_units == 0


# ── H. no attribution (MB10) ────────────────────────────────────────────────


@pytest.mark.parametrize("org_id", [pytest.param("", id="empty"), pytest.param(None, id="none")])
def test_an_unattributable_event_produces_no_signal(org_id, buffer):
    buffer.record(_event(org_id=org_id))
    assert len(buffer) == 0
    assert buffer.counters.unattributed == 1


def test_an_unattributable_event_logs_at_debug(caplog, buffer):
    with caplog.at_level(logging.DEBUG, logger="voxvoice.api.metering.metric_buffer"):
        buffer.record(_event(org_id=""))
    assert any("org_id" in r.getMessage() for r in caplog.records)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_no_billing_validation_error_escapes(buffer):
    """`Scope.__post_init__` raises on an empty `org_id`. The buffer validates *before*
    constructing it, so the exception is never raised rather than caught after the fact."""
    from billing.foundation.errors import BillingValidationError

    try:
        buffer.record(_event(org_id=""))
    except BillingValidationError:  # pragma: no cover
        pytest.fail("BillingValidationError escaped onto the bus path")
    assert buffer.counters.unattributed == 1


def test_attribution_is_never_synthesised(buffer):
    """CV10 — ingest nothing rather than invent an organization that did not incur the usage."""
    buffer.record(_event(org_id=""))
    assert buffer.drain("") == ()
    assert len(buffer) == 0


def test_scope_is_never_none_on_a_buffered_signal(buffer):
    buffer.record(_event())
    assert buffer.drain("org-1")[0].scope is not None


# ── I. the four counters stay independent (MB3, MB8-MB10) ───────────────────


def test_each_rejection_path_increments_only_its_own_counter():
    small = MetricTurnBuffer(capacity=1)
    small.record(_event(id="a"))
    small.record(_event(id="b"))  # overflow → dropped
    small.record(_event(seq="x"))  # malformed
    small.record(_event(data={"status": "cancelled"}))  # no measurement
    small.record(_event(org_id=""))  # unattributed
    assert small.counters == BufferCounters(
        dropped=1, malformed=1, missing_measurement=1, unattributed=1
    )


def test_counters_start_at_zero(buffer):
    assert buffer.counters == BufferCounters(0, 0, 0, 0)


def test_counters_are_monotonic(buffer):
    for _ in range(3):
        buffer.record(_event(org_id=""))
    assert buffer.counters.unattributed == 3
    buffer.record(_event())
    assert buffer.counters.unattributed == 3  # never decreases


def test_the_counter_snapshot_is_immutable(buffer):
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        buffer.counters.dropped = 99


def test_counters_are_not_published_as_events(buffer):
    """MB-non-goals — no second telemetry framework and no counter events."""
    bus = InProcessEventBus()
    seen: list[Event] = []
    bus.subscribe(seen.append)
    buffer.subscribe(bus)
    bus.publish(_event(org_id=""))
    assert [e.type for e in seen] == [EventType.METRIC_TURN]


# ── J. drain (MB5) ──────────────────────────────────────────────────────────


def test_drain_is_destructive(buffer):
    buffer.record(_event())
    assert len(buffer.drain("org-1")) == 1
    assert buffer.drain("org-1") == ()
    assert len(buffer) == 0


def test_drain_returns_only_the_requested_org(buffer):
    buffer.record(_event(id="a", org_id="org-1"))
    buffer.record(_event(id="b", org_id="org-2"))
    assert [s.event_id for s in buffer.drain("org-1")] == ["a"]


def test_drain_leaves_other_orgs_untouched(buffer):
    buffer.record(_event(id="a", org_id="org-1"))
    buffer.record(_event(id="b", org_id="org-2"))
    buffer.record(_event(id="c", org_id="org-2"))
    buffer.drain("org-1")
    assert [s.event_id for s in buffer.drain("org-2")] == ["b", "c"]


def test_drain_preserves_insertion_order(buffer):
    for i in range(6):
        buffer.record(_event(id=f"e{i}", seq=i))
    assert [s.event_id for s in buffer.drain("org-1")] == [f"e{i}" for i in range(6)]


def test_drain_respects_the_limit(buffer):
    for i in range(10):
        buffer.record(_event(id=f"e{i}", seq=i))
    assert [s.event_id for s in buffer.drain("org-1", limit=3)] == ["e0", "e1", "e2"]
    assert len(buffer) == 7


def test_a_limited_drain_leaves_the_remainder_in_order(buffer):
    for i in range(5):
        buffer.record(_event(id=f"e{i}", seq=i))
    buffer.drain("org-1", limit=2)
    assert [s.event_id for s in buffer.drain("org-1")] == ["e2", "e3", "e4"]


def test_drain_has_the_frozen_default_limit(buffer):
    import inspect

    assert inspect.signature(buffer.drain).parameters["limit"].default == 500


def test_drain_rejects_a_non_positive_limit(buffer):
    """Following `OutboxRecovery.recover`'s precedent — a nonsensical limit is a caller bug, and
    this call is off the hot path, so failing loudly is correct here."""
    for bad in (0, -1, "5", None):
        with pytest.raises(ValueError):
            buffer.drain("org-1", limit=bad)


def test_draining_an_unknown_org_returns_empty(buffer):
    buffer.record(_event())
    assert buffer.drain("org-unknown") == ()
    assert len(buffer) == 1  # nothing consumed


def test_drain_returns_a_tuple(buffer):
    buffer.record(_event())
    assert isinstance(buffer.drain("org-1"), tuple)


def test_drain_introduces_no_cursor():
    """MB5 — removal *is* the consumption state; a second offset model is forbidden."""
    source = _module_code()["metric_buffer.py"]
    for forbidden in ("self._cursor", "self._offset", "self._acked", "checkpoint"):
        assert forbidden not in source, forbidden


# ── K. replay (MB6) ─────────────────────────────────────────────────────────


def test_replay_is_non_destructive(buffer):
    buffer.record(_event(ts=100.0))
    assert len(buffer.replay("org-1", 0.0, 1_000.0)) == 1
    assert len(buffer.replay("org-1", 0.0, 1_000.0)) == 1
    assert len(buffer) == 1


def test_replay_filters_by_observed_at(buffer):
    for i, ts in enumerate((10.0, 20.0, 30.0, 40.0)):
        buffer.record(_event(id=f"e{i}", ts=ts, seq=i))
    assert [s.event_id for s in buffer.replay("org-1", 15.0, 35.0)] == ["e1", "e2"]


def test_replay_is_half_open(buffer):
    """`[start, end)`, matching `BillingPeriod` — two adjacent periods must never both claim one
    instant."""
    buffer.record(_event(id="start", ts=10.0))
    buffer.record(_event(id="end", ts=20.0))
    assert [s.event_id for s in buffer.replay("org-1", 10.0, 20.0)] == ["start"]


def test_replay_filters_by_org(buffer):
    buffer.record(_event(id="a", org_id="org-1", ts=5.0))
    buffer.record(_event(id="b", org_id="org-2", ts=5.0))
    assert [s.event_id for s in buffer.replay("org-2", 0.0, 10.0)] == ["b"]


def test_replay_preserves_insertion_order(buffer):
    for i in range(5):
        buffer.record(_event(id=f"e{i}", ts=float(i), seq=i))
    assert [s.event_id for s in buffer.replay("org-1", 0.0, 100.0)] == [f"e{i}" for i in range(5)]


def test_replay_cannot_recover_drained_signals(buffer):
    """Resident-only, best-effort — the honest limitation, asserted so nobody relies on more."""
    buffer.record(_event(ts=5.0))
    buffer.drain("org-1")
    assert buffer.replay("org-1", 0.0, 10.0) == ()


def test_replay_cannot_recover_overflowed_signals():
    small = MetricTurnBuffer(capacity=2)
    for i in range(5):
        small.record(_event(id=f"e{i}", ts=float(i), seq=i))
    assert [s.event_id for s in small.replay("org-1", 0.0, 100.0)] == ["e3", "e4"]


def test_replay_returns_a_tuple(buffer):
    assert isinstance(buffer.replay("org-1", 0.0, 1.0), tuple)


def test_replay_adds_no_persistence_or_store():
    source = _module_code()["metric_buffer.py"]
    for forbidden in ("sqlite", "sqlmodel", "sqlalchemy", "Session(", "engine", "WAL", "commit("):
        assert forbidden not in source, forbidden


# ── L. the hot-path contract, statically (MB4) ──────────────────────────────


def _module_sources() -> dict[str, str]:
    return {
        path.name: path.read_text()
        for path in sorted((REPO_ROOT / "platform" / MODULE_DIR).glob("*.py"))
    }


def _code_only(source: str) -> str:
    """The module's *executable* text — docstrings and comments stripped.

    The prose in this package necessarily names the things the architecture forbids ("no lock",
    "not an outbox destination", "never publishes"), so a raw substring guard would fire on the
    documentation that exists to prevent the very thing it describes. Parsing and re-emitting the
    AST leaves only what actually runs, which is what these guards are about."""
    import ast

    tree = ast.parse(source)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                if isinstance(first.value.value, str):
                    body.pop(0)
    return ast.unparse(tree)


def _module_code() -> dict[str, str]:
    return {name: _code_only(source) for name, source in _module_sources().items()}


def test_the_package_imports_no_runtime_observability_or_events():
    """MB11/AR-15 — `api.metering → billing.foundation, core` and nothing else on this path."""
    for name, source in _module_code().items():
        for forbidden in (
            "import runtime",
            "from runtime",
            "import observability",
            "from observability",
            "import events",
            "from events",
            "import provider",
            "from provider",
        ):
            assert forbidden not in source, f"{name}: {forbidden}"


def test_the_package_never_publishes(buffer):
    """MB4 — a reentrant publish inside a synchronous run-to-completion dispatch is exactly the
    unbounded hot-path work this contract forbids."""
    for name, source in _module_code().items():
        for forbidden in ("EventPublisher", ".publish(", "publisher"):
            assert forbidden not in source, f"{name}: {forbidden}"


def test_the_package_performs_no_io():
    for name, source in _module_code().items():
        for forbidden in ("open(", "requests", "httpx", "socket", "urllib", "subprocess"):
            assert forbidden not in source, f"{name}: {forbidden}"


def test_the_package_spawns_no_thread_task_or_timer():
    """No scheduler, worker, thread or timer — MB4 and the M10.10 non-goals."""
    for name, source in _module_code().items():
        for forbidden in (
            "threading",
            "asyncio",
            "concurrent",
            "Timer",
            "sleep",
            "multiprocessing",
            "async def",
            "await ",
        ):
            assert forbidden not in source, f"{name}: {forbidden}"


def test_the_package_takes_no_lock():
    """MB4 — bus-side append and API-side drain use only atomic `deque` operations, so no mutual
    exclusion is needed on the hot path. A lock here would require a new owner ruling."""
    import re

    for name, source in _module_code().items():
        assert not re.search(r"\bLock\b", source), name
        assert not re.search(r"\block\b", source), name
        assert "acquire()" not in source, name


def test_the_package_declares_no_outbox_or_transport():
    """MB1/MB2 — this is an in-process subscriber, not an outbox destination."""
    for name, source in _module_code().items():
        for forbidden in ("DeliveryTransport", "Outbox", "outbox", "DurableEventLog", "durable"):
            assert forbidden not in source, f"{name}: {forbidden}"


def test_the_buffer_uses_a_bounded_deque():
    source = _module_sources()["metric_buffer.py"]
    assert "deque(maxlen=capacity)" in source
    assert "from collections import deque" in source


def test_recording_allocates_no_unbounded_state(buffer):
    """MB3 — resident state is bounded by capacity, never by session lifetime or event count."""
    for i in range(100):
        buffer.record(_event(id=f"e{i}", org_id=f"org-{i}", seq=i))
    assert len(buffer) == 100 <= buffer.capacity


# ── M. boundaries: CV5, Billing, dependency direction ───────────────────────


def test_no_pre_existing_api_file_was_modified():
    """**CV5.** The whole reason M10.10 is a *new package*: every file that existed under
    `platform/api/` before this milestone must be byte-for-byte unchanged."""
    changed = [
        line
        for line in _git("diff", "--name-only", "HEAD", "--", "platform/api").splitlines()
        if line
    ]
    assert changed == [], changed


def test_the_new_package_is_the_only_api_addition():
    untracked = [
        line
        for line in _git(
            "ls-files", "--others", "--exclude-standard", "--", "platform/api"
        ).splitlines()
        if line
    ]
    assert all(line.startswith("platform/api/metering/") for line in untracked), untracked


def test_billing_is_unchanged_apart_from_the_authorized_enum_member():
    """CV1-CV3 — the single additive `MeterSource.OBSERVABILITY` is the only Billing change M10.10
    was ever authorized to rely on."""
    changed = [
        line
        for line in _git("diff", "--name-only", "HEAD", "--", "platform/billing").splitlines()
        if line
    ]
    assert changed in ([], ["platform/billing/foundation/catalog.py"]), changed


def test_the_authorized_meter_source_member_exists():
    assert MeterSource.OBSERVABILITY.value == "observability"


def test_the_pre_existing_meter_sources_are_unchanged():
    """Additive under AR-32: the vocabulary grew, nothing moved."""
    expected = {
        "RUNTIME_SESSION": "runtime.session",
        "PROVIDER_STT": "provider.stt",
        "PROVIDER_TTS": "provider.tts",
        "PROVIDER_LLM": "provider.llm",
        "KNOWLEDGE": "knowledge",
        "TOOL": "tool",
        "WORKFLOW": "workflow",
        "DEPLOYMENT": "deployment",
        "CONTROL_PLANE_API": "control_plane.api",
    }
    for name, value in expected.items():
        assert getattr(MeterSource, name).value == value, name


def test_billing_does_not_import_the_api_plane():
    """AR-15 — the edge is one-way, so the graph stays acyclic."""
    for path in (REPO_ROOT / "platform" / "billing").rglob("*.py"):
        source = path.read_text()
        assert "from api" not in source, path
        assert "import api" not in source, path


def test_billing_holds_no_event_bus_subscription():
    """MS2/MS5 — Billing pulls; it never subscribes, never opens a connection, never holds a
    runtime resource."""
    for path in (REPO_ROOT / "platform" / "billing").rglob("*.py"):
        source = path.read_text()
        assert "EventBus" not in source, path
        assert ".subscribe(" not in source, path


def test_the_null_metric_stream_default_is_intact():
    """CV10 — a build that forgets to wire this buffer ingests nothing rather than inventing
    usage."""
    source = (REPO_ROOT / "platform" / "billing" / "application" / "application.py").read_text()
    assert "NullMetricStream()" in source


def test_the_metric_stream_port_contract_is_unchanged():
    """The port stays frozen: two abstract methods, no third door (MS4)."""
    assert set(MetricStreamPort.__abstractmethods__) == {"drain", "replay"}


def test_no_new_event_vocabulary_is_introduced():
    """M10.10 consumes the M10.9 envelope; it defines no event and republishes nothing."""
    assert {m.value for m in EventType if m.value.startswith("metric.")} == {"metric.turn"}
    assert EventSource.OBSERVABILITY.value == "observability"


def test_the_certification_exception_is_narrow():
    """PL-2's exception is a *(path, sub-package)* pair, not a plane exemption (owner ruling).

    The negative cases are the point: an `api` module outside `api/metering/` and a metering module
    reaching past `billing.foundation` must both still be violations, or the exception has quietly
    become "api may import billing"."""
    from tests.certification.runner import (
        PERMITTED_BILLING_EDGES,
        _permitted_billing_edge,
    )
    from tests.certification.runner import (
        REPO_ROOT as CERT_ROOT,
    )

    assert PERMITTED_BILLING_EDGES == (("api/metering/", "billing.foundation"),)

    allowed = CERT_ROOT / "api" / "metering" / "metric_buffer.py"
    assert _permitted_billing_edge(allowed, {"billing.foundation.catalog"})
    # wrong Billing layer, right path
    assert not _permitted_billing_edge(allowed, {"billing.application.metering_engine"})
    # partially permitted is not permitted
    assert not _permitted_billing_edge(
        allowed, {"billing.foundation.domain", "billing.persistence.models"}
    )
    # right layer, wrong path
    assert not _permitted_billing_edge(
        CERT_ROOT / "api" / "routers" / "sessions.py", {"billing.foundation"}
    )


def test_pl2_still_forbids_every_other_plane():
    """`api` stays in `FORBIDDEN_PLANES`; the ruling narrowed one edge, it removed no protection."""
    from tests.certification.runner import FORBIDDEN_PLANES

    assert set(FORBIDDEN_PLANES) == {
        "runtime",
        "actuator",
        "api",
        "product",
        "bootstrap",
        "providers",
    }


def test_pl2_is_satisfied_and_names_the_authorized_edge():
    """The permission must be visible in the certification report, not only in the runner."""
    from tests.certification.runner import certify

    result = {r.control_id: r for r in certify().results}["PL-2"]
    assert result.passed
    assert result.violations == ()
    assert "api/metering/metric_buffer.py" in result.detail
    assert "billing.foundation" in result.detail


def test_no_migration_ships_with_this_milestone():
    assert "migration" not in "".join(_module_code().values()).lower()
