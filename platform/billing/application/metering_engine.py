"""Metering Engine application layer (M9.11) — commands, queries, use cases, mapping.

Additive to M9.3/M9.6–M9.10: the buses, Unit of Work, authorization orchestrator,
application-event vocabulary, and dispatch discipline are reused exactly as they are. This module
adds 17 commands, 12 queries, their use cases, and the view DTOs — and nothing else.

The four beats are unchanged: **authorize → open a transaction → coordinate the Foundation →
stage an event.** Every metering rule — what an event becomes, how a raw signal normalizes, where
a late event lands, how a window folds, when a period may seal, whether a replay matched — is
Foundation behaviour these methods *call*. There is no rule here, and a test greps for the ones
that would be tempting to inline.

Names differ from the frozen M9.3 surface where they must: M9.3 owns `RecordUsage`,
`QuarantineUsage`, and `GetUsageSummary`, which write the minimal M9.2 usage records. Everything
here is named distinctly — the same call M9.6 made for `CreateCatalogPlan` and M9.10 for
`GrantCommercialEntitlement`.

Three correctness properties this layer is responsible for, stated because they are easy to break
by accident:

* **One transaction per source event.** Ingestion writes the usage record, the activity entry, and
  the staged application event inside one Unit of Work. A partial commit would be a usage record
  with no history, or history claiming a record that does not exist.
* **A duplicate is a success.** It commits (the activity entry is a fact worth keeping), emits
  `usage.duplicate_ignored`, and returns the *existing* record. It is never an error, because
  at-least-once delivery makes replays normal.
* **Nothing here measures, polls, or reaches into another plane.** Signals arrive through
  `MetricStreamPort`, which the platform implements outside this plane.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from billing.foundation import BillingNotFound as _NotFound
from billing.foundation import (
    BillingPeriod,
    CommercialMeter,
    CommercialUsage,
    IngestionOutcome,
    MeterAggregation,
    MeteringAction,
    MeterSource,
    MeterState,
    MeterTrigger,
    MetricSignal,
    Normalization,
    PeriodState,
    PeriodTrigger,
    QuarantineEntry,
    QuarantineReason,
    QuarantineTrigger,
    SourceEvent,
    UsagePeriod,
    UsageSearchQuery,
    WindowGranularity,
)

from .authorization import AuthorizationOrchestrator
from .authorization import BillingPermission as P
from .commands import _Command
from .events import PREFIX_APP_EVENT, ApplicationEvent, ApplicationEventType
from .queries import _Query
from .unit_of_work import UnitOfWorkFactory

# ── commands ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DefineCommercialMeter(_Command):
    """Declare what is counted, by whom, in what unit, and how late an event may be."""

    code: str = ""
    unit: str = "unit"
    display_name: str = ""
    aggregation: MeterAggregation = MeterAggregation.SUM
    source: MeterSource = MeterSource.RUNTIME_SESSION
    normalization: Normalization = field(default_factory=Normalization)
    late_arrival_window_seconds: float = 172_800.0
    accepted_event_types: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True, slots=True)
class VersionCommercialMeter(_Command):
    """Amend a meter by publishing a **new version** of it. Never an in-place edit."""

    code: str = ""
    unit: str | None = None
    display_name: str | None = None
    aggregation: MeterAggregation | None = None
    normalization: Normalization | None = None
    late_arrival_window_seconds: float | None = None
    accepted_event_types: tuple[str, ...] | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class ActivateCommercialMeter(_Command):
    code: str = ""


@dataclass(frozen=True, slots=True)
class DeprecateCommercialMeter(_Command):
    """A deprecated meter stops being offered; it does not stop counting what customers already
    send."""

    code: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ReinstateCommercialMeter(_Command):
    code: str = ""


@dataclass(frozen=True, slots=True)
class RetireCommercialMeter(_Command):
    code: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class OpenUsagePeriod(_Command):
    account_id: str = ""
    period_start: float = 0.0
    period_end: float = 0.0


@dataclass(frozen=True, slots=True)
class BeginUsagePeriodClose(_Command):
    account_id: str = ""
    period_start: float = 0.0
    period_end: float = 0.0


@dataclass(frozen=True, slots=True)
class SealUsagePeriod(_Command):
    """Freeze a period. **A one-way door** — after this, corrections are adjustments in a later
    period or credit notes, never retroactive mutation."""

    account_id: str = ""
    period_start: float = 0.0
    period_end: float = 0.0


@dataclass(frozen=True, slots=True)
class IngestMetricSignal(_Command):
    """The correctness core. Idempotent on `(event_id, meter_code)`."""

    signal: MetricSignal | None = None
    period_start: float = 0.0
    period_end: float = 0.0
    ingest_run_id: str = ""


@dataclass(frozen=True, slots=True)
class DrainMetricStream(_Command):
    """Pull whatever the platform has decoded and ingest it. Never polls Runtime — the port is
    the platform's own buffer, and it is safe to call from several instances at once."""

    period_start: float = 0.0
    period_end: float = 0.0
    limit: int = 500


@dataclass(frozen=True, slots=True)
class ResolveQuarantinedEvent(_Command):
    """Re-ingest a quarantined event now that its cause is fixed. Produces exactly the record it
    would have produced originally, **including the same derived id**."""

    entry_id: str = ""
    meter_code: str = ""
    account_id: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class DiscardQuarantinedEvent(_Command):
    entry_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RecordUsageCorrection(_Command):
    """Append a correction. Never edits the record it corrects."""

    usage_id: str = ""
    delta_units: int = 0
    reason: str = ""
    reason_code: str = ""
    period_start: float | None = None
    period_end: float | None = None
    sequence: int = 1


@dataclass(frozen=True, slots=True)
class RebuildUsageAggregates(_Command):
    """Recompute a period's aggregates from its usage records. Deterministic, so a rebuild after
    a restart equals the aggregate maintained continuously."""

    account_id: str = ""
    period_start: float = 0.0
    period_end: float = 0.0
    granularity: WindowGranularity = WindowGranularity.PERIOD


@dataclass(frozen=True, slots=True)
class ReplayUsagePeriod(_Command):
    """Re-derive a period from the authoritative log and compare against what it sealed."""

    account_id: str = ""
    period_start: float = 0.0
    period_end: float = 0.0


@dataclass(frozen=True, slots=True)
class ReconcileUsagePeriod(_Command):
    """Compare the event log against recorded usage. Continuous, not period-end (R-1)."""

    account_id: str = ""
    period_start: float = 0.0
    period_end: float = 0.0


ALL_METERING_COMMANDS = (
    DefineCommercialMeter,
    VersionCommercialMeter,
    ActivateCommercialMeter,
    DeprecateCommercialMeter,
    ReinstateCommercialMeter,
    RetireCommercialMeter,
    OpenUsagePeriod,
    BeginUsagePeriodClose,
    SealUsagePeriod,
    IngestMetricSignal,
    DrainMetricStream,
    ResolveQuarantinedEvent,
    DiscardQuarantinedEvent,
    RecordUsageCorrection,
    RebuildUsageAggregates,
    ReplayUsagePeriod,
    ReconcileUsagePeriod,
)


# ── queries ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetCommercialMeter(_Query):
    code: str = ""


@dataclass(frozen=True, slots=True)
class ListCommercialMeters(_Query):
    include_retired: bool = False


@dataclass(frozen=True, slots=True)
class GetCommercialUsageSnapshot(_Query):
    """Named distinctly from M9.3's `GetUsageSummary`, which reads the minimal M9.2 records."""

    account_id: str = ""
    period_start: float = 0.0
    period_end: float = 0.0


@dataclass(frozen=True, slots=True)
class GetUsagePeriodState(_Query):
    account_id: str = ""
    period_start: float = 0.0
    period_end: float = 0.0


@dataclass(frozen=True, slots=True)
class ListUsagePeriods(_Query):
    account_id: str = ""


@dataclass(frozen=True, slots=True)
class SearchCommercialUsage(_Query):
    query: UsageSearchQuery = field(default_factory=UsageSearchQuery)


