"""Metering factories + repository contracts + the metric-stream seam (M9.11).

`MeteringFactory` is the one place a meter, a source event, a usage period, a quarantine entry,
or a correction is constructed, following the M9.2 factory discipline exactly: validate
structurally, **fail fast** on error-severity findings (`BillingValidationError`, §A2.6), compose
an immutable aggregate from the injected id and clock seams, and emit the corresponding
`billing.*` fact through the injected emitter.

**Exactly one event per business fact.** A rejected command emits nothing at all. Note the one
that is easy to miss: a *duplicate* ingestion emits `billing.usage.duplicate_ignored`, not
`billing.usage.recorded` — "we saw this event again and did nothing" is a distinct business fact,
and it is the evidence that idempotency worked.

No I/O, no persistence, no networking. Emission is the only effect and it is injected.

---

## The metric-stream seam

`MetricStreamPort` is the **only** way a `metric.*` signal reaches Billing, and it is an ABC
declared *here* rather than an import of any producer. That is not politeness — ADR-0031 IR1/IR3
confine this plane to `core` + `billing` + stdlib, and ADR-0023 M1 says Billing measures nothing.
The Customer Platform implements this port at the composition root, exactly as it implements
`BillingAuthorizer`.

Three consequences follow structurally rather than by convention:

* **Billing never polls Runtime and never reaches into runtime state.** The port is *push*: the
  platform hands Billing decoded facts. There is no method on it that fetches anything.
* **Billing never blocks Runtime or Deployment.** Nothing in this plane is on a runtime code
  path; a total Billing outage delays billing and nothing else (ADR-0031 D3).
* **Billing never becomes a metric producer.** The port has no emit, publish, or write method —
  there is nothing to call.

A build with no port wired ingests nothing, which is the safe failure: no usage is invented.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, replace

from .catalog import MeterAggregation, MeterSource
from .commercial_metering import (
    DEFAULT_LATE_ARRIVAL_WINDOW_SECONDS,
    CommercialMeter,
    CommercialUsage,
    CorrectionKind,
    MeteringAction,
    MeteringActivity,
    MeterState,
    Normalization,
    PeriodState,
    QuarantineEntry,
    QuarantineReason,
    SourceEvent,
    UsageAggregateSnapshot,
    UsageCorrection,
    UsagePeriod,
    UsageWindow,
    derive_correction_id,
)
from .domain import BillingPeriod, Scope
from .errors import BillingValidationError
from .events import BillingEvent, BillingEventType, EventEmitter, NullEventEmitter
from .metering_rules import MeteringValidator
from .seams import Clock, IdGenerator, SequentialIdGenerator, SystemClock

PREFIX_COMMERCIAL_METER = "cmt"
PREFIX_USAGE_PERIOD = "upd"
PREFIX_QUARANTINE = "qtn"
PREFIX_USAGE_AGGREGATE = "uag"
PREFIX_METERING_ACTIVITY = "mac"
PREFIX_INGEST_RUN = "irn"


# ── the metric-stream seam ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MetricSignal:
    """One decoded `metric.*` fact, as the platform hands it over.

    Deliberately minimal and deliberately *not* an operational metric type: it carries the
    identity, the raw magnitude, and the attribution scope, and nothing else. Billing keeps no
    copy of an operational payload it does not own, which is what stops this seam becoming a
    second home for runtime telemetry."""

    event_id: str
    event_type: str
    source: MeterSource
    observed_at: float
    raw_units: int
    raw_unit: str = ""
    scope: Scope | None = None
    received_at: float = 0.0
    producer: str = ""
    sequence: int = 0
    payload_digest: str = ""

    def to_source_event(self) -> SourceEvent:
        return SourceEvent(
            event_id=self.event_id,
            event_type=self.event_type,
            source=self.source,
            observed_at=self.observed_at,
            received_at=self.received_at or self.observed_at,
            producer=self.producer,
            sequence=self.sequence,
            payload_digest=self.payload_digest,
        )


class MetricStreamPort(ABC):
    """The **only** inbound path for `metric.*` signals. Implemented outside this plane.

    Read-only and pull-shaped by design: `drain` asks the platform's own buffer for what it has
    already received, so Billing never subscribes to a runtime bus, never opens a connection, and
    never holds a runtime resource. There is **no emit, publish, or write method** — Billing is
    structurally incapable of producing an operational metric (ADR-0023 §6).

    An unwired port means no ingestion, not invented usage."""

    @abstractmethod
    def drain(self, org_id: str, limit: int = 500) -> tuple[MetricSignal, ...]:
        """Take up to `limit` signals the platform has already decoded for this organization.

        Draining must be safe to call repeatedly and safe to call from several instances: a
        signal returned twice is not an error, because ingestion is idempotent on
        `(event_id, meter_code)`. That contract is what makes the whole pipeline
        at-least-once-safe rather than requiring the stream to be exactly-once."""

    @abstractmethod
    def replay(
        self, org_id: str, period_start: float, period_end: float
    ) -> tuple[MetricSignal, ...]:
        """Re-read a closed window from the authoritative log — the input to a deterministic
        replay. The log, not Billing, is the truth being re-read."""


class NullMetricStream(MetricStreamPort):
    """The safe default when no stream is wired: ingest nothing rather than invent usage."""

    def drain(self, org_id: str, limit: int = 500) -> tuple[MetricSignal, ...]:
        return ()

    def replay(
        self, org_id: str, period_start: float, period_end: float
    ) -> tuple[MetricSignal, ...]:
        return ()


class RecordingMetricStream(MetricStreamPort):
    """A deterministic in-memory stream for tests and for exercising replay.

    Append-only and order-stable, and `drain` is **non-destructive** on purpose: a real platform
    buffer may redeliver, and a test stream that could not would hide exactly the bug idempotency
    exists to prevent."""

    def __init__(self, signals: Sequence[MetricSignal] = ()) -> None:
        self._signals: list[MetricSignal] = list(signals)

    def offer(self, signal: MetricSignal) -> None:
        self._signals.append(signal)

    def drain(self, org_id: str, limit: int = 500) -> tuple[MetricSignal, ...]:
        return tuple(self._signals[:limit])

    def replay(
        self, org_id: str, period_start: float, period_end: float
    ) -> tuple[MetricSignal, ...]:
        return tuple(s for s in self._signals if period_start <= s.observed_at < period_end)

    @property
    def signals(self) -> tuple[MetricSignal, ...]:
        return tuple(self._signals)


# ── the factory ─────────────────────────────────────────────────────────────────


class MeteringFactory:
    """Composes validated metering aggregates. Stateless apart from its injected seams."""

    def __init__(
        self,
        *,
        ids: IdGenerator | None = None,
        clock: Clock | None = None,
        emitter: EventEmitter | None = None,
        validator: MeteringValidator | None = None,
    ) -> None:
        self._ids = ids or SequentialIdGenerator()
        self._clock = clock or SystemClock()
        self._emitter = emitter or NullEventEmitter()
        self._validator = validator or MeteringValidator()

    # ── meters ──────────────────────────────────────────────────────────────────

    def define_meter(
        self,
        *,
        org_id: str,
        code: str,
        unit: str,
        display_name: str = "",
        aggregation: MeterAggregation = MeterAggregation.SUM,
        source: MeterSource = MeterSource.RUNTIME_SESSION,
        normalization: Normalization | None = None,
        late_arrival_window_seconds: float = DEFAULT_LATE_ARRIVAL_WINDOW_SECONDS,
        accepted_event_types: Sequence[str] = (),
        description: str = "",
        existing: Sequence[CommercialMeter] = (),
        actor_id: str = "",
    ) -> CommercialMeter:
        """Declare a meter. Starts as a **draft** — a meter nobody has approved should not begin
        counting a customer's money by accident.

        `existing` is passed in so the cross-meter check runs at construction: two live meters
        claiming the same `(source, event type)` is the double-counting failure, and catching it
        when the second meter is defined is the only cheap moment to catch it."""
        now = self._clock.now()
        meter = CommercialMeter(
            id=self._ids.new(PREFIX_COMMERCIAL_METER),
            org_id=org_id,
            code=code,
            display_name=display_name or code,
            unit=unit,
            aggregation=aggregation,
            source=source,
            normalization=normalization or Normalization(),
            late_arrival_window_seconds=late_arrival_window_seconds,
            state=MeterState.DRAFT,
            version=1,
            description=description,
            accepted_event_types=tuple(accepted_event_types),
            created_at=now,
            updated_at=now,
        )
        self._fail_fast(self._validator.validate_meters([*existing, meter]))
        self._emit(
            BillingEventType.METER_CREATED,
            org_id,
            meter.id,
            actor_id,
            code=code,
            unit=unit,
            source=source.value,
        )
        return meter

    def version_meter(
        self,
        meter: CommercialMeter,
        *,
        actor_id: str = "",
        existing: Sequence[CommercialMeter] = (),
        **changes,
    ) -> CommercialMeter:
        """Amend a meter by **publishing a new version of it**.

        Never an in-place edit. Unit, aggregation, normalization, and rounding all change what a
        customer owes; a silent change to any of them would make historical usage
        unexplainable, because every record's provenance pins `code@version`."""
        amended = replace(
            meter,
            **{k: v for k, v in changes.items() if v is not None},
            version=meter.version + 1,
            updated_at=self._clock.now(),
        )
        others = [m for m in existing if m.id != meter.id]
        self._fail_fast(self._validator.validate_meters([*others, amended]))
        self._emit(
            BillingEventType.METER_VERSIONED,
            meter.org_id,
            meter.id,
            actor_id,
            code=meter.code,
            version=amended.version,
            fields=sorted(k for k, v in changes.items() if v is not None),
        )
        return amended

    def emit_meter_transition(
        self,
        meter: CommercialMeter,
        *,
        event_type: BillingEventType,
        actor_id: str = "",
        **data,
    ) -> None:
        """Emit the `billing.*` fact for a meter lifecycle move the lifecycle service applied.
        Kept here so every emission in this domain goes through one place."""
        self._emit(event_type, meter.org_id, meter.id, actor_id, **data)

    # ── source events ───────────────────────────────────────────────────────────

    def source_event(self, signal, *, received_at: float | None = None) -> SourceEvent:
        """Turn a decoded platform signal into the identity Billing records against.

        `received_at` defaults to *now* through the injected clock, because lateness is the
        difference between observation and receipt and Billing is the only party that knows when
        it received something."""
        event = signal.to_source_event()
        return replace(
            event,
            received_at=received_at
            if received_at is not None
            else (signal.received_at or self._clock.now()),
        )

    # ── usage periods ───────────────────────────────────────────────────────────

    def open_period(
        self,
        *,
        org_id: str,
        account_id: str,
        period: BillingPeriod,
        actor_id: str = "",
    ) -> UsagePeriod:
        now = self._clock.now()
        usage_period = UsagePeriod(
            id=self._ids.new(PREFIX_USAGE_PERIOD),
            org_id=org_id,
            account_id=account_id,
            period=period,
            state=PeriodState.OPEN,
            opened_at=now,
            updated_at=now,
        )
        self._fail_fast(self._validator.validate_period(usage_period))
        self._emit(
            BillingEventType.USAGE_PERIOD_OPENED,
            org_id,
            usage_period.id,
            actor_id,
            account_id=account_id,
            period_start=period.start,
            period_end=period.end,
        )
        return usage_period

    def emit_period_transition(
        self,
        period: UsagePeriod,
        *,
        event_type: BillingEventType,
        actor_id: str = "",
        **data,
    ) -> None:
        self._emit(event_type, period.org_id, period.id, actor_id, **data)

    # ── usage ───────────────────────────────────────────────────────────────────

    def emit_usage_recorded(self, usage: CommercialUsage, *, actor_id: str = "") -> None:
        """One fact per recorded usage. An adjustment emits `usage.adjusted` instead, because
        "we billed this late, in a different period" is a materially different fact from "we
        billed this"."""
        self._emit(
            BillingEventType.USAGE_ADJUSTED
            if usage.is_adjustment
            else BillingEventType.USAGE_RECORDED,
            usage.org_id,
            usage.id,
            actor_id,
            account_id=usage.account_id,
            meter=usage.meter_pin,
            units=usage.quantity.units,
            event_id=usage.source.event_id,
            disposition=usage.provenance.disposition.value,
        )

    def emit_duplicate_ignored(
        self, *, org_id: str, event_id: str, meter_code: str, actor_id: str = ""
    ) -> None:
        """A duplicate is a **success**, and recording it as a fact is what makes idempotency
        observable rather than merely believed."""
        self._emit(
            BillingEventType.USAGE_DUPLICATE_IGNORED,
            org_id,
            f"{event_id}:{meter_code}",
            actor_id,
            event_id=event_id,
            meter=meter_code,
        )

    # ── corrections ─────────────────────────────────────────────────────────────

    def correction(
        self,
        *,
        org_id: str,
        corrects_usage_id: str,
        kind: CorrectionKind,
        delta_units: int,
        unit: str,
        reason: str = "",
        reason_code: str = "",
        period: BillingPeriod | None = None,
        sequence: int = 1,
        actor_id: str = "",
    ) -> UsageCorrection:
        """Compose a correction. Its id is **derived**, like a usage id, so applying the same
        correction twice produces one record rather than two."""
        record = UsageCorrection(
            id=derive_correction_id(corrects_usage_id, reason_code or reason, sequence),
            org_id=org_id,
            corrects_usage_id=corrects_usage_id,
            kind=kind,
            delta_units=delta_units,
            unit=unit,
            reason=reason,
            reason_code=reason_code,
            period=period,
            created_at=self._clock.now(),
            created_by=actor_id,
        )
        self._fail_fast(self._validator.validate_correction(record))
        self._emit(
            BillingEventType.USAGE_CORRECTED,
            org_id,
            record.id,
            actor_id,
            corrects=corrects_usage_id,
            kind=kind.value,
            delta=delta_units,
        )
        return record

    # ── quarantine ──────────────────────────────────────────────────────────────

    def quarantine(
        self,
        *,
        org_id: str,
        event: SourceEvent,
        meter_code: str,
        reason: QuarantineReason,
        detail: str = "",
        account_id: str = "",
        scope: Scope | None = None,
        raw_units: int = 0,
        raw_unit: str = "",
        actor_id: str = "",
    ) -> QuarantineEntry:
        """Park an unbillable event. **Never dropped, never billed** — the visible half of total
        attribution (ADR-0023 M6)."""
        entry = QuarantineEntry(
            id=self._ids.new(PREFIX_QUARANTINE),
            org_id=org_id,
            source=event,
            meter_code=meter_code,
            reason=reason,
            detail=detail,
            account_id=account_id,
            scope=scope,
            raw_units=raw_units,
            raw_unit=raw_unit,
            received_at=self._clock.now(),
        )
        self._emit(
            BillingEventType.USAGE_QUARANTINED,
            org_id,
            entry.id,
            actor_id,
            event_id=event.event_id,
            meter=meter_code,
            reason=reason.value,
        )
        return entry

    def emit_quarantine_transition(
        self,
        entry: QuarantineEntry,
        *,
        event_type: BillingEventType,
        actor_id: str = "",
        **data,
    ) -> None:
        self._emit(event_type, entry.org_id, entry.id, actor_id, **data)

    # ── aggregates ──────────────────────────────────────────────────────────────

    def aggregate_id(self, *, account_id: str, meter_code: str, window: UsageWindow) -> str:
        """A **derived** aggregate id, so rebuilding an aggregate replaces it rather than
        creating a second one that disagrees with the first."""
        return "uag_" + _digest_id(
            account_id, meter_code, window.granularity.value, str(window.start)
        )

    def emit_aggregates_rebuilt(
        self,
        *,
        org_id: str,
        account_id: str,
        period: BillingPeriod,
        count: int,
        actor_id: str = "",
    ) -> None:
        self._emit(
            BillingEventType.USAGE_AGGREGATES_REBUILT,
            org_id,
            account_id,
            actor_id,
            period_start=period.start,
            period_end=period.end,
            aggregates=count,
        )

    def emit_replayed(
        self,
        *,
        org_id: str,
        account_id: str,
        period: BillingPeriod,
        produced: int,
        duplicates: int,
        matched: bool,
        actor_id: str = "",
    ) -> None:
        self._emit(
            BillingEventType.USAGE_PERIOD_REPLAYED,
            org_id,
            account_id,
            actor_id,
            period_start=period.start,
            period_end=period.end,
            produced=produced,
            duplicates=duplicates,
            matched_seal=matched,
        )

    def emit_reconciled(
        self,
        *,
        org_id: str,
        account_id: str,
        period: BillingPeriod,
        discrepancies: int,
        actor_id: str = "",
    ) -> None:
        self._emit(
            BillingEventType.USAGE_PERIOD_RECONCILED,
            org_id,
            account_id,
            actor_id,
            period_start=period.start,
            period_end=period.end,
            discrepancies=discrepancies,
        )

    # ── history ─────────────────────────────────────────────────────────────────

    def activity(
        self,
        *,
        org_id: str,
        action: MeteringAction,
        actor_id: str = "",
        account_id: str = "",
        meter_code: str = "",
        event_id: str = "",
        usage_id: str = "",
        period: BillingPeriod | None = None,
        detail: str = "",
    ) -> MeteringActivity:
        return MeteringActivity(
            id=self._ids.new(PREFIX_METERING_ACTIVITY),
            org_id=org_id,
            action=action,
            actor_id=actor_id,
            at=self._clock.now(),
            account_id=account_id,
            meter_code=meter_code,
            event_id=event_id,
            usage_id=usage_id,
            period_start=period.start if period else None,
            period_end=period.end if period else None,
            detail=detail,
        )

    def new_run_id(self) -> str:
        return self._ids.new(PREFIX_INGEST_RUN)

    def now(self) -> float:
        """The one place this domain reads a clock, and only through the injected seam. Every
        rule in `metering_rules.py` takes `at` as an argument instead."""
        return self._clock.now()

    # ── internals ───────────────────────────────────────────────────────────────

    def _fail_fast(self, diagnostics) -> None:
        if diagnostics.has_errors:
            raise BillingValidationError(diagnostics)

    def _emit(
        self,
        event_type: BillingEventType,
        org_id: str,
        subject_id: str,
        actor_id: str,
        **data,
    ) -> None:
        self._emitter.emit(
            BillingEvent(
                id=self._ids.new("bev"),
                type=event_type,
                ts=self._clock.now(),
                org_id=org_id,
                subject_id=subject_id,
                actor_id=actor_id,
                data=dict(data),
            )
        )


