"""`MetricTurnBuffer` (M10.10, ADR-0039) — the platform-owned metric-stream buffer.

M10.9 shipped the producer and nothing consumed it: `metric.turn` was published into a bus with
zero subscribers. This is the consumer, and it is the **only** component on this path that touches
the `EventBus` (ADR-0033 **MS3**). It subscribes in-process, decodes `metric.turn` envelopes into
Billing's frozen `MetricSignal`, and holds them until Billing pulls them through the equally frozen
`MetricStreamPort`.

**Why a subscriber and not an outbox destination** (ADR-0039 **MB1**). ADR-0033 MS3 and plan
§M10.10 say the buffer *subscribes*; ADR-0035 **DW11** called it *"an outbox destination under DW5,
not an in-process subscriber"*. Those are mutually exclusive, and the owner ruled for in-process
subscription, scoped strictly to this delivery mechanism — ADR-0035 is not amended and DW11 remains
in force for every other purpose. The outbox reading was also not executable here: ADR-0037 **RC9**
defers a concrete `DeliveryTransport` and DW10/RL17/RC16 keep durable runtime wiring unwired. Being
an in-process *synchronous* subscriber, this buffer sits squarely inside the category **DW5**
already carves out, not against it.

**Why every method here is cheap** (**MB4**). `InProcessEventBus.publish()` is **synchronous
run-to-completion**: it stamps `seq`, may write durably inline, then drains its queue calling every
subscriber **on the caller's stack**. `metric.turn` is published from `TurnLatencyTap.complete()`,
which runs *inside the conversation coroutine* — so every millisecond spent in `record()` is charged
to a turn under invariant **I1** (voice-to-voice ≤ 1.0 s). `record()` therefore does bounded O(1)
in-memory work and nothing else: no I/O, no blocking, no sleeping, no provider call, no publish, no
thread/task/timer, and no lock. `EventDispatcher`'s per-subscriber isolation protects *correctness*
when a subscriber fails; it protects nothing at all against latency, so it is a safety net rather
than a licence, and this buffer is written to be **total**.

**Why it is bounded, and what that costs** (**MB3**). ADR-0033 MS5 makes a total Billing outage an
*expected, tolerated* condition — so an un-drained buffer is a normal steady state, and an unbounded
one on a long-lived voice process would be a guaranteed path to memory exhaustion during exactly the
scenario the architecture promises to survive. Capacity is fixed at `BUFFER_CAPACITY`, overflow
drops the **oldest**, and every drop is counted.

**This buffer is not durable, and nothing here should be read as implying it is.** There is no
persistence, no durable storage, no migration, no flush and no shutdown hook: **its contents are
lost on process exit**, and `replay()` is best-effort over what is still resident. A closed billing
period cannot be reconstructed from it (**MB6**).

**Not** a metrics framework, an aggregator, a tracer, a cost meter, a scheduler or a second
sequence authority. It resolves no meter — Billing resolves that from `event_type` against the
org's configured meters (**MB18**), which is why `meter_code` appears nowhere here.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import deque
from dataclasses import dataclass

from billing.foundation.catalog import MeterSource
from billing.foundation.domain import Scope
from billing.foundation.metering_factories import MetricSignal, MetricStreamPort
from core.events import Event, EventType

log = logging.getLogger("voxvoice.api.metering.metric_buffer")

#: The one capacity constant (**MB3**). Deliberately a literal and not a setting: M10.10 has no
#: configuration surface, so this may never be read from an environment variable, a settings
#: object, a database, an argument or a file.
BUFFER_CAPACITY = 10_000

#: `MetricSignal.producer` — provenance only, and never consulted by meter matching
#: (`CommercialMeter.accepts()` reads `source` and `event_type`). Module-qualified on purpose, so
#: the provenance names the exact producer rather than merely its plane (**MB15**).
PRODUCER = "observability.turn_latency"

#: `MetricSignal.raw_unit`. `voice_to_voice_ms` is milliseconds and nothing else is ever the
#: magnitude, so the unit is fixed rather than derived (**MB9**).
RAW_UNIT = "ms"

#: The payload key carrying the one scalar that may become `raw_units` (ADR-0038 TP5).
MEASUREMENT_KEY = "voice_to_voice_ms"

#: The subscription filter (**MB1**, plan §M10.10). Exactly one type: this buffer must never see
#: an event it was not ratified to consume. Expressed as the declared `EventTypeFilter` type
#: (`frozenset[EventType] | None`) rather than a bare member, so the filter is a set membership
#: test and can never degrade into a string containment test.
METRIC_TURN_FILTER: frozenset[EventType] = frozenset({EventType.METRIC_TURN})


@dataclass(frozen=True, slots=True)
class BufferCounters:
    """An immutable snapshot of the four independent counters (**MB3**, **MB8**–**MB10**).

    Four, not one: "we ran out of room", "that envelope was not decodable", "that turn had no
    voice-to-voice measurement" and "that event named no organization" call for four different
    operator responses, and collapsing them would hide the difference. A snapshot rather than live
    attributes so a reader cannot mutate accounting."""

    dropped: int = 0
    """Signals discarded by overflow — the oldest, per **MB3**."""

    malformed: int = 0
    """Envelopes that could not be decoded (**MB8**). *Never* incremented for a turn that merely
    lacks `voice_to_voice_ms` — that is normal operation and belongs to `missing_measurement`."""

    missing_measurement: int = 0
    """Turns with no `voice_to_voice_ms` (**MB9**) — interrupted or incomplete turns, which are
    expected rather than faulty."""

    unattributed: int = 0
    """Events that could not name an organization (**MB10**)."""


def canonical_payload_digest(data: dict) -> str:
    """SHA-256 over a canonical JSON rendering of the **payload only** (**MB15**).

    Sorted keys, compact separators, `ensure_ascii=False`, UTF-8 — a deterministic representation,
    so two structurally equal payloads always digest identically regardless of insertion order.

    `seq`, `ts`, `id` and envelope metadata are **excluded**: the digest is provenance for what was
    *measured*, so it must stay stable under everything the envelope stamps. That is what lets a
    later reconciliation prove *"the event this usage was derived from is the same event the log
    still holds"* without Billing keeping a second copy of a payload it does not own.

    Billing's private `_digest_id` is deliberately **not** reused — different inputs, different
    purpose, and a truncated output.
    """
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class MetricTurnBuffer(MetricStreamPort):
    """One process-wide, bounded, in-memory buffer of decoded `metric.turn` signals.

    Implements Billing's frozen `MetricStreamPort` from outside `platform/billing/**` (MS6), so
    Billing pulls and never subscribes. Process-wide and **org-keyed by the signal's own scope**
    rather than split into per-org or per-session buffers: one bound is enforceable, many are not,
    and a per-session buffer would make the capacity guarantee meaningless.
    """

    def __init__(self, *, capacity: int = BUFFER_CAPACITY) -> None:
        # `maxlen` is the second line of defence, not the first: `record()` accounts for the drop
        # explicitly before appending, because a silent `maxlen` eviction is invisible to an
        # operator. The parameter exists so tests can prove overflow behaviour without pushing
        # 10_000 events through the bus — production has exactly one capacity (MB3) and callers
        # in production must not pass this.
        self._capacity = capacity
        self._signals: deque[MetricSignal] = deque(maxlen=capacity)
        self._dropped = 0
        self._malformed = 0
        self._missing_measurement = 0
        self._unattributed = 0
        self._overflow_warned = False

    # ── subscription (MB1) ──────────────────────────────────────────────────

    def subscribe(self, bus) -> object:
        """Register `record` on `bus` for `metric.turn` **only**, returning the bus's handle.

        The bus is passed in rather than constructed: this buffer creates no `EventBus`, owns no
        bus lifecycle, and publishes nothing.
        """
        return bus.subscribe(self.record, types=METRIC_TURN_FILTER)

    # ── the hot path (MB4) ──────────────────────────────────────────────────

    def record(self, event: Event) -> None:
        """Decode one `metric.turn` and buffer it. Bounded O(1); never raises.

        Every rejection path returns normally after counting, because this runs on the publisher's
        stack inside a conversation turn. The precedence is fixed and deliberate:

        1. **undecodable** → `malformed` (**MB8**),
        2. **no measurement** → `missing_measurement` (**MB9**),
        3. **no organization** → `unattributed` (**MB10**).

        Identity is checked strictly and payload shape leniently: a missing *optional* key is
        normal, while a missing or unusable identity means the envelope is not the thing this
        buffer was ratified to consume.
        """
        try:
            signal = self._decode(event)
        except _Skip as skip:
            skip.count(self)
            return
        except Exception as exc:  # noqa: BLE001 — totality: nothing may reach the conversation
            self._malformed += 1
            log.warning("undecodable metric.turn dropped (%s): %r", _identify(event), exc)
            return
        self._append(signal)

    def _append(self, signal: MetricSignal) -> None:
        """Append, dropping the oldest at capacity (**MB3**).

        Only `deque.append`/`popleft` are used, each individually atomic, so a bus-side append and
        an API-side drain need **no mutual exclusion on the hot path**.
        """
        if len(self._signals) >= self._capacity:
            self._signals.popleft()
            self._dropped += 1
            if not self._overflow_warned:
                # First occurrence only. A line per drop would make the overflow guard itself an
                # unbounded cost on the hot path — the failure it exists to prevent (MB3).
                self._overflow_warned = True
                log.warning(
                    "metric buffer full at %d signals; dropping oldest "
                    "(further drops counted, not logged)",
                    self._capacity,
                )
        self._signals.append(signal)

    # ── decoding (MB15) ─────────────────────────────────────────────────────

    def _decode(self, event: Event) -> MetricSignal:
        """`Event` → `MetricSignal`, or raise `_Skip` for a ratified skip.

        Anything else that goes wrong propagates and is counted as malformed by `record`.
        """
        # ── identity: strict ────────────────────────────────────────────────
        if event.type is not EventType.METRIC_TURN:
            raise ValueError(f"expected metric.turn, got {event.type!r}")
        if not isinstance(event.id, str) or not event.id:
            raise ValueError("event id is missing")
        if not _is_int(event.seq):
            raise ValueError(f"event seq is not an integer: {event.seq!r}")
        if not isinstance(event.ts, (int, float)) or isinstance(event.ts, bool):
            raise ValueError(f"event ts is not a timestamp: {event.ts!r}")
        data = event.data
        if not isinstance(data, dict):
            raise ValueError(f"event data is not a mapping: {type(data).__name__}")

        # ── the measurement (MB9): absent is normal, present-but-unusable is not ──
        if MEASUREMENT_KEY not in data or data[MEASUREMENT_KEY] is None:
            raise _Skip.measurement(event)
        raw_units = data[MEASUREMENT_KEY]
        if not _is_int(raw_units):
            raise ValueError(f"{MEASUREMENT_KEY} is not an integer: {raw_units!r}")

        # ── attribution (MB10): validated *before* Scope, never caught after ─────
        org_id = event.org_id
        if not isinstance(org_id, str) or not org_id:
            raise _Skip.attribution(event)

        # `Scope` is constructed only once its precondition is known to hold, so
        # `BillingValidationError` can never escape onto the bus path.
        return MetricSignal(
            event_id=event.id,
            event_type=event.type.value,
            source=MeterSource.OBSERVABILITY,
            observed_at=float(event.ts),
            raw_units=raw_units,
            raw_unit=RAW_UNIT,
            scope=Scope(org_id=org_id),
            # `received_at` is left at its default: Billing's `MeteringFactory.source_event`
            # fills it from its own injected clock, because lateness is *Billing's* observation.
            producer=PRODUCER,
            # Provenance only (MB7). `Event.seq` is authoritative within one `EventBus` instance,
            # and the repository runs one bus per session — so this carries no cross-session or
            # global ordering guarantee, and no second sequence authority is introduced.
            sequence=event.seq,
            payload_digest=canonical_payload_digest(data),
        )

    # ── MetricStreamPort (MB5, MB6) ─────────────────────────────────────────

    def drain(self, org_id: str, limit: int = 500) -> tuple[MetricSignal, ...]:
        """Take up to `limit` of this organization's signals, oldest first. **Destructive.**

        Removal *is* the consumption state: there is no cursor, no acknowledgement and no read
        offset (**MB5**). Redelivery is not required to be impossible — Billing's ingestion is
        idempotent on `(event_id, meter_code)` (ED12) — but a non-destructive drain combined with a
        bound would let overflow discard the oldest signals *before anyone read them*, which is
        precisely the data this buffer exists to hold.

        Implemented as a single bounded rotation over the resident signals using only atomic
        `popleft`/`append`, so no lock is taken. The O(capacity) cost is acceptable because drain
        runs off the conversation hot path — it is an API request, never a bus dispatch.
        """
        if not _is_int(limit) or limit <= 0:
            raise ValueError(f"limit must be a positive integer, got {limit!r}")
        taken: list[MetricSignal] = []
        for _ in range(len(self._signals)):
            try:
                signal = self._signals.popleft()
            except IndexError:  # pragma: no cover — only reachable under concurrent drains
                break
            if len(taken) < limit and _org_of(signal) == org_id:
                taken.append(signal)
            else:
                # Everything not taken goes back in the order it was held, so other organizations
                # are untouched and this organization's remainder keeps its insertion order.
                self._signals.append(signal)
        return tuple(taken)

    def replay(
        self, org_id: str, period_start: float, period_end: float
    ) -> tuple[MetricSignal, ...]:
        """This organization's **resident** signals observed in `[period_start, period_end)`.

        **Non-destructive and best-effort.** A bounded in-memory buffer cannot guarantee historical
        coverage (**MB6**): it can never return a signal already discarded by overflow, already
        destructively drained, or lost when the process exited. It is not a replay store, and
        nothing here should be relied on to reconstruct a closed billing period.

        Half-open on purpose, matching `BillingPeriod`'s own `[start, end)` convention, so two
        adjacent periods can never both claim the same instant.
        """
        return tuple(
            signal
            for signal in tuple(self._signals)
            if _org_of(signal) == org_id and period_start <= signal.observed_at < period_end
        )

    # ── introspection (tests and operators; no behaviour) ───────────────────

    @property
    def counters(self) -> BufferCounters:
        """The four independent counters (**MB3**, **MB8**–**MB10**), as a snapshot."""
        return BufferCounters(
            dropped=self._dropped,
            malformed=self._malformed,
            missing_measurement=self._missing_measurement,
            unattributed=self._unattributed,
        )

    @property
    def capacity(self) -> int:
        return self._capacity

    def __len__(self) -> int:
        """Resident signals. Bounded by `capacity`, always."""
        return len(self._signals)


# ── ratified skips ──────────────────────────────────────────────────────────


class _Skip(Exception):
    """A ratified reason to ingest nothing — **not** a decode failure.

    Modelled as a control-flow signal internal to this module so `_decode` has exactly one exit
    style, and so the two skip reasons can never be conflated with `malformed`. It never escapes
    `record`.
    """

    def __init__(self, kind: str, event: Event) -> None:
        super().__init__(kind)
        self.kind = kind
        self.event = event

    @classmethod
    def measurement(cls, event: Event) -> _Skip:
        return cls("missing_measurement", event)

    @classmethod
    def attribution(cls, event: Event) -> _Skip:
        return cls("unattributed", event)

    def count(self, buffer: MetricTurnBuffer) -> None:
        """Record the skip and say so at `DEBUG`.

        `DEBUG`, not `WARNING`: an interrupted turn with no voice-to-voice measurement and an event
        from an unwired composition root are both **normal operation**, and logging them as faults
        would train an operator to ignore the log.
        """
        if self.kind == "missing_measurement":
            buffer._missing_measurement += 1
            log.debug("metric.turn without %s ignored (%s)", MEASUREMENT_KEY, _identify(self.event))
        else:
            buffer._unattributed += 1
            log.debug("metric.turn without an org_id ignored (%s)", _identify(self.event))


# ── helpers ─────────────────────────────────────────────────────────────────


def _is_int(value: object) -> bool:
    """A real integer. `bool` is an `int` subclass and is never a magnitude or a sequence."""
    return isinstance(value, int) and not isinstance(value, bool)


def _org_of(signal: MetricSignal) -> str:
    """The organization a buffered signal is attributed to.

    Every buffered signal has a `Scope` by construction (MB10 skips the ones that cannot), so this
    is total — the guard is here so a future reader does not have to prove that invariant again.
    """
    return signal.scope.org_id if signal.scope is not None else ""


def _identify(event: Event) -> str:
    """A short, allocation-cheap identity for a log line. Never the payload."""
    return f"event_id={getattr(event, 'id', '?')!r}"