@dataclass(frozen=True, slots=True)
class InspectCommercialUsage(_Query):
    """Full provenance for one billable fact: what it came from and how it became what it is."""

    usage_id: str = ""


@dataclass(frozen=True, slots=True)
class DiagnoseUsagePeriod(_Query):
    account_id: str = ""
    period_start: float = 0.0
    period_end: float = 0.0


@dataclass(frozen=True, slots=True)
class ListQuarantinedEvents(_Query):
    pending_only: bool = True
    limit: int = 100


@dataclass(frozen=True, slots=True)
class GetUsageReconciliationReport(_Query):
    account_id: str = ""
    period_start: float = 0.0
    period_end: float = 0.0


@dataclass(frozen=True, slots=True)
class ExportCommercialUsage(_Query):
    account_id: str = ""
    period_start: float = 0.0
    period_end: float = 0.0


@dataclass(frozen=True, slots=True)
class MeteringTimeline(_Query):
    account_id: str = ""
    limit: int = 100


ALL_METERING_QUERIES = (
    GetCommercialMeter,
    ListCommercialMeters,
    GetCommercialUsageSnapshot,
    GetUsagePeriodState,
    ListUsagePeriods,
    SearchCommercialUsage,
    InspectCommercialUsage,
    DiagnoseUsagePeriod,
    ListQuarantinedEvents,
    GetUsageReconciliationReport,
    ExportCommercialUsage,
    MeteringTimeline,
)


# ── view DTOs ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class NormalizationView:
    numerator: int
    denominator: int
    rounding: str
    is_identity: bool


@dataclass(frozen=True, slots=True)
class CommercialMeterView:
    id: str
    code: str
    display_name: str
    unit: str
    aggregation: str
    source: str
    normalization: NormalizationView
    late_arrival_window_seconds: float
    state: str
    version: int
    pin: str
    description: str
    accepted_event_types: tuple[str, ...]
    is_ingestible: bool
    allowed_triggers: tuple[str, ...]
    created_at: float
    updated_at: float
    activated_at: float | None
    retired_at: float | None


@dataclass(frozen=True, slots=True)
class SourceEventView:
    event_id: str
    event_type: str
    source: str
    observed_at: float
    received_at: float
    lateness: float
    producer: str
    sequence: int
    payload_digest: str


@dataclass(frozen=True, slots=True)
class ProvenanceView:
    meter_pin: str
    source_kind: str
    ingested_at: float
    ingest_run_id: str
    normalization: NormalizationView
    raw_units: int
    raw_unit: str
    disposition: str
    replayed: bool
    was_normalized: bool
    was_late: bool


@dataclass(frozen=True, slots=True)
class CommercialUsageView:
    id: str
    account_id: str
    meter_code: str
    meter_version: int
    meter_pin: str
    units: int
    unit: str
    scope_id: str
    period_start: float
    period_end: float
    status: str
    subscription_id: str
    corrects_usage_id: str
    is_billable: bool
    is_adjustment: bool
    recorded_at: float
    digest: str
    source: SourceEventView
    provenance: ProvenanceView


@dataclass(frozen=True, slots=True)
class IngestionReceiptView:
    """What ingesting one signal did. `duplicate` is a success, not an error."""

    outcome: str
    duplicate: bool
    quarantined: bool
    usage_id: str
    event_id: str
    meter_code: str
    units: int
    disposition: str
    reason: str
    detail: str


@dataclass(frozen=True, slots=True)
class DrainReceiptView:
    signals_seen: int
    recorded: int
    adjusted: int
    duplicates: int
    quarantined: int
    ingest_run_id: str


@dataclass(frozen=True, slots=True)
class UsageWindowView:
    granularity: str
    start: float
    end: float


@dataclass(frozen=True, slots=True)
class UsageAggregateView:
    id: str
    meter_code: str
    scope_id: str
    window: UsageWindowView
    aggregation: str
    units: int
    unit: str
    record_count: int
    adjustment_count: int
    source_digest: str
    sealed: bool


@dataclass(frozen=True, slots=True)
class UsageSnapshotView:
    account_id: str
    period_start: float
    period_end: float
    at: float
    state: str
    is_sealed: bool
    aggregates: tuple[UsageAggregateView, ...]
    meter_codes: tuple[str, ...]
    record_count: int
    adjustment_count: int
    quarantined_count: int
    digest: str


@dataclass(frozen=True, slots=True)
class UsagePeriodView:
    id: str
    account_id: str
    period_start: float
    period_end: float
    state: str
    record_count: int
    adjustment_count: int
    quarantined_count: int
    seal_digest: str
    allowed_triggers: tuple[str, ...]
    opened_at: float
    closing_at: float | None
    sealed_at: float | None
    sealed_by: str


@dataclass(frozen=True, slots=True)
class QuarantineEntryView:
    id: str
    event_id: str
    event_type: str
    meter_code: str
    reason: str
    detail: str
    state: str
    account_id: str
    raw_units: int
    raw_unit: str
    received_at: float
    resolved_at: float | None
    resolved_by: str
    resolution_note: str
    resulting_usage_id: str
    allowed_triggers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DiscrepancyView:
    kind: str
    event_id: str
    meter_code: str
    usage_id: str
    expected: str
    actual: str
    detail: str


@dataclass(frozen=True, slots=True)
class ReconciliationView:
    account_id: str
    period_start: float
    period_end: float
    at: float
    events_examined: int
    usage_examined: int
    is_clean: bool
    duplicate_count: int
    discrepancies: tuple[DiscrepancyView, ...]


@dataclass(frozen=True, slots=True)
class ReplayReceiptView:
    account_id: str
    period_start: float
    period_end: float
    events_replayed: int
    usage_produced: int
    duplicates_skipped: int
    quarantined: int
    replay_digest: str
    seal_digest: str
    matches_seal: bool


@dataclass(frozen=True, slots=True)
class UsageInspectionView:
    usage: CommercialUsageView
    explanation: tuple[str, ...]
    corrections: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MeteringDiagnosticView:
    path: str
    code: str
    severity: str
    message: str
    remediation: str


@dataclass(frozen=True, slots=True)
class MeteringDiagnosticsView:
    account_id: str
    period_start: float
    period_end: float
    findings: tuple[MeteringDiagnosticView, ...]
    error_count: int
    warning_count: int
    is_valid: bool


@dataclass(frozen=True, slots=True)
class MeteringActivityView:
    id: str
    action: str
    actor_id: str
    at: float
    account_id: str
    meter_code: str
    event_id: str
    usage_id: str
    period_start: float | None
    period_end: float | None
    detail: str


@dataclass(frozen=True, slots=True)
class UsageExportRowView:
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
class UsageExportView:
    account_id: str
    period_start: float
    period_end: float
    generated_at: float
    rows: tuple[UsageExportRowView, ...]
    row_count: int
    total_units: int
    digest: str
    sealed: bool


@dataclass(frozen=True, slots=True)
class UsageSearchView:
    usage: tuple[CommercialUsageView, ...]
    total: int


def _normalization_view(normalization: Normalization) -> NormalizationView:
    return NormalizationView(
        numerator=normalization.numerator,
        denominator=normalization.denominator,
        rounding=normalization.rounding.value,
        is_identity=normalization.is_identity,
    )


def meter_view(
    meter: CommercialMeter, allowed: tuple[MeterTrigger, ...] = ()
) -> CommercialMeterView:
    return CommercialMeterView(
        id=meter.id,
        code=meter.code,
        display_name=meter.display_name,
        unit=meter.unit,
        aggregation=meter.aggregation.value,
        source=meter.source.value,
        normalization=_normalization_view(meter.normalization),
        late_arrival_window_seconds=meter.late_arrival_window_seconds,
        state=meter.state.value,
        version=meter.version,
        pin=meter.pin,
        description=meter.description,
        accepted_event_types=meter.accepted_event_types,
        is_ingestible=meter.is_ingestible,
        allowed_triggers=tuple(t.value for t in allowed),
        created_at=meter.created_at,
        updated_at=meter.updated_at,
        activated_at=meter.activated_at,
        retired_at=meter.retired_at,
    )


