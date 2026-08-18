"""Metering Engine domain (M9.11) — Billing consumes; Billing never measures.

The most correctness-critical domain in Epic 9, because it is the only one where an error is
**silent**: a lost event under-bills and nobody complains, a duplicated event over-bills and the
customer leaves. Everything below is shaped so that the revenue ledger is *reproducible from the
event log* (ADR-0023 RS2), which is the only property that makes an accounting error findable.

The load-bearing invariant, restated as code shape rather than prose:

* **Billing never measures.** Nothing here reads a clock, a socket, a runtime, or a metric
  producer. A `SourceEvent` arrives already decoded through the `MetricStreamPort` seam
  (`metering_factories.py`), which the Customer Platform implements *outside* this plane — the
  same shape `BillingAuthorizer` uses. Billing imports no producer, polls nothing, and blocks
  nothing (ADR-0023 M1, ADR-0031 IR1/IR3/D3).
* **Exactly one authoritative usage record exists per source event.** `(org_id, event_id,
  meter_code)` is the identity, defended four deep: a pure ingestion decision here, an idempotent
  repository append, a database unique constraint, and an `IntegrityError` re-read that makes two
  processes racing on the same event indistinguishable from one processing it twice.
* **Usage is append-only.** There is no update and no delete anywhere in the domain. A correction
  is a *new* record that references the one it corrects.
* **Sealing is a one-way door.** `USAGE_PERIOD_LIFECYCLE` has no edge out of `sealed` at all, and
  a test enumerates the whole machine to prove it.

Two design decisions deserve stating before the code, because both are unusual and both are
deliberate:

**1. A usage record's id is *derived*, not minted.** `derive_usage_id` hashes
`(org_id, event_id, meter_code)`. Every other aggregate in Billing takes its id from the
`IdGenerator` seam; usage does not, because replay must produce **byte-identical** commercial
usage — including ids — or "reprocessing a period yields identical usage records" is a claim
nobody can check. A sequence-minted id would differ on every replay and quietly make the
guarantee unverifiable.

**2. A sealed period carries a digest of what it sealed.** `seal_digest` is a deterministic hash
over the sealed usage set, so a later replay can *prove* it reproduced the same commercial usage
rather than merely asserting it. That is the difference between a correctness property and a
correctness intention.

M9.2's `UsageStatus`, `MeterAggregation`, `MeterSource`, `Rounding`, `Quantity`, `Scope`,
`BillingPeriod`, and `fold` are **reused, not re-declared** — two answers to "how does this
aggregation reduce?" is exactly the drift §A2.5 exists to prevent.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum

from core.fsm import StateMachineDefinition, define

from .catalog import MeterAggregation, MeterSource
from .domain import BillingPeriod, Quantity, Scope
from .metering import UsageStatus, fold
from .money import Rounding

#: How far past a period's end a late event may still attach to that period, unless the meter
#: declares otherwise. Beyond it the event becomes an explicitly labelled adjustment in the
#: current period — never silently backdated, never silently dropped (Blueprint §7).
DEFAULT_LATE_ARRIVAL_WINDOW_SECONDS = 172_800.0  # 48 hours

#: The scale a meter's raw signal is normalized by, as an exact integer ratio. Floats are absent
#: from this domain entirely: a rounding difference in the eighth decimal place is a revenue
#: incident nobody can reproduce.
DEFAULT_NORMALIZATION_NUMERATOR = 1
DEFAULT_NORMALIZATION_DENOMINATOR = 1


# ── identity ────────────────────────────────────────────────────────────────────


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def derive_usage_id(org_id: str, event_id: str, meter_code: str) -> str:
    """The deterministic identity of one commercial usage record.

    Derived rather than minted so that **replay produces byte-identical usage, ids included**.
    A sequence-minted id would differ on every replay and make "reprocessing a period yields
    identical usage records" a claim nobody could check. Deriving it also means two processes
    racing on the same source event compute the *same* primary key, so the database's unique
    constraint arbitrates rather than two half-written rows coexisting."""
    return "usg_" + _digest(org_id, event_id, meter_code)[:32]


def derive_correction_id(usage_id: str, reason_code: str, sequence: int) -> str:
    """Corrections are also replay-stable: the same correction applied twice is one record."""
    return "ucr_" + _digest(usage_id, reason_code, str(sequence))[:32]


# ── meter lifecycle ─────────────────────────────────────────────────────────────


class MeterState(StrEnum):
    """The lifecycle of a commercial meter definition.

    Named `MeterState` because M9.2's `MeterDefinition` carries no lifecycle at all — it is a
    plain versioned record. This adds governance around it without reshaping it."""

    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class MeterTrigger(StrEnum):
    ACTIVATE = "activate"
    DEPRECATE = "deprecate"
    REINSTATE = "reinstate"
    RETIRE = "retire"


# The meter lifecycle. Three absences, each a decision:
#
#   * **no deletion state.** A meter names what a customer was billed for; deleting one would
#     make an issued invoice unexplainable (ADR-0023 RS4);
#   * there is no RETIRED → ACTIVE edge. A retired meter's code may have been reused in a new
#     definition; resurrecting the old one would make `code@version` ambiguous, and that pin is
#     what every usage record's provenance points at;
#   * there is no DRAFT → DEPRECATE edge: deprecating something never active is a distinction
#     without a difference.
#
# DEPRECATED is deliberately still ingestible: a deprecated meter stops being offered, it does
# not stop counting what customers are already sending. Only RETIRED refuses ingestion, and a
# retired meter's events are quarantined rather than dropped.
METER_LIFECYCLE: StateMachineDefinition = define(
    [
        (MeterState.DRAFT, MeterTrigger.ACTIVATE, MeterState.ACTIVE),
        (MeterState.ACTIVE, MeterTrigger.DEPRECATE, MeterState.DEPRECATED),
        (MeterState.ACTIVE, MeterTrigger.RETIRE, MeterState.RETIRED),
        (MeterState.DEPRECATED, MeterTrigger.REINSTATE, MeterState.ACTIVE),
        (MeterState.DEPRECATED, MeterTrigger.RETIRE, MeterState.RETIRED),
    ]
)

#: Meter states that still accept ingestion. A deprecated meter counts; a retired one does not.
INGESTIBLE_METER_STATES: frozenset[MeterState] = frozenset(
    {MeterState.ACTIVE, MeterState.DEPRECATED}
)


# ── usage period lifecycle ──────────────────────────────────────────────────────


class PeriodState(StrEnum):
    """A usage period's disposition. `sealed` is terminal — see `USAGE_PERIOD_LIFECYCLE`."""

    OPEN = "open"
    CLOSING = "closing"  # no new in-period usage; late arrivals still land as adjustments
    SEALED = "sealed"


