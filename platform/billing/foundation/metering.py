"""Metering (M9.10 domain) — usage records are a *projection*, never a measurement.

ADR-0023 M1–M8, the epic's most correctness-critical rule set:

* **Billing never measures.** Measurement belongs to runtime producers (§A5); Billing
  subscribes to `metric.*` read-only and emits none of it.
* A **usage record** is a normalized, attributed, deduplicated billable unit derived from
  a source event. It is keyed by that event's identity, so at-least-once delivery yields
  exactly-once billing and reprocessing is safe.
* **Attribution is total**: every record resolves to exactly one billing account, or it is
  quarantined and alerted — never silently billed, never silently dropped.
* **Sealed periods stay sealed.** After sealing, corrections are adjustments or credit
  notes, never retroactive mutation.

Pure data + pure derivation. Nothing here reads an event stream; ingestion (M9.10) supplies
already-decoded facts through the repository/port seams.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .catalog import MeterAggregation
from .domain import BillingPeriod, Quantity, Scope


class UsageStatus(StrEnum):
    """A usage record's disposition. QUARANTINED is a first-class outcome, not an error
    path: unattributable usage must be visible, not invisible (ADR-0023 M6)."""

    RECORDED = "recorded"
    QUARANTINED = "quarantined"
    ADJUSTMENT = "adjustment"  # late arrival landed after its period sealed (M7)


@dataclass(frozen=True, slots=True)
class UsageSource:
    """The identity of the `metric.*` event a usage record was derived from.

    `event_id` is the idempotency key: re-ingesting the same event is a no-op, which is
    what turns at-least-once delivery into exactly-once billing (M4)."""

    event_id: str
    event_type: str
    observed_at: float


@dataclass(frozen=True, slots=True)
class UsageRecord:
    """One immutable billable fact. Append-only: there is no update or delete path — a
    correction is a new record with `ADJUSTMENT` status referencing the original."""

    id: str
    org_id: str
    account_id: str
    meter_code: str
    quantity: Quantity
    scope: Scope
    period: BillingPeriod
    source: UsageSource
    status: UsageStatus = UsageStatus.RECORDED
    subscription_id: str = ""
    corrects_record_id: str = ""
    quarantine_reason: str = ""

    @property
    def is_billable(self) -> bool:
        return self.status in (UsageStatus.RECORDED, UsageStatus.ADJUSTMENT)

    @property
    def idempotency_key(self) -> tuple[str, str]:
        """`(source event id, meter)` — the pair that makes ingestion idempotent."""
        return (self.source.event_id, self.meter_code)


@dataclass(frozen=True, slots=True)
class UsageAggregate:
    """A period total per meter per scope — the input to rating and to quota checks.

    Maintained incrementally through the period rather than computed at period end
    (Blueprint §24), so period-close work stays small and the number a customer sees
    mid-period is the number they are billed on."""

    id: str
    org_id: str
    account_id: str
    meter_code: str
    scope: Scope
    period: BillingPeriod
    aggregation: MeterAggregation
    quantity: Quantity
    record_count: int = 0
    sealed: bool = False

    @property
    def is_sealed(self) -> bool:
        return self.sealed


@dataclass(frozen=True, slots=True)
class UsageQuarantine:
    """An unattributable usage signal, retained with its reason so it can be alerted on
    and resolved — the visible half of "total attribution" (M6)."""

    id: str
    source: UsageSource
    meter_code: str
    reason: str
    received_at: float
    org_id: str = ""  # may be unknown — that is precisely why it is quarantined


def fold(aggregation: MeterAggregation, quantities: tuple[Quantity, ...]) -> Quantity:
    """Reduce quantities under a declared aggregation. Pure and deterministic: identical
    input always yields an identical result, which is what makes a period re-derivable
    from the event log (ADR-0023 RS2).

    TIME_WEIGHTED and UNIQUE_COUNT fold to SUM here because their weighting/deduplication
    happens during normalization, where the source signal still carries the dimension the
    weighting needs. Doing it twice would double-count.
    """
    if not quantities:
        raise ValueError("cannot fold an empty quantity set")
    unit = quantities[0].unit
    if any(q.unit != unit for q in quantities):
        raise ValueError("cannot fold quantities with mixed units")
    if aggregation is MeterAggregation.MAX:
        return Quantity(max(q.units for q in quantities), unit)
    return Quantity(sum(q.units for q in quantities), unit)