def _source_view(event: SourceEvent) -> SourceEventView:
    return SourceEventView(
        event_id=event.event_id,
        event_type=event.event_type,
        source=event.source.value,
        observed_at=event.observed_at,
        received_at=event.received_at,
        lateness=event.lateness,
        producer=event.producer,
        sequence=event.sequence,
        payload_digest=event.payload_digest,
    )


def usage_view(usage: CommercialUsage) -> CommercialUsageView:
    p = usage.provenance
    return CommercialUsageView(
        id=usage.id,
        account_id=usage.account_id,
        meter_code=usage.meter_code,
        meter_version=usage.meter_version,
        meter_pin=usage.meter_pin,
        units=usage.quantity.units,
        unit=usage.quantity.unit,
        scope_id=usage.scope.narrowest_id,
        period_start=usage.period.start,
        period_end=usage.period.end,
        status=usage.status.value,
        subscription_id=usage.subscription_id,
        corrects_usage_id=usage.corrects_usage_id,
        is_billable=usage.is_billable,
        is_adjustment=usage.is_adjustment,
        recorded_at=usage.recorded_at,
        digest=usage.digest,
        source=_source_view(usage.source),
        provenance=ProvenanceView(
            meter_pin=p.meter_pin,
            source_kind=p.source_kind.value,
            ingested_at=p.ingested_at,
            ingest_run_id=p.ingest_run_id,
            normalization=_normalization_view(p.normalization),
            raw_units=p.raw_units,
            raw_unit=p.raw_unit,
            disposition=p.disposition.value,
            replayed=p.replayed,
            was_normalized=p.was_normalized,
            was_late=p.was_late,
        ),
    )


def period_view(period: UsagePeriod, allowed: tuple[PeriodTrigger, ...] = ()) -> UsagePeriodView:
    return UsagePeriodView(
        id=period.id,
        account_id=period.account_id,
        period_start=period.period.start,
        period_end=period.period.end,
        state=period.state.value,
        record_count=period.record_count,
        adjustment_count=period.adjustment_count,
        quarantined_count=period.quarantined_count,
        seal_digest=period.seal_digest,
        allowed_triggers=tuple(t.value for t in allowed),
        opened_at=period.opened_at,
        closing_at=period.closing_at,
        sealed_at=period.sealed_at,
        sealed_by=period.sealed_by,
    )


def quarantine_view(
    entry: QuarantineEntry, allowed: tuple[QuarantineTrigger, ...] = ()
) -> QuarantineEntryView:
    return QuarantineEntryView(
        id=entry.id,
        event_id=entry.source.event_id,
        event_type=entry.source.event_type,
        meter_code=entry.meter_code,
        reason=entry.reason.value,
        detail=entry.detail,
        state=entry.state.value,
        account_id=entry.account_id,
        raw_units=entry.raw_units,
        raw_unit=entry.raw_unit,
        received_at=entry.received_at,
        resolved_at=entry.resolved_at,
        resolved_by=entry.resolved_by,
        resolution_note=entry.resolution_note,
        resulting_usage_id=entry.resulting_usage_id,
        allowed_triggers=tuple(t.value for t in allowed),
    )


def snapshot_view(snapshot) -> UsageSnapshotView:
    return UsageSnapshotView(
        account_id=snapshot.account_id,
        period_start=snapshot.period.start,
        period_end=snapshot.period.end,
        at=snapshot.at,
        state=snapshot.state.value,
        is_sealed=snapshot.is_sealed,
        aggregates=tuple(
            UsageAggregateView(
                id=a.id,
                meter_code=a.meter_code,
                scope_id=a.scope.narrowest_id,
                window=UsageWindowView(
                    granularity=a.window.granularity.value,
                    start=a.window.start,
                    end=a.window.end,
                ),
                aggregation=a.aggregation.value,
                units=a.quantity.units,
                unit=a.quantity.unit,
                record_count=a.record_count,
                adjustment_count=a.adjustment_count,
                source_digest=a.source_digest,
                sealed=a.sealed,
            )
            for a in snapshot.aggregates
        ),
        meter_codes=snapshot.meter_codes,
        record_count=snapshot.record_count,
        adjustment_count=snapshot.adjustment_count,
        quarantined_count=snapshot.quarantined_count,
        digest=snapshot.digest,
    )


def reconciliation_view(report) -> ReconciliationView:
    return ReconciliationView(
        account_id=report.account_id,
        period_start=report.period.start,
        period_end=report.period.end,
        at=report.at,
        events_examined=report.events_examined,
        usage_examined=report.usage_examined,
        is_clean=report.is_clean,
        duplicate_count=report.duplicate_count,
        discrepancies=tuple(
            DiscrepancyView(
                kind=d.kind.value,
                event_id=d.event_id,
                meter_code=d.meter_code,
                usage_id=d.usage_id,
                expected=d.expected,
                actual=d.actual,
                detail=d.detail,
            )
            for d in report.discrepancies
        ),
    )


def replay_view(result) -> ReplayReceiptView:
    return ReplayReceiptView(
        account_id=result.account_id,
        period_start=result.period.start,
        period_end=result.period.end,
        events_replayed=result.events_replayed,
        usage_produced=result.usage_produced,
        duplicates_skipped=result.duplicates_skipped,
        quarantined=result.quarantined,
        replay_digest=result.replay_digest,
        seal_digest=result.seal_digest,
        matches_seal=result.matches_seal,
    )


def export_view(export) -> UsageExportView:
    return UsageExportView(
        account_id=export.account_id,
        period_start=export.period.start,
        period_end=export.period.end,
        generated_at=export.generated_at,
        rows=tuple(
            UsageExportRowView(
                usage_id=r.usage_id,
                account_id=r.account_id,
                meter_pin=r.meter_pin,
                units=r.units,
                unit=r.unit,
                status=r.status,
                period_start=r.period_start,
                period_end=r.period_end,
                observed_at=r.observed_at,
                recorded_at=r.recorded_at,
                scope_id=r.scope_id,
                subscription_id=r.subscription_id,
                event_id=r.event_id,
                disposition=r.disposition,
                corrects_usage_id=r.corrects_usage_id,
                digest=r.digest,
            )
            for r in export.rows
        ),
        row_count=export.row_count,
        total_units=export.total_units,
        digest=export.digest,
        sealed=export.sealed,
    )


def activity_view(activity) -> MeteringActivityView:
    return MeteringActivityView(
        id=activity.id,
        action=activity.action.value,
        actor_id=activity.actor_id,
        at=activity.at,
        account_id=activity.account_id,
        meter_code=activity.meter_code,
        event_id=activity.event_id,
        usage_id=activity.usage_id,
        period_start=activity.period_start,
        period_end=activity.period_end,
        detail=activity.detail,
    )


def diagnostics_view(
    account_id: str, period: BillingPeriod, diagnostics
) -> MeteringDiagnosticsView:
    return MeteringDiagnosticsView(
        account_id=account_id,
        period_start=period.start,
        period_end=period.end,
        findings=tuple(
            MeteringDiagnosticView(
                path=f.path,
                code=f.code.value,
                severity=f.severity.value,
                message=f.message,
                remediation=f.remediation,
            )
            for f in diagnostics.findings
        ),
        error_count=len(diagnostics.errors),
        warning_count=len(diagnostics.warnings),
        is_valid=diagnostics.is_valid,
    )


# ── use cases ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MeteringRepositories:
    """The metering seams, injected. Interfaces only — implementations in persistence."""

    meters: object
    usage: object
    periods: object
    aggregates: object
    quarantine: object
    corrections: object
    activity: object


#: Which `billing.*` fact each meter lifecycle move publishes, and which activity action records
#: it. A table so "exactly one event per business fact" is checkable rather than asserted in prose.
def _meter_transitions() -> dict:
    from billing.foundation import BillingEventType as E

    return {
        MeterTrigger.ACTIVATE: (E.METER_ACTIVATED, MeteringAction.METER_ACTIVATED),
        MeterTrigger.DEPRECATE: (E.METER_DEPRECATED, MeteringAction.METER_DEPRECATED),
        MeterTrigger.REINSTATE: (E.METER_REINSTATED, MeteringAction.METER_REINSTATED),
        MeterTrigger.RETIRE: (E.METER_RETIRED, MeteringAction.METER_RETIRED),
    }