def _digest_id(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]


# ── repository contracts (additive to the M9.2 seam) ────────────────────────────


class CommercialMeterRepository(ABC):
    """The meter registry. Org-scoped by construction: every read filters on `org_id`, so a
    cross-tenant query is not expressible (§A11.2)."""

    @abstractmethod
    def add(self, meter: CommercialMeter) -> CommercialMeter: ...
    @abstractmethod
    def get(self, org_id: str, meter_id: str) -> CommercialMeter | None: ...
    @abstractmethod
    def by_code(self, org_id: str, code: str) -> CommercialMeter | None: ...
    @abstractmethod
    def all(self, org_id: str) -> tuple[CommercialMeter, ...]: ...
    @abstractmethod
    def replace(self, meter: CommercialMeter) -> CommercialMeter:
        """Whole-record replacement. Retirement is a state, not a deletion — a meter that named
        what an issued invoice billed must remain readable."""


class CommercialUsageRepository(ABC):
    """**Append-only, and idempotent by `(org_id, event_id, meter_code)`.**

    There is no update method and no delete method — not by convention, but because none exists
    to call. `append` is the whole write surface, and it must be safe to call twice with the same
    record from two processes: implementations return the *stored* record on conflict rather than
    raising, which is what turns at-least-once delivery into exactly-once billing (ADR-0023 M4)
    even under concurrent ingestion."""

    @abstractmethod
    def append(self, usage: CommercialUsage) -> CommercialUsage:
        """Store, or return what is already stored for this source event. Never raises on a
        duplicate; never writes a second row for one event."""

    @abstractmethod
    def get(self, org_id: str, usage_id: str) -> CommercialUsage | None: ...
    @abstractmethod
    def exists(self, org_id: str, event_id: str, meter_code: str) -> bool: ...
    @abstractmethod
    def for_period(
        self, org_id: str, account_id: str, period_start: float, period_end: float
    ) -> tuple[CommercialUsage, ...]: ...
    @abstractmethod
    def all(self, org_id: str, limit: int = 1000) -> tuple[CommercialUsage, ...]: ...