class PeriodTrigger(StrEnum):
    BEGIN_CLOSE = "begin_close"
    SEAL = "seal"


# **Sealing is a one-way door** (ADR-0023 M7). There is no edge out of SEALED — not to OPEN, not
# to CLOSING, not to anything. After sealing, a correction is an adjustment in a later period or
# a credit note; it is never a retroactive mutation of what was billed.
#
# The direct OPEN → SEAL edge exists because a period with no usage at all has nothing to close
# gradually, and forcing an empty period through CLOSING would be ceremony rather than safety.
USAGE_PERIOD_LIFECYCLE: StateMachineDefinition = define(
    [
        (PeriodState.OPEN, PeriodTrigger.BEGIN_CLOSE, PeriodState.CLOSING),
        (PeriodState.OPEN, PeriodTrigger.SEAL, PeriodState.SEALED),
        (PeriodState.CLOSING, PeriodTrigger.SEAL, PeriodState.SEALED),
    ]
)

#: Period states that still accept in-period usage.
INGESTIBLE_PERIOD_STATES: frozenset[PeriodState] = frozenset({PeriodState.OPEN})


# ── quarantine lifecycle ────────────────────────────────────────────────────────


class QuarantineState(StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"  # re-ingested successfully after the cause was fixed
    DISCARDED = "discarded"  # judged not billable, with a recorded reason


class QuarantineTrigger(StrEnum):
    RESOLVE = "resolve"
    DISCARD = "discard"


# Quarantined events are **never deleted**: unattributable usage must be visible, not invisible
# (ADR-0023 M6). Both exits are terminal and both are recorded, so "what happened to that
# event?" always has an answer.
QUARANTINE_LIFECYCLE: StateMachineDefinition = define(
    [
        (QuarantineState.PENDING, QuarantineTrigger.RESOLVE, QuarantineState.RESOLVED),
        (QuarantineState.PENDING, QuarantineTrigger.DISCARD, QuarantineState.DISCARDED),
    ]
)


class QuarantineReason(StrEnum):
    """Why an event could not become commercial usage. A closed vocabulary, because an
    open-ended reason string cannot be alerted on or counted."""

    UNKNOWN_METER = "unknown_meter"
    RETIRED_METER = "retired_meter"
    NON_AUTHORITATIVE_SOURCE = "non_authoritative_source"
    UNATTRIBUTABLE = "unattributable"
    INVALID_QUANTITY = "invalid_quantity"
    UNIT_MISMATCH = "unit_mismatch"
    FUTURE_EVENT = "future_event"
    MALFORMED_EVENT = "malformed_event"
    NO_OPEN_PERIOD = "no_open_period"


# ── meters ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Normalization:
    """Raw signal → billable units, as an **exact integer ratio** plus a declared rounding.

    No float appears anywhere in this domain. Milliseconds→minutes is `1/60_000`, tokens→
    thousands is `1/1_000`; both are exact, and both are versioned with the meter because a
    silent normalization change is a revenue incident nobody can reproduce (Blueprint §5)."""

    numerator: int = DEFAULT_NORMALIZATION_NUMERATOR
    denominator: int = DEFAULT_NORMALIZATION_DENOMINATOR
    rounding: Rounding = Rounding.HALF_UP

    @property
    def is_identity(self) -> bool:
        return self.numerator == self.denominator

    @property
    def is_valid(self) -> bool:
        return self.numerator > 0 and self.denominator > 0


@dataclass(frozen=True, slots=True)
class CommercialMeter:
    """A governed, versioned meter definition: what is counted, by whom, in what unit, and how
    late an event may be and still belong to its period.

    Named `CommercialMeter` beside M9.2's `MeterDefinition`, which stays exactly as it is — the
    same call M9.6 made for `Product` beside `Plan`.

    **One meter has exactly one authoritative source.** If two planes emit an overlapping signal,
    exactly one is designated here; the other is quarantined as `NON_AUTHORITATIVE_SOURCE` rather
    than silently double-counted. That single field is what prevents the highest-severity metering
    failure mode there is (Blueprint §7, no-double-counting)."""

    id: str
    org_id: str
    code: str
    display_name: str = ""
    unit: str = "unit"
    aggregation: MeterAggregation = MeterAggregation.SUM
    source: MeterSource = MeterSource.RUNTIME_SESSION
    normalization: Normalization = field(default_factory=Normalization)
    late_arrival_window_seconds: float = DEFAULT_LATE_ARRIVAL_WINDOW_SECONDS
    state: MeterState = MeterState.DRAFT
    version: int = 1
    description: str = ""
    accepted_event_types: tuple[str, ...] = ()  # empty ⇒ any type from the authoritative source
    created_at: float = 0.0
    updated_at: float = 0.0
    activated_at: float | None = None
    retired_at: float | None = None

    @property
    def pin(self) -> str:
        """The stable identity of *what was counted*: `code@version`. Every usage record's
        provenance points at this, which is what makes an old invoice explainable after the
        meter has moved on."""
        return f"{self.code}@{self.version}"

    @property
    def is_ingestible(self) -> bool:
        return self.state in INGESTIBLE_METER_STATES

    @property
    def is_retired(self) -> bool:
        return self.state is MeterState.RETIRED

    def accepts(self, source: MeterSource, event_type: str) -> bool:
        """Whether this meter is the authoritative counter for a signal.

        Both halves matter: the *source* prevents two planes counting one thing, and the
        *event type* allow-list prevents one plane's unrelated event being folded into a meter
        that happens to share its source."""
        if source is not self.source:
            return False
        return not self.accepted_event_types or event_type in self.accepted_event_types

    def is_late(self, period: BillingPeriod, observed_at: float) -> bool:
        return observed_at >= period.end

    def within_late_window(self, period: BillingPeriod, received_at: float) -> bool:
        return received_at < period.end + self.late_arrival_window_seconds


# ── source events ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SourceEvent:
    """One decoded `metric.*` fact, as it arrived.

    Billing did not produce this and does not own it: the object exists so that a usage record
    can point back at exactly what it was derived from. `received_at` is separate from
    `observed_at` on purpose — the difference between them *is* lateness, and collapsing them
    would make the late-arrival policy undecidable.

    `payload_digest` is provenance, not data: it lets a reconciliation prove that the event a
    usage record was derived from is the same event the log still holds, without Billing keeping
    a second copy of an operational payload it does not own."""

    event_id: str
    event_type: str
    source: MeterSource
    observed_at: float
    received_at: float = 0.0
    producer: str = ""
    sequence: int = 0
    payload_digest: str = ""

    @property
    def lateness(self) -> float:
        """How long the event took to reach Billing. Never negative — a negative value would
        mean the stream claimed to deliver before it observed, which is a malformed event."""
        return max(self.received_at - self.observed_at, 0.0)

    def identity(self, meter_code: str) -> tuple[str, str]:
        """`(event id, meter code)` — the exactly-once key (ADR-0023 M4)."""
        return (self.event_id, meter_code)


class LateArrivalDisposition(StrEnum):
    """Where a late event lands. Declared as an enum rather than decided at a call site, because
    "which period did this get billed in?" must have one answer per event."""

    IN_PERIOD = "in_period"
    LATE_IN_WINDOW = "late_in_window"  # attaches to its original period
    LATE_OUT_OF_WINDOW = "late_out_of_window"  # adjustment in the current period
    PERIOD_SEALED = "period_sealed"  # adjustment; the original period is frozen


#: Dispositions that produce an adjustment rather than an in-period record.
ADJUSTING_DISPOSITIONS: frozenset[LateArrivalDisposition] = frozenset(
    {LateArrivalDisposition.LATE_OUT_OF_WINDOW, LateArrivalDisposition.PERIOD_SEALED}
)


# ── provenance ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class UsageProvenance:
    """Everything needed to explain — and re-derive — one usage record.

    Provenance is stored rather than reconstructed because the things it names are versioned and
    will move: the meter will gain a version, the normalization will change, the ingest run will
    be long finished. A record that pointed at *current* definitions would silently change what
    it claims about the past."""

    meter_pin: str  # code@version at ingestion time
    source_kind: MeterSource
    ingested_at: float
    ingest_run_id: str = ""
    normalization: Normalization = field(default_factory=Normalization)
    raw_units: int = 0
    raw_unit: str = ""
    disposition: LateArrivalDisposition = LateArrivalDisposition.IN_PERIOD
    replayed: bool = False

    @property
    def was_normalized(self) -> bool:
        return not self.normalization.is_identity

    @property
    def was_late(self) -> bool:
        return self.disposition is not LateArrivalDisposition.IN_PERIOD


# ── commercial usage ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CommercialUsage:
    """**The** billable fact Billing owns: one immutable, attributed, normalized record derived
    from exactly one source event.

    Named `CommercialUsage` beside M9.2's `UsageRecord`, which stays exactly as it is and which
    the M9.5 ingestion surface still writes.

    Append-only by construction: no field here is ever amended after creation, and there is no
    repository method that could. A correction is a *new* record with `ADJUSTMENT` status
    pointing at the one it corrects — which is why a customer can be shown not just what they
    were billed but every change to it and why.

    `UsageStatus` is M9.2's, reused unchanged: RECORDED / QUARANTINED / ADJUSTMENT already say
    everything this domain needs, and a second status enum would be two answers to one
    question."""

    id: str
    org_id: str
    account_id: str
    meter_code: str
    meter_version: int
    quantity: Quantity
    scope: Scope
    period: BillingPeriod
    source: SourceEvent
    provenance: UsageProvenance
    status: UsageStatus = UsageStatus.RECORDED
    subscription_id: str = ""
    corrects_usage_id: str = ""
    quarantine_reason: str = ""
    recorded_at: float = 0.0

    @property
    def is_billable(self) -> bool:
        return self.status in (UsageStatus.RECORDED, UsageStatus.ADJUSTMENT)

    @property
    def is_adjustment(self) -> bool:
        return self.status is UsageStatus.ADJUSTMENT

    @property
    def idempotency_key(self) -> tuple[str, str]:
        """`(source event id, meter code)` — the pair that makes ingestion idempotent."""
        return self.source.identity(self.meter_code)

    @property
    def meter_pin(self) -> str:
        return f"{self.meter_code}@{self.meter_version}"

    @property
    def digest(self) -> str:
        """A deterministic fingerprint of the *commercial content* of this record.

        Excludes `recorded_at` and the ingest run, which legitimately differ between an original
        ingestion and a replay of it. What it includes is everything a customer is billed on, so
        two runs producing equal digests is a proof of identical commercial outcome rather than
        a hope."""
        return _digest(
            self.id,
            self.org_id,
            self.account_id,
            self.meter_pin,
            str(self.quantity.units),
            self.quantity.unit,
            self.scope.narrowest_id,
            f"{self.period.start}:{self.period.end}",
            self.status.value,
            self.corrects_usage_id,
        )


class CorrectionKind(StrEnum):
    """Why a correction exists. Distinct kinds because they mean different things to an auditor:
    a *correction* fixes what was measured, an *adjustment* moves when it was billed, and a
    *reversal* withdraws it entirely."""

    CORRECTION = "correction"
    ADJUSTMENT = "adjustment"
    REVERSAL = "reversal"


@dataclass(frozen=True, slots=True)
class UsageCorrection:
    """An append-only record that something already billed needed changing.

    The correction never mutates the record it corrects. It carries the *delta* and the reason,
    so the original figure, the change, and the justification are all separately readable — which
    is what an auditor asks for and what a single overwritten number cannot provide."""

    id: str
    org_id: str
    corrects_usage_id: str
    kind: CorrectionKind
    delta_units: int  # signed: negative withdraws
    unit: str
    reason: str = ""
    reason_code: str = ""
    period: BillingPeriod | None = None  # the period the correction lands in
    created_at: float = 0.0
    created_by: str = ""
    resulting_usage_id: str = ""

    @property
    def withdraws(self) -> bool:
        return self.delta_units < 0


# ── aggregation ─────────────────────────────────────────────────────────────────


class WindowGranularity(StrEnum):
    """The aggregation windows Billing maintains.

    HOUR and DAY exist for dashboards and anomaly detection; PERIOD is the one rating and quota
    read. Keeping them explicit prevents the usual failure where a dashboard's number and an
    invoice's number are computed two different ways and disagree."""

    HOUR = "hour"
    DAY = "day"
    PERIOD = "period"


#: Window lengths in seconds. PERIOD has no fixed length — it is the billing period itself.
WINDOW_SECONDS: dict[WindowGranularity, float] = {
    WindowGranularity.HOUR: 3_600.0,
    WindowGranularity.DAY: 86_400.0,
}


@dataclass(frozen=True, slots=True)
class UsageWindow:
    """One half-open aggregation window `[start, end)`.

    Half-open for the same reason every other window in Billing is: with closed intervals an
    event on a boundary belongs to two windows, and the totals stop adding up."""

    granularity: WindowGranularity
    start: float
    end: float

    def contains(self, ts: float) -> bool:
        return self.start <= ts < self.end

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class UsageAggregateSnapshot:
    """A deterministic total for one meter, one scope, one window.

    Named beside M9.2's `UsageAggregate`, which remains the period-level aggregate the revenue
    spine reads. This adds windowing, a digest, and record provenance around the same idea.

    Carries `source_digest`: a fingerprint of the exact record set folded into it. Two
    aggregations over the same records produce the same digest, so "did the total change because
    usage changed, or because the aggregation changed?" is answerable — the question that
    otherwise turns a billing dispute into an archaeology exercise."""

    id: str
    org_id: str
    account_id: str
    meter_code: str
    scope: Scope
    window: UsageWindow
    period: BillingPeriod
    aggregation: MeterAggregation
    quantity: Quantity
    record_count: int = 0
    adjustment_count: int = 0
    source_digest: str = ""
    sealed: bool = False
    computed_at: float = 0.0

    @property
    def is_sealed(self) -> bool:
        return self.sealed


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    """What an account consumed in a period — the composed read model rating and quota consume.

    Derived, never stored: everything in it comes from the usage records and the aggregates, so
    there is no second copy of a billable fact to fall out of date (§A2.5)."""

    org_id: str
    account_id: str
    period: BillingPeriod
    at: float = 0.0
    aggregates: tuple[UsageAggregateSnapshot, ...] = ()
    record_count: int = 0
    adjustment_count: int = 0
    quarantined_count: int = 0
    state: PeriodState = PeriodState.OPEN
    digest: str = ""

    @property
    def is_sealed(self) -> bool:
        return self.state is PeriodState.SEALED

    @property
    def meter_codes(self) -> tuple[str, ...]:
        return tuple(sorted({a.meter_code for a in self.aggregates}))

    def total_for(self, meter_code: str) -> Quantity | None:
        matching = [a for a in self.aggregates if a.meter_code == meter_code]
        if not matching:
            return None
        return fold(matching[0].aggregation, tuple(a.quantity for a in matching))


# ── usage periods ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class UsagePeriod:
    """A billing period's metering state: whether it still accepts usage, and what it sealed.

    `seal_digest` is the load-bearing field. It is a deterministic hash over the usage the period
    contained at the moment of sealing, which means a replay months later can **prove** it
    reproduced the same commercial usage instead of merely asserting it. Without it, "replay is
    deterministic" is untestable in production, and a property nobody can test in production is a
    property that decays."""

    id: str
    org_id: str
    account_id: str
    period: BillingPeriod
    state: PeriodState = PeriodState.OPEN
    record_count: int = 0
    adjustment_count: int = 0
    quarantined_count: int = 0
    seal_digest: str = ""
    opened_at: float = 0.0
    closing_at: float | None = None
    sealed_at: float | None = None
    sealed_by: str = ""
    updated_at: float = 0.0

    @property
    def is_open(self) -> bool:
        return self.state is PeriodState.OPEN

    @property
    def is_sealed(self) -> bool:
        return self.state is PeriodState.SEALED

    @property
    def accepts_in_period_usage(self) -> bool:
        return self.state in INGESTIBLE_PERIOD_STATES


# ── quarantine ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class QuarantineEntry:
    """An event that could not become commercial usage, retained with its reason.

    The visible half of "attribution is total" (ADR-0023 M6): usage is never silently billed and
    never silently dropped. A quarantined event keeps its full source identity, so resolving the
    cause and re-ingesting produces exactly the record it would have produced originally —
    including the same derived id."""

    id: str
    org_id: str
    source: SourceEvent
    meter_code: str
    reason: QuarantineReason
    detail: str = ""
    state: QuarantineState = QuarantineState.PENDING
    account_id: str = ""  # may be unknown — that is often precisely why it is quarantined
    scope: Scope | None = None
    raw_units: int = 0
    raw_unit: str = ""
    received_at: float = 0.0
    resolved_at: float | None = None
    resolved_by: str = ""
    resolution_note: str = ""
    resulting_usage_id: str = ""

    @property
    def is_pending(self) -> bool:
        return self.state is QuarantineState.PENDING

    @property
    def is_resolvable(self) -> bool:
        return self.is_pending and bool(self.meter_code)


# ── reconciliation ──────────────────────────────────────────────────────────────


class DiscrepancyKind(StrEnum):
    """What reconciliation found. Each kind has a different remedy, so they are distinct rather
    than one "mismatch"."""

    MISSING_USAGE = "missing_usage"  # the log has an event with no usage record
    ORPHAN_USAGE = "orphan_usage"  # a usage record with no event in the log
    QUANTITY_MISMATCH = "quantity_mismatch"  # same event, different billable quantity
    DIGEST_MISMATCH = "digest_mismatch"  # same event, different derived content
    DUPLICATE_USAGE = "duplicate_usage"  # two records for one source event — must be zero


@dataclass(frozen=True, slots=True)
class UsageDiscrepancy:
    """One difference between what the event log says and what Billing recorded."""

    kind: DiscrepancyKind
    event_id: str
    meter_code: str = ""
    usage_id: str = ""
    expected: str = ""
    actual: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class UsageReconciliation:
    """The outcome of comparing the authoritative event log against recorded commercial usage.

    Run continuously rather than at period end (Blueprint §7, R-1). `is_clean` is the property
    an alert fires on; the discrepancies are what an engineer acts on."""

    org_id: str
    account_id: str
    period: BillingPeriod
    at: float = 0.0
    events_examined: int = 0
    usage_examined: int = 0
    discrepancies: tuple[UsageDiscrepancy, ...] = ()

    @property
    def is_clean(self) -> bool:
        return not self.discrepancies

    @property
    def duplicate_count(self) -> int:
        """Must always be zero. A non-zero value means exactly-once was violated, which is the
        one metering failure that cannot be corrected by re-running anything."""
        return sum(1 for d in self.discrepancies if d.kind is DiscrepancyKind.DUPLICATE_USAGE)

    def of_kind(self, kind: DiscrepancyKind) -> tuple[UsageDiscrepancy, ...]:
        return tuple(d for d in self.discrepancies if d.kind is kind)


# ── replay ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """What a deterministic replay of a period produced, and whether it matched.

    `matches_seal` is the guarantee made checkable: replaying a sealed period recomputes the
    seal digest and compares. Equal digests prove the commercial outcome is identical; unequal
    digests are a financial-integrity incident, and the point of the whole design is that such an
    incident is *detectable* rather than silent."""

    org_id: str
    account_id: str
    period: BillingPeriod
    at: float = 0.0
    events_replayed: int = 0
    usage_produced: int = 0
    duplicates_skipped: int = 0
    quarantined: int = 0
    replay_digest: str = ""
    seal_digest: str = ""
    usage: tuple[CommercialUsage, ...] = ()

    @property
    def matches_seal(self) -> bool:
        """`True` when this replay reproduced exactly what the period sealed."""
        return bool(self.seal_digest) and self.replay_digest == self.seal_digest

    @property
    def is_deterministic_against(self) -> bool:
        return self.matches_seal


# ── history ─────────────────────────────────────────────────────────────────────


class MeteringAction(StrEnum):
    """What happened in the metering domain. The activity log's closed vocabulary."""

    METER_DEFINED = "metering.meter_defined"
    METER_VERSIONED = "metering.meter_versioned"
    METER_ACTIVATED = "metering.meter_activated"
    METER_DEPRECATED = "metering.meter_deprecated"
    METER_REINSTATED = "metering.meter_reinstated"
    METER_RETIRED = "metering.meter_retired"
    USAGE_RECORDED = "metering.usage_recorded"
    USAGE_DUPLICATE_IGNORED = "metering.usage_duplicate_ignored"
    USAGE_ADJUSTED = "metering.usage_adjusted"
    USAGE_CORRECTED = "metering.usage_corrected"
    EVENT_QUARANTINED = "metering.event_quarantined"
    QUARANTINE_RESOLVED = "metering.quarantine_resolved"
    QUARANTINE_DISCARDED = "metering.quarantine_discarded"
    PERIOD_OPENED = "metering.period_opened"
    PERIOD_CLOSING = "metering.period_closing"
    PERIOD_SEALED = "metering.period_sealed"
    AGGREGATES_REBUILT = "metering.aggregates_rebuilt"
    PERIOD_REPLAYED = "metering.period_replayed"
    PERIOD_RECONCILED = "metering.period_reconciled"


@dataclass(frozen=True, slots=True)
class MeteringActivity:
    """One append-only entry in the metering history: who changed what, when.

    Kept alongside the `billing.*` fact stream rather than instead of it — the stream is the
    platform-wide log; this is the metering domain's ordered, queryable timeline. A duplicate
    ingestion appears here too: "we saw this event again and did nothing" is itself a fact worth
    keeping, and it is the evidence that idempotency worked."""

    id: str
    org_id: str
    action: MeteringAction
    actor_id: str = ""
    at: float = 0.0
    account_id: str = ""
    meter_code: str = ""
    event_id: str = ""
    usage_id: str = ""
    period_start: float | None = None
    period_end: float | None = None
    detail: str = ""


# ── search + export ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class UsageSearchQuery:
    """A deterministic filter over commercial usage. No relevance scoring and no index — the
    same records and the same query always come back in the same order, which is what a paged
    admin screen, a reproducible test, and a finance export all need."""

    text: str = ""  # case-insensitive substring over meter code, event id, subscription
    account_id: str = ""
    meter_codes: tuple[str, ...] = ()
    statuses: tuple[UsageStatus, ...] = ()
    scope_id: str = ""
    subscription_id: str = ""
    event_id: str = ""
    period_start: float | None = None
    period_end: float | None = None
    observed_from: float | None = None
    observed_until: float | None = None
    billable_only: bool = False
    adjustments_only: bool = False
    late_only: bool = False
    limit: int = 100
    offset: int = 0


@dataclass(frozen=True, slots=True)
class UsageSearchResult:
    usage: tuple[CommercialUsage, ...] = ()
    total: int = 0


@dataclass(frozen=True, slots=True)
class UsageExportRow:
    """One row of a finance-grade export. Flat, fully attributed, and self-describing: an export
    that needed a second lookup to be meaningful would not survive contact with an auditor."""

    usage_id: str
    account_id: str
    meter_pin: str
    units: int
    unit: str
    status: str
    period_start: float
    period_end: float
    observed_at: float
    recorded_at: float
    scope_id: str
    subscription_id: str
    event_id: str
    disposition: str
    corrects_usage_id: str
    digest: str


@dataclass(frozen=True, slots=True)
class UsageExport:
    """A deterministic export of a period's commercial usage.

    Ordered by `(meter pin, usage id)` and carrying its own digest, so two exports of an unchanged
    sealed period are byte-identical — which is what lets finance diff one month against the
    next and trust the difference."""

    org_id: str
    account_id: str
    period: BillingPeriod
    generated_at: float = 0.0
    rows: tuple[UsageExportRow, ...] = ()
    digest: str = ""
    sealed: bool = False

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def total_units(self) -> int:
        return sum(r.units for r in self.rows)