_METER_TRANSITIONS = _meter_transitions()


class MeteringCommandUseCases:
    """Orchestration for the Metering Engine. Coordinates; decides nothing.

    Not one method here normalizes a quantity, decides where a late event lands, folds a window,
    or computes a digest. Those are `UsageNormalizer`, `LateArrivalPolicy`, `UsageAggregator`, and
    `UsageSealingService` — and a test greps this module to keep it that way."""

    def __init__(
        self,
        *,
        context,
        metering_repos: MeteringRepositories,
        factory,
        meter_lifecycle,
        period_lifecycle,
        quarantine_lifecycle,
        ingestion,
        late_policy,
        aggregator,
        sealing,
        replay,
        reconciliation,
        corrections,
        stream,
        authz: AuthorizationOrchestrator,
        uow_factory: UnitOfWorkFactory,
        ids,
        clock,
    ) -> None:
        self._ctx = context
        self._repos = metering_repos
        self._factory = factory
        self._meter_lifecycle = meter_lifecycle
        self._period_lifecycle = period_lifecycle
        self._quarantine_lifecycle = quarantine_lifecycle
        self._ingestion = ingestion
        self._late_policy = late_policy
        self._aggregator = aggregator
        self._sealing = sealing
        self._replay = replay
        self._reconciliation = reconciliation
        self._corrections = corrections
        self._stream = stream
        self._authz = authz
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock

    # ── meters ──────────────────────────────────────────────────────────────────

    def define_meter(self, command: DefineCommercialMeter) -> CommercialMeter:
        """Declaring what is counted is a platform act, so it takes the catalog permission."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            meter = self._factory.define_meter(
                org_id=command.org_id,
                code=command.code,
                unit=command.unit,
                display_name=command.display_name,
                aggregation=command.aggregation,
                source=command.source,
                normalization=command.normalization,
                late_arrival_window_seconds=command.late_arrival_window_seconds,
                accepted_event_types=command.accepted_event_types,
                description=command.description,
                existing=self._repos.meters.all(command.org_id),
                actor_id=command.actor_id,
            )
            stored = self._repos.meters.add(meter)
            self._record(
                command,
                MeteringAction.METER_DEFINED,
                meter_code=stored.code,
                detail=stored.pin,
            )
            uow.stage(self._event(command, stored.id, code=stored.code, unit=stored.unit))
            return stored

    def version_meter(self, command: VersionCommercialMeter) -> CommercialMeter:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            meter = self._meter(command.org_id, command.code)
            stored = self._repos.meters.replace(
                self._factory.version_meter(
                    meter,
                    actor_id=command.actor_id,
                    existing=self._repos.meters.all(command.org_id),
                    unit=command.unit,
                    display_name=command.display_name,
                    aggregation=command.aggregation,
                    normalization=command.normalization,
                    late_arrival_window_seconds=command.late_arrival_window_seconds,
                    accepted_event_types=command.accepted_event_types,
                    description=command.description,
                )
            )
            self._record(
                command,
                MeteringAction.METER_VERSIONED,
                meter_code=stored.code,
                detail=stored.pin,
            )
            uow.stage(self._event(command, stored.id, code=stored.code, version=stored.version))
            return stored

    def activate_meter(self, command: ActivateCommercialMeter) -> CommercialMeter:
        return self._meter_transition(command, MeterTrigger.ACTIVATE)

    def deprecate_meter(self, command: DeprecateCommercialMeter) -> CommercialMeter:
        return self._meter_transition(command, MeterTrigger.DEPRECATE, detail=command.reason)

    def reinstate_meter(self, command: ReinstateCommercialMeter) -> CommercialMeter:
        return self._meter_transition(command, MeterTrigger.REINSTATE)

    def retire_meter(self, command: RetireCommercialMeter) -> CommercialMeter:
        return self._meter_transition(command, MeterTrigger.RETIRE, detail=command.reason)

    # ── periods ─────────────────────────────────────────────────────────────────

    def open_period(self, command: OpenUsagePeriod) -> UsagePeriod:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            period = BillingPeriod(command.period_start, command.period_end)
            existing = self._repos.periods.for_account(
                command.org_id, command.account_id, period.start, period.end
            )
            if existing is not None:
                # Idempotent: opening a period twice is a retry, not a second period. Two period
                # rows for one window would split a customer's usage across both.
                return existing
            stored = self._repos.periods.add(
                self._factory.open_period(
                    org_id=command.org_id,
                    account_id=command.account_id,
                    period=period,
                    actor_id=command.actor_id,
                )
            )
            self._record(
                command, MeteringAction.PERIOD_OPENED, account_id=command.account_id, period=period
            )
            uow.stage(self._event(command, stored.id, account_id=command.account_id))
            return stored

    def begin_close(self, command: BeginUsagePeriodClose) -> UsagePeriod:
        return self._period_transition(command, PeriodTrigger.BEGIN_CLOSE)

    def seal_period(self, command: SealUsagePeriod) -> UsagePeriod:
        """Freeze a period, recording the digest of exactly what it sealed.

        The digest is what makes a later replay a *proof* rather than an assertion, so it is
        computed by the Foundation from the stored usage rather than passed in by a caller."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            period = self._period(command)
            usage = self._usage_for(command.org_id, command.account_id, period.period)
            digest = self._sealing.seal_digest(usage)
            moved = self._period_lifecycle.apply(
                replace(
                    period,
                    record_count=len([u for u in usage if u.is_billable]),
                    adjustment_count=len([u for u in usage if u.is_adjustment]),
                ),
                PeriodTrigger.SEAL,
                at=self._clock.now(),
                seal_digest=digest,
                sealed_by=command.actor_id,
            )
            stored = self._repos.periods.replace(moved)
            self._factory.emit_period_transition(
                stored,
                event_type=_period_sealed_event(),
                actor_id=command.actor_id,
                account_id=stored.account_id,
                records=stored.record_count,
                digest=digest,
            )
            self._record(
                command,
                MeteringAction.PERIOD_SEALED,
                account_id=stored.account_id,
                period=stored.period,
                detail=digest,
            )
            uow.stage(self._event(command, stored.id, digest=digest, records=stored.record_count))
            return stored

    # ── ingestion ───────────────────────────────────────────────────────────────

    def ingest(self, command: IngestMetricSignal) -> IngestionReceiptView:
        """The correctness core: one source event → at most one commercial usage record.

        Ingestion takes the *usage ingest* permission rather than `BILLING_MANAGE`, because the
        caller is a system actor on a pipeline, not a human changing a commercial term."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_USAGE_INGEST)
        if command.signal is None:
            raise _NotFound("no metric signal supplied")
        with self._uow.new() as uow:
            receipt = self._ingest_one(
                command,
                command.signal,
                period=BillingPeriod(command.period_start, command.period_end),
                run_id=command.ingest_run_id or self._factory.new_run_id(),
                uow=uow,
            )
            return receipt

    def drain(self, command: DrainMetricStream) -> DrainReceiptView:
        """Ingest everything the platform has decoded. Safe to run on several instances at once:
        every signal goes through the same idempotent path, so overlapping drains converge."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_USAGE_INGEST)
        period = BillingPeriod(command.period_start, command.period_end)
        run_id = self._factory.new_run_id()
        signals = self._stream.drain(command.org_id, command.limit)
        counts = {"recorded": 0, "adjusted": 0, "duplicate": 0, "quarantined": 0}
        for signal in signals:
            with self._uow.new() as uow:
                receipt = self._ingest_one(command, signal, period=period, run_id=run_id, uow=uow)
                counts[receipt.outcome] = counts.get(receipt.outcome, 0) + 1
        return DrainReceiptView(
            signals_seen=len(signals),
            recorded=counts["recorded"],
            adjusted=counts["adjusted"],
            duplicates=counts["duplicate"],
            quarantined=counts["quarantined"],
            ingest_run_id=run_id,
        )

    def _ingest_one(
        self, command, signal: MetricSignal, *, period: BillingPeriod, run_id: str, uow
    ) -> IngestionReceiptView:
        """One signal, one transaction. Every branch below commits *something* — a usage record,
        a quarantine entry, or a duplicate-ignored fact — because "we saw this and did nothing at
        all" is the one outcome that would make the pipeline unauditable."""
        now = self._clock.now()
        event = self._factory.source_event(signal)
        meter = self._meter_for(command.org_id, event)
        accounts = self._accounts_by_scope(command.org_id)

        # The billing account must be resolved *before* deciding, because the period's sealing
        # state is an input to the decision and a period belongs to an account. Resolved with the
        # ingestion service's own `AttributionService`, so this cannot disagree with the answer
        # the decision reaches a moment later.
        #
        # Defect found at M9.11 and fixed here: an earlier draft looked the period up by the
        # event's *scope* id rather than the resolved account, so no period row was ever found
        # and `period_state` silently defaulted to OPEN — which let usage land in a **sealed**
        # period. That is the exact failure ADR-0023 M7 exists to prevent.
        attribution = self._ingestion.attribution.attribute(
            signal.scope,
            accounts_by_scope=accounts,
            subscriptions_by_account=self._subscriptions_by_account(command.org_id),
        )
        period_row = (
            self._repos.periods.for_account(
                command.org_id, attribution.account_id, period.start, period.end
            )
            if attribution.is_resolved
            else None
        )
        # Where an adjustment would land: the account's latest still-open period, which is
        # never the sealed one it is adjusting.
        landing = (
            self._open_period_for(command.org_id, attribution.account_id)
            if attribution.is_resolved
            else None
        )
        decision = self._ingestion.decide(
            event,
            org_id=command.org_id,
            meter=meter,
            raw_units=signal.raw_units,
            raw_unit=signal.raw_unit,
            scope=signal.scope,
            period=period,
            period_state=period_row.state if period_row else PeriodState.OPEN,
            current_period=landing,
            accounts_by_scope=accounts,
            subscriptions_by_account=self._subscriptions_by_account(command.org_id),
            already_recorded=(
                meter is not None
                and self._repos.usage.exists(command.org_id, event.event_id, meter.code)
            ),
            ingested_at=now,
            ingest_run_id=run_id,
        )

        if decision.is_duplicate:
            self._factory.emit_duplicate_ignored(
                org_id=command.org_id,
                event_id=event.event_id,
                meter_code=meter.code if meter else "",
                actor_id=command.actor_id,
            )
            self._record(
                command,
                MeteringAction.USAGE_DUPLICATE_IGNORED,
                meter_code=meter.code if meter else "",
                event_id=event.event_id,
                detail=decision.detail,
            )
            uow.stage(self._event(command, event.event_id, duplicate=True))
            return IngestionReceiptView(
                outcome="duplicate",
                duplicate=True,
                quarantined=False,
                usage_id="",
                event_id=event.event_id,
                meter_code=meter.code if meter else "",
                units=0,
                disposition=decision.disposition.value,
                reason="",
                detail=decision.detail,
            )

        # An adjustment with nowhere open to land is quarantined rather than written back into
        # the sealed period it is adjusting. Visible, re-ingestible, and never a broken seal.
        if (
            decision.is_recorded
            and self._late_policy.requires_open_period(decision.disposition)
            and decision.usage is not None
            and decision.usage.period == period
            and period_row is not None
            and period_row.is_sealed
        ):
            decision = replace(
                decision,
                outcome=IngestionOutcome.QUARANTINED,
                usage=None,
                reason=QuarantineReason.NO_OPEN_PERIOD,
                detail=(
                    "the event's period is sealed and no open period exists to receive the "
                    "adjustment; it is held until one does"
                ),
            )

        if decision.is_quarantined:
            entry = self._repos.quarantine.add(
                self._factory.quarantine(
                    org_id=command.org_id,
                    event=event,
                    meter_code=meter.code if meter else "",
                    reason=decision.reason or QuarantineReason.MALFORMED_EVENT,
                    detail=decision.detail,
                    scope=signal.scope,
                    raw_units=signal.raw_units,
                    raw_unit=signal.raw_unit,
                    actor_id=command.actor_id,
                )
            )
            self._record(
                command,
                MeteringAction.EVENT_QUARANTINED,
                meter_code=entry.meter_code,
                event_id=event.event_id,
                detail=entry.reason.value,
            )
            uow.stage(self._event(command, entry.id, reason=entry.reason.value))
            return IngestionReceiptView(
                outcome="quarantined",
                duplicate=False,
                quarantined=True,
                usage_id="",
                event_id=event.event_id,
                meter_code=entry.meter_code,
                units=0,
                disposition=decision.disposition.value,
                reason=entry.reason.value,
                detail=entry.detail,
            )

        usage = decision.usage
        assert usage is not None  # noqa: S101 — the decision type guarantees it
        stored = self._repos.usage.append(usage)
        self._factory.emit_usage_recorded(stored, actor_id=command.actor_id)
        self._record(
            command,
            MeteringAction.USAGE_ADJUSTED
            if stored.is_adjustment
            else MeteringAction.USAGE_RECORDED,
            account_id=stored.account_id,
            meter_code=stored.meter_code,
            event_id=event.event_id,
            usage_id=stored.id,
            period=stored.period,
            detail=stored.provenance.disposition.value,
        )
        uow.stage(self._event(command, stored.id, units=stored.quantity.units))
        return IngestionReceiptView(
            outcome="adjusted" if stored.is_adjustment else "recorded",
            duplicate=False,
            quarantined=False,
            usage_id=stored.id,
            event_id=event.event_id,
            meter_code=stored.meter_code,
            units=stored.quantity.units,
            disposition=stored.provenance.disposition.value,
            reason="",
            detail="",
        )

    # ── quarantine ──────────────────────────────────────────────────────────────

    def resolve_quarantine(self, command: ResolveQuarantinedEvent) -> QuarantineEntry:
        """Re-ingest a parked event now that its cause is fixed.

        Because the usage id is derived from `(org, event, meter)`, resolving produces exactly
        the record the original ingestion would have — so resolving twice is idempotent for the
        same reason ingestion is."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_USAGE_INGEST)
        with self._uow.new() as uow:
            entry = self._quarantine_entry(command.org_id, command.entry_id)
            meter_code = command.meter_code or entry.meter_code
            meter = self._meter(command.org_id, meter_code)
            usage_id = ""
            if entry.scope is not None or command.account_id:
                decision = self._ingestion.decide(
                    entry.source,
                    org_id=command.org_id,
                    meter=meter,
                    raw_units=entry.raw_units,
                    raw_unit=entry.raw_unit,
                    scope=entry.scope,
                    period=BillingPeriod(entry.source.observed_at, entry.source.observed_at + 1.0),
                    accounts_by_scope=self._resolution_scope(entry, command.account_id),
                    already_recorded=self._repos.usage.exists(
                        command.org_id, entry.source.event_id, meter_code
                    ),
                    ingested_at=self._clock.now(),
                    ingest_run_id=self._factory.new_run_id(),
                )
                if decision.usage is not None:
                    usage_id = self._repos.usage.append(decision.usage).id
                    self._factory.emit_usage_recorded(decision.usage, actor_id=command.actor_id)
            moved = self._quarantine_lifecycle.apply(
                entry,
                QuarantineTrigger.RESOLVE,
                at=self._clock.now(),
                actor_id=command.actor_id,
                note=command.note,
                resulting_usage_id=usage_id,
            )
            stored = self._repos.quarantine.replace(moved)
            self._factory.emit_quarantine_transition(
                stored,
                event_type=_quarantine_resolved_event(),
                actor_id=command.actor_id,
                usage_id=usage_id,
            )
            self._record(
                command,
                MeteringAction.QUARANTINE_RESOLVED,
                meter_code=meter_code,
                event_id=stored.source.event_id,
                usage_id=usage_id,
                detail=command.note,
            )
            uow.stage(self._event(command, stored.id, usage_id=usage_id))
            return stored

    def discard_quarantine(self, command: DiscardQuarantinedEvent) -> QuarantineEntry:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            entry = self._quarantine_entry(command.org_id, command.entry_id)
            stored = self._repos.quarantine.replace(
                self._quarantine_lifecycle.apply(
                    entry,
                    QuarantineTrigger.DISCARD,
                    at=self._clock.now(),
                    actor_id=command.actor_id,
                    note=command.reason,
                )
            )
            self._factory.emit_quarantine_transition(
                stored,
                event_type=_quarantine_discarded_event(),
                actor_id=command.actor_id,
                reason=command.reason,
            )
            self._record(
                command,
                MeteringAction.QUARANTINE_DISCARDED,
                event_id=stored.source.event_id,
                detail=command.reason,
            )
            uow.stage(self._event(command, stored.id, reason=command.reason))
            return stored

    # ── corrections ─────────────────────────────────────────────────────────────

    def correct(self, command: RecordUsageCorrection):
        """Append a correction and the adjustment record expressing it. Never edits anything."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            original = self._repos.usage.get(command.org_id, command.usage_id)
            if original is None:
                raise _NotFound(f"usage {command.usage_id!r} not found")
            landing = (
                BillingPeriod(command.period_start, command.period_end)
                if command.period_start is not None and command.period_end is not None
                else None
            )
            correction = self._factory.correction(
                org_id=command.org_id,
                corrects_usage_id=original.id,
                kind=self._corrections.kind_for(
                    command.delta_units, period_changed=landing is not None
                ),
                delta_units=command.delta_units,
                unit=original.quantity.unit,
                reason=command.reason,
                reason_code=command.reason_code,
                period=landing,
                sequence=command.sequence,
                actor_id=command.actor_id,
            )
            stored_correction = self._repos.corrections.append(correction)
            adjustment = self._corrections.apply(
                original,
                correction,
                usage_id=f"{original.id}:c{command.sequence}",
                at=self._clock.now(),
                period=landing,
            )
            stored_usage = self._repos.usage.append(adjustment)
            self._factory.emit_usage_recorded(stored_usage, actor_id=command.actor_id)
            self._record(
                command,
                MeteringAction.USAGE_CORRECTED,
                account_id=original.account_id,
                meter_code=original.meter_code,
                usage_id=stored_usage.id,
                detail=f"{correction.kind.value} {command.delta_units}",
            )
            uow.stage(
                self._event(
                    command, stored_correction.id, corrects=original.id, delta=command.delta_units
                )
            )
            return stored_usage

    # ── aggregates, replay, reconciliation ──────────────────────────────────────

    def rebuild_aggregates(self, command: RebuildUsageAggregates) -> tuple:
        """Recompute a period's aggregates from its records. Deterministic, so a rebuild after a
        restart equals what was maintained continuously."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            period = BillingPeriod(command.period_start, command.period_end)
            usage = self._usage_for(command.org_id, command.account_id, period)
            period_row = self._repos.periods.for_account(
                command.org_id, command.account_id, period.start, period.end
            )
            built = []
            for meter_code in sorted({u.meter_code for u in usage}):
                meter = self._repos.meters.by_code(command.org_id, meter_code)
                if meter is None:
                    continue
                for window in self._windows(usage, meter_code, command.granularity, period):
                    scope = next(u.scope for u in usage if u.meter_code == meter_code)
                    aggregate = self._aggregator.aggregate(
                        usage,
                        aggregate_id=self._factory.aggregate_id(
                            account_id=command.account_id,
                            meter_code=meter_code,
                            window=window,
                        ),
                        org_id=command.org_id,
                        account_id=command.account_id,
                        meter_code=meter_code,
                        scope=scope,
                        period=period,
                        window=window,
                        aggregation=meter.aggregation,
                        unit=meter.unit,
                        at=self._clock.now(),
                        sealed=bool(period_row and period_row.is_sealed),
                    )
                    built.append(self._repos.aggregates.upsert(aggregate))
            self._factory.emit_aggregates_rebuilt(
                org_id=command.org_id,
                account_id=command.account_id,
                period=period,
                count=len(built),
                actor_id=command.actor_id,
            )
            self._record(
                command,
                MeteringAction.AGGREGATES_REBUILT,
                account_id=command.account_id,
                period=period,
                detail=str(len(built)),
            )
            uow.stage(self._event(command, command.account_id, aggregates=len(built)))
            return tuple(built)

    def replay_period(self, command: ReplayUsagePeriod) -> ReplayReceiptView:
        """Re-derive a period from the authoritative log and compare against its seal.

        The replay uses the **same pure ingestion decision** the live path uses — not a second
        implementation that could drift from it. A mismatch against the seal digest is a
        financial-integrity incident, and this is what makes it detectable."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            period = BillingPeriod(command.period_start, command.period_end)
            period_row = self._repos.periods.for_account(
                command.org_id, command.account_id, period.start, period.end
            )
            signals = self._stream.replay(command.org_id, period.start, period.end)
            events = tuple(
                (
                    self._factory.source_event(s),
                    s.raw_units,
                    s.raw_unit,
                    s.scope,
                )
                for s in signals
            )
            meters = {m.code: m for m in self._repos.meters.all(command.org_id)}
            result = self._replay.replay(
                events,
                org_id=command.org_id,
                account_id=command.account_id,
                period=period,
                meters_by_code=meters,
                meter_for_event=self._match_meter,
                accounts_by_scope=self._accounts_by_scope(command.org_id),
                existing=self._usage_for(command.org_id, command.account_id, period),
                period_state=period_row.state if period_row else PeriodState.OPEN,
                seal_digest=period_row.seal_digest if period_row else "",
                at=self._clock.now(),
                ingest_run_id=self._factory.new_run_id(),
            )
            self._factory.emit_replayed(
                org_id=command.org_id,
                account_id=command.account_id,
                period=period,
                produced=result.usage_produced,
                duplicates=result.duplicates_skipped,
                matched=result.matches_seal,
                actor_id=command.actor_id,
            )
            self._record(
                command,
                MeteringAction.PERIOD_REPLAYED,
                account_id=command.account_id,
                period=period,
                detail=f"produced={result.usage_produced} matched={result.matches_seal}",
            )
            uow.stage(self._event(command, command.account_id, matched=result.matches_seal))
            return replay_view(result)

    def reconcile(self, command: ReconcileUsagePeriod) -> ReconciliationView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            period = BillingPeriod(command.period_start, command.period_end)
            report = self._reconcile_report(command.org_id, command.account_id, period)
            self._factory.emit_reconciled(
                org_id=command.org_id,
                account_id=command.account_id,
                period=period,
                discrepancies=len(report.discrepancies),
                actor_id=command.actor_id,
            )
            self._record(
                command,
                MeteringAction.PERIOD_RECONCILED,
                account_id=command.account_id,
                period=period,
                detail=str(len(report.discrepancies)),
            )
            uow.stage(
                self._event(command, command.account_id, discrepancies=len(report.discrepancies))
            )
            return reconciliation_view(report)

    # ── internals ───────────────────────────────────────────────────────────────

    def _meter_transition(self, command, trigger: MeterTrigger, *, detail: str = ""):
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            meter = self._meter(command.org_id, command.code)
            if trigger in (MeterTrigger.ACTIVATE, MeterTrigger.REINSTATE):
                # Activation is the moment a meter's claim on a signal becomes real, so it is the
                # moment the claim must be checked — two drafts created before either was
                # activated cannot both go live.
                self._meter_lifecycle.guard_no_conflict(
                    meter, self._repos.meters.all(command.org_id)
                )
            moved = self._meter_lifecycle.apply(meter, trigger, at=self._clock.now())
            stored = self._repos.meters.replace(moved)
            event_type, action = _METER_TRANSITIONS[trigger]
            self._factory.emit_meter_transition(
                stored, event_type=event_type, actor_id=command.actor_id, state=stored.state.value
            )
            self._record(command, action, meter_code=stored.code, detail=detail)
            uow.stage(self._event(command, stored.id, state=stored.state.value))
            return stored

    def _period_transition(self, command, trigger: PeriodTrigger):
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            period = self._period(command)
            moved = self._period_lifecycle.apply(period, trigger, at=self._clock.now())
            stored = self._repos.periods.replace(moved)
            self._factory.emit_period_transition(
                stored,
                event_type=_period_closing_event(),
                actor_id=command.actor_id,
                account_id=stored.account_id,
            )
            self._record(
                command,
                MeteringAction.PERIOD_CLOSING,
                account_id=stored.account_id,
                period=stored.period,
            )
            uow.stage(self._event(command, stored.id, state=stored.state.value))
            return stored

    def _reconcile_report(self, org_id: str, account_id: str, period: BillingPeriod):
        recorded = self._usage_for(org_id, account_id, period)
        meters = {m.code: m for m in self._repos.meters.all(org_id)}
        expected = []
        for signal in self._stream.replay(org_id, period.start, period.end):
            meter = self._match_meter(self._factory.source_event(signal), meters)
            if meter is None:
                continue
            expected.append(
                (
                    signal.event_id,
                    meter.code,
                    self._ingestion.normalizer.normalize(signal.raw_units, meter.normalization),
                )
            )
        return self._reconciliation.reconcile(
            org_id=org_id,
            account_id=account_id,
            period=period,
            expected=tuple(expected),
            recorded=recorded,
            at=self._clock.now(),
        )

    def _windows(self, usage, meter_code: str, granularity: WindowGranularity, period):
        """Which windows to build. PERIOD is one; HOUR and DAY are the distinct windows the
        period's own records actually fall into — building empty ones would store rows nobody
        asked for and make a rebuild non-idempotent in size."""
        if granularity is WindowGranularity.PERIOD:
            return [self._aggregator.window_for(granularity, at=period.start, period=period)]
        seen = {}
        for record in usage:
            if record.meter_code != meter_code or not record.is_billable:
                continue
            window = self._aggregator.window_for(
                granularity, at=record.source.observed_at, period=period
            )
            seen[window.start] = window
        return [seen[k] for k in sorted(seen)]

    def _open_period_for(self, org_id: str, account_id: str) -> BillingPeriod | None:
        """The account's latest still-open period, or `None`. A read of stored state, not a
        decision: which dispositions *need* one is `LateArrivalPolicy.requires_open_period`."""
        if not account_id:
            return None
        open_periods = [p for p in self._repos.periods.all(org_id, account_id) if p.is_open]
        if not open_periods:
            return None
        return max(open_periods, key=lambda p: p.period.start).period

    def _meter_for(self, org_id: str, event: SourceEvent) -> CommercialMeter | None:
        return self._match_meter(event, {m.code: m for m in self._repos.meters.all(org_id)})

    @staticmethod
    def _match_meter(event: SourceEvent, meters: dict) -> CommercialMeter | None:
        """The authoritative meter for a signal, or `None`.

        Exact event-type matches are preferred over wildcard meters, and the result is ordered by
        code so two instances resolving the same event pick the same meter. Ambiguity — two
        meters both claiming a signal — is prevented at definition time by the validator, not
        silently resolved here."""
        candidates = [m for m in meters.values() if m.accepts(event.source, event.event_type)]
        if not candidates:
            return None
        exact = [m for m in candidates if event.event_type in m.accepted_event_types]
        return sorted(exact or candidates, key=lambda m: m.code)[0]

    def _meter(self, org_id: str, code: str) -> CommercialMeter:
        found = self._repos.meters.by_code(org_id, code) or self._repos.meters.get(org_id, code)
        if found is None:
            raise _NotFound(f"meter {code!r} is not defined")
        return found

    def _period(self, command) -> UsagePeriod:
        found = self._repos.periods.for_account(
            command.org_id, command.account_id, command.period_start, command.period_end
        )
        if found is None:
            raise _NotFound(
                f"no usage period {command.period_start}–{command.period_end} for account "
                f"{command.account_id!r}"
            )
        return found

    def _quarantine_entry(self, org_id: str, entry_id: str) -> QuarantineEntry:
        found = self._repos.quarantine.get(org_id, entry_id)
        if found is None:
            raise _NotFound(f"quarantined event {entry_id!r} not found")
        return found

    def _usage_for(self, org_id: str, account_id: str, period: BillingPeriod):
        return self._repos.usage.for_period(org_id, account_id, period.start, period.end)

    def _accounts_by_scope(self, org_id: str) -> dict[str, str]:
        """Scope id → billing account id, built from the accounts domain when it is wired.

        Read here and *passed into* the Foundation, so attribution stays pure and this domain
        never imports the accounts domain — the same one-way, shallow dependency M9.9 and M9.10
        used.

        **An organization with more than one active billing account resolves to nothing**, and its
        events quarantine as `UNATTRIBUTABLE` rather than being billed to whichever account
        happened to be read last. M9.8 models no scope→account attachment (an account has no
        workspace or project binding), so there is no honest way to choose between two of them
        here. Quarantining is the correct behaviour under ADR-0023 M6: never silently billed,
        never silently dropped, and visible until somebody attaches the scope explicitly.
        Recorded as a limitation in the M9.11 spec rather than papered over with a guess."""
        accounts = getattr(self._ctx, "commercial_accounts", None)
        if accounts is None:
            return {}
        active = [a for a in accounts.accounts.all(org_id) if a.state.value == "active"]
        return {org_id: active[0].id} if len(active) == 1 else {}

    def _subscriptions_by_account(self, org_id: str) -> dict[str, str]:
        subscriptions = getattr(self._ctx, "subscriptions_engine", None)
        if subscriptions is None:
            return {}
        found: dict[str, str] = {}
        for s in subscriptions.subscriptions.all(org_id):
            if s.is_live and s.account_id not in found:
                found[s.account_id] = s.id
        return found

    @staticmethod
    def _resolution_scope(entry: QuarantineEntry, account_id: str) -> dict[str, str]:
        if not account_id or entry.scope is None:
            return {}
        return {entry.scope.narrowest_id: account_id, entry.scope.org_id: account_id}

    def _record(
        self,
        command,
        action: MeteringAction,
        *,
        account_id: str = "",
        meter_code: str = "",
        event_id: str = "",
        usage_id: str = "",
        period: BillingPeriod | None = None,
        detail: str = "",
    ) -> None:
        """Append metering history **inside the caller's transaction**, so the history and the
        fact it describes commit or vanish together."""
        self._repos.activity.append(
            self._factory.activity(
                org_id=command.org_id,
                action=action,
                actor_id=command.actor_id,
                account_id=account_id,
                meter_code=meter_code,
                event_id=event_id,
                usage_id=usage_id,
                period=period,
                detail=detail,
            )
        )

    def _event(self, command, subject_id: str, **data) -> ApplicationEvent:
        return ApplicationEvent(
            id=self._ids.new(PREFIX_APP_EVENT),
            type=ApplicationEventType.USAGE_INGESTED,
            ts=self._clock.now(),
            use_case=type(command).__name__,
            outcome="completed",
            org_id=command.org_id,
            actor=command.actor_id,
            subject_id=subject_id,
            data=dict(data),
        )