class UsagePeriodRepository(ABC):
    """Usage periods. `replace` exists because a period's *state* advances; the sealed digest it
    carries never changes once written, which the lifecycle enforces above."""

    @abstractmethod
    def add(self, period: UsagePeriod) -> UsagePeriod: ...
    @abstractmethod
    def get(self, org_id: str, period_id: str) -> UsagePeriod | None: ...
    @abstractmethod
    def for_account(
        self, org_id: str, account_id: str, period_start: float, period_end: float
    ) -> UsagePeriod | None: ...
    @abstractmethod
    def all(self, org_id: str, account_id: str = "") -> tuple[UsagePeriod, ...]: ...
    @abstractmethod
    def replace(self, period: UsagePeriod) -> UsagePeriod: ...


class UsageAggregateSnapshotRepository(ABC):
    """Derived aggregates. Replaceable by construction — they are recomputable from usage at any
    time, and a rebuilt aggregate must *replace* rather than accumulate beside the old one."""

    @abstractmethod
    def upsert(self, aggregate: UsageAggregateSnapshot) -> UsageAggregateSnapshot: ...
    @abstractmethod
    def for_period(
        self, org_id: str, account_id: str, period_start: float, period_end: float
    ) -> tuple[UsageAggregateSnapshot, ...]: ...
    @abstractmethod
    def get(self, org_id: str, aggregate_id: str) -> UsageAggregateSnapshot | None: ...


class QuarantineEntryRepository(ABC):
    """Quarantined events. No delete: unattributable usage must stay visible (M6)."""

    @abstractmethod
    def add(self, entry: QuarantineEntry) -> QuarantineEntry: ...
    @abstractmethod
    def get(self, org_id: str, entry_id: str) -> QuarantineEntry | None: ...
    @abstractmethod
    def pending(self, org_id: str, limit: int = 100) -> tuple[QuarantineEntry, ...]: ...
    @abstractmethod
    def all(self, org_id: str, limit: int = 500) -> tuple[QuarantineEntry, ...]: ...
    @abstractmethod
    def replace(self, entry: QuarantineEntry) -> QuarantineEntry:
        """State only — resolving or discarding. The source event it holds is never amended."""


class UsageCorrectionRepository(ABC):
    """Append-only corrections."""

    @abstractmethod
    def append(self, correction: UsageCorrection) -> UsageCorrection: ...
    @abstractmethod
    def for_usage(self, org_id: str, usage_id: str) -> tuple[UsageCorrection, ...]: ...


class MeteringActivityRepository(ABC):
    """Append-only metering history."""

    @abstractmethod
    def append(self, activity: MeteringActivity) -> MeteringActivity: ...
    @abstractmethod
    def history(
        self, org_id: str, account_id: str = "", limit: int = 100
    ) -> tuple[MeteringActivity, ...]:
        """Newest first. `account_id=""` spans the whole organization."""


__all__ = [
    "PREFIX_COMMERCIAL_METER",
    "PREFIX_INGEST_RUN",
    "PREFIX_METERING_ACTIVITY",
    "PREFIX_QUARANTINE",
    "PREFIX_USAGE_AGGREGATE",
    "PREFIX_USAGE_PERIOD",
    "CommercialMeterRepository",
    "CommercialUsageRepository",
    "MeteringActivityRepository",
    "MeteringFactory",
    "MetricSignal",
    "MetricStreamPort",
    "NullMetricStream",
    "QuarantineEntryRepository",
    "RecordingMetricStream",
    "UsageAggregateSnapshotRepository",
    "UsageCorrectionRepository",
    "UsagePeriodRepository",
]