def _period_sealed_event():
    from billing.foundation import BillingEventType as E

    return E.USAGE_PERIOD_SEALED


def _period_closing_event():
    from billing.foundation import BillingEventType as E

    return E.USAGE_PERIOD_CLOSING


def _quarantine_resolved_event():
    from billing.foundation import BillingEventType as E

    return E.USAGE_QUARANTINE_RESOLVED


def _quarantine_discarded_event():
    from billing.foundation import BillingEventType as E

    return E.USAGE_QUARANTINE_DISCARDED


class MeteringQueryUseCases:
    """Read-only. No transaction, no audit, no mutation, and no measurement."""

    def __init__(
        self,
        *,
        context,
        metering_repos: MeteringRepositories,
        factory,
        meter_lifecycle,
        period_lifecycle,
        quarantine_lifecycle,
        aggregator,
        inspector,
        search,
        exporter,
        reconciliation,
        stream,
        ingestion,
        authz,
    ) -> None:
        self._ctx = context
        self._repos = metering_repos
        self._factory = factory
        self._meter_lifecycle = meter_lifecycle
        self._period_lifecycle = period_lifecycle
        self._quarantine_lifecycle = quarantine_lifecycle
        self._aggregator = aggregator
        self._inspector = inspector
        self._search = search
        self._exporter = exporter
        self._reconciliation = reconciliation
        self._stream = stream
        self._ingestion = ingestion
        self._authz = authz

    def meter(self, query: GetCommercialMeter) -> CommercialMeterView | None:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = self._repos.meters.by_code(query.org_id, query.code)
        if found is None:
            return None
        return meter_view(found, self._meter_lifecycle.allowed(found.state))

    def meters(self, query: ListCommercialMeters) -> tuple[CommercialMeterView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        return tuple(
            meter_view(m, self._meter_lifecycle.allowed(m.state))
            for m in self._repos.meters.all(query.org_id)
            if query.include_retired or m.state is not MeterState.RETIRED
        )

    def snapshot(self, query: GetCommercialUsageSnapshot) -> UsageSnapshotView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        period = BillingPeriod(query.period_start, query.period_end)
        usage = self._repos.usage.for_period(
            query.org_id, query.account_id, period.start, period.end
        )
        aggregates = self._repos.aggregates.for_period(
            query.org_id, query.account_id, period.start, period.end
        )
        period_row = self._repos.periods.for_account(
            query.org_id, query.account_id, period.start, period.end
        )
        quarantined = len(
            [
                q
                for q in self._repos.quarantine.pending(query.org_id, 1000)
                if period.contains(q.source.observed_at)
            ]
        )
        return snapshot_view(
            self._aggregator.snapshot(
                usage,
                aggregates,
                org_id=query.org_id,
                account_id=query.account_id,
                period=period,
                state=period_row.state if period_row else PeriodState.OPEN,
                quarantined_count=quarantined,
                at=period.end,
            )
        )

    def period(self, query: GetUsagePeriodState) -> UsagePeriodView | None:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = self._repos.periods.for_account(
            query.org_id, query.account_id, query.period_start, query.period_end
        )
        if found is None:
            return None
        return period_view(found, self._period_lifecycle.allowed(found.state))

    def periods(self, query: ListUsagePeriods) -> tuple[UsagePeriodView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        return tuple(
            period_view(p, self._period_lifecycle.allowed(p.state))
            for p in self._repos.periods.all(query.org_id, query.account_id)
        )

    def search(self, query: SearchCommercialUsage) -> UsageSearchView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        result = self._search.search(self._repos.usage.all(query.org_id, 5000), query.query)
        return UsageSearchView(usage=tuple(usage_view(u) for u in result.usage), total=result.total)

    def inspect(self, query: InspectCommercialUsage) -> UsageInspectionView | None:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        found = self._repos.usage.get(query.org_id, query.usage_id)
        if found is None:
            return None
        corrections = self._repos.corrections.for_usage(query.org_id, found.id)
        return UsageInspectionView(
            usage=usage_view(found),
            explanation=self._inspector.explain(found),
            corrections=tuple(
                f"{c.kind.value} {c.delta_units} {c.unit}: {c.reason or c.reason_code}"
                for c in corrections
            ),
        )

    def diagnose(self, query: DiagnoseUsagePeriod) -> MeteringDiagnosticsView | None:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        period = BillingPeriod(query.period_start, query.period_end)
        period_row = self._repos.periods.for_account(
            query.org_id, query.account_id, period.start, period.end
        )
        if period_row is None:
            return None
        return diagnostics_view(
            query.account_id,
            period,
            self._inspector.diagnose(
                period=period_row,
                usage=self._repos.usage.for_period(
                    query.org_id, query.account_id, period.start, period.end
                ),
                quarantined=self._repos.quarantine.all(query.org_id, 1000),
                meters=self._repos.meters.all(query.org_id),
            ),
        )

    def quarantined(self, query: ListQuarantinedEvents) -> tuple[QuarantineEntryView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        entries = (
            self._repos.quarantine.pending(query.org_id, query.limit)
            if query.pending_only
            else self._repos.quarantine.all(query.org_id, query.limit)
        )
        return tuple(
            quarantine_view(e, self._quarantine_lifecycle.allowed(e.state)) for e in entries
        )

    def reconciliation(self, query: GetUsageReconciliationReport) -> ReconciliationView:
        """A read: reconciling computes nothing durable, so asking is free of side effects."""
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        period = BillingPeriod(query.period_start, query.period_end)
        recorded = self._repos.usage.for_period(
            query.org_id, query.account_id, period.start, period.end
        )
        meters = {m.code: m for m in self._repos.meters.all(query.org_id)}
        expected = []
        for signal in self._stream.replay(query.org_id, period.start, period.end):
            meter = MeteringCommandUseCases._match_meter(self._factory.source_event(signal), meters)
            if meter is not None:
                expected.append(
                    (
                        signal.event_id,
                        meter.code,
                        self._ingestion.normalizer.normalize(signal.raw_units, meter.normalization),
                    )
                )
        return reconciliation_view(
            self._reconciliation.reconcile(
                org_id=query.org_id,
                account_id=query.account_id,
                period=period,
                expected=tuple(expected),
                recorded=recorded,
                at=period.end,
            )
        )

    def export(self, query: ExportCommercialUsage) -> UsageExportView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        period = BillingPeriod(query.period_start, query.period_end)
        period_row = self._repos.periods.for_account(
            query.org_id, query.account_id, period.start, period.end
        )
        return export_view(
            self._exporter.export(
                self._repos.usage.for_period(
                    query.org_id, query.account_id, period.start, period.end
                ),
                org_id=query.org_id,
                account_id=query.account_id,
                period=period,
                sealed=bool(period_row and period_row.is_sealed),
                at=period.end,
            )
        )

    def timeline(self, query: MeteringTimeline) -> tuple[MeteringActivityView, ...]:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)
        return tuple(
            activity_view(a)
            for a in self._repos.activity.history(query.org_id, query.account_id, query.limit)
        )


# ── wiring table ────────────────────────────────────────────────────────────────


def register_metering_command_handlers(bus, uc: MeteringCommandUseCases) -> None:
    bus.register(DefineCommercialMeter, uc.define_meter)
    bus.register(VersionCommercialMeter, uc.version_meter)
    bus.register(ActivateCommercialMeter, uc.activate_meter)
    bus.register(DeprecateCommercialMeter, uc.deprecate_meter)
    bus.register(ReinstateCommercialMeter, uc.reinstate_meter)
    bus.register(RetireCommercialMeter, uc.retire_meter)
    bus.register(OpenUsagePeriod, uc.open_period)
    bus.register(BeginUsagePeriodClose, uc.begin_close)
    bus.register(SealUsagePeriod, uc.seal_period)
    bus.register(IngestMetricSignal, uc.ingest)
    bus.register(DrainMetricStream, uc.drain)
    bus.register(ResolveQuarantinedEvent, uc.resolve_quarantine)
    bus.register(DiscardQuarantinedEvent, uc.discard_quarantine)
    bus.register(RecordUsageCorrection, uc.correct)
    bus.register(RebuildUsageAggregates, uc.rebuild_aggregates)
    bus.register(ReplayUsagePeriod, uc.replay_period)
    bus.register(ReconcileUsagePeriod, uc.reconcile)


def register_metering_query_handlers(bus, uc: MeteringQueryUseCases) -> None:
    bus.register(GetCommercialMeter, uc.meter)
    bus.register(ListCommercialMeters, uc.meters)
    bus.register(GetCommercialUsageSnapshot, uc.snapshot)
    bus.register(GetUsagePeriodState, uc.period)
    bus.register(ListUsagePeriods, uc.periods)
    bus.register(SearchCommercialUsage, uc.search)
    bus.register(InspectCommercialUsage, uc.inspect)
    bus.register(DiagnoseUsagePeriod, uc.diagnose)
    bus.register(ListQuarantinedEvents, uc.quarantined)
    bus.register(GetUsageReconciliationReport, uc.reconciliation)
    bus.register(ExportCommercialUsage, uc.export)
    bus.register(MeteringTimeline, uc.timeline)
