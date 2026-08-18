"""Metering Engine wire DTOs (M9.11) — Pydantic v2, additive to the earlier schemas.

Same discipline as before: a third representation, distinct from Foundation aggregates and
Application views, with `to_*()` inbound and `from_*()` outbound. Foundation enums are reused so
validation and OpenAPI enums come from the domain vocabulary.

**Not one field here carries money, and not one carries an operational metric.** A quantity is a
count in a meter's declared unit; what it costs is rating's (M9.12). And the inbound signal type
is deliberately minimal — an identity, a magnitude, and an attribution scope — so this surface
cannot become a second home for runtime telemetry Billing does not own.

`event_id` is required and has no default, deliberately. It is the exactly-once key: an ingestion
API that let a caller omit it would silently make billing non-idempotent.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from billing.foundation import (
    MeterAggregation,
    MeterSource,
    Normalization,
    Rounding,
    Scope,
    ScopeKind,
    UsageStatus,
    WindowGranularity,
)

# ── shared bodies ───────────────────────────────────────────────────────────────


class NormalizationBody(BaseModel):
    """Raw signal → billable units as an **exact integer ratio**. No float, ever."""

    numerator: int = Field(default=1, ge=1)
    denominator: int = Field(default=1, ge=1)
    rounding: Rounding = Rounding.HALF_UP

    def to_normalization(self) -> Normalization:
        return Normalization(
            numerator=self.numerator, denominator=self.denominator, rounding=self.rounding
        )


class ScopeBody(BaseModel):
    kind: ScopeKind = ScopeKind.ORGANIZATION
    workspace_id: str = ""
    project_id: str = ""
    agent_id: str = ""

    def to_scope(self, org_id: str) -> Scope:
        """The organization always comes from the verified token, never the body."""
        return Scope(
            org_id=org_id,
            kind=self.kind,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            agent_id=self.agent_id,
        )


# ── requests ────────────────────────────────────────────────────────────────────


class DefineMeterBody(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "voice.minutes",
                "unit": "minute",
                "source": "runtime.session",
                "aggregation": "sum",
                "normalization": {"numerator": 1, "denominator": 60000, "rounding": "half_up"},
                "accepted_event_types": ["metric.session.duration_ms"],
            }
        }
    )
    code: str
    unit: str = Field(min_length=1, description="What the meter counts in")
    display_name: str = ""
    aggregation: MeterAggregation = MeterAggregation.SUM
    source: MeterSource = Field(
        default=MeterSource.RUNTIME_SESSION,
        description="The **one** authoritative producer. Two meters claiming one signal is "
        "rejected — that is the double-counting failure",
    )
    normalization: NormalizationBody = Field(default_factory=NormalizationBody)
    late_arrival_window_seconds: float = Field(default=172_800.0, ge=0)
    accepted_event_types: list[str] = Field(default_factory=list)
    description: str = ""


class VersionMeterBody(BaseModel):
    unit: str | None = None
    display_name: str | None = None
    aggregation: MeterAggregation | None = None
    normalization: NormalizationBody | None = None
    late_arrival_window_seconds: float | None = Field(default=None, ge=0)
    accepted_event_types: list[str] | None = None
    description: str | None = None


class ReasonBody(BaseModel):
    reason: str = ""


class UsagePeriodBody(BaseModel):
    """Named distinctly from M9.5's `PeriodBody`, which carries a bare `{start, end}` window.
    This one names the account too, because a *usage* period belongs to one."""

    account_id: str
    period_start: float = Field(gt=0)
    period_end: float = Field(gt=0)


class MetricSignalBody(BaseModel):
    """One decoded `metric.*` fact. Billing did not produce this and keeps no operational copy
    of it — only the identity, the magnitude, and the attribution scope."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "event_id": "evt_8f21",
                "event_type": "metric.session.duration_ms",
                "source": "runtime.session",
                "observed_at": 1735689600.0,
                "raw_units": 185000,
                "raw_unit": "ms",
            }
        }
    )
    event_id: str = Field(
        min_length=1,
        description="The exactly-once key. Re-sending the same id is a no-op, not a second bill",
    )
    event_type: str = Field(min_length=1)
    source: MeterSource
    observed_at: float = Field(gt=0, description="When the producer observed it")
    raw_units: int = Field(ge=0, description="The raw magnitude, before normalization")
    raw_unit: str = ""
    received_at: float | None = Field(
        default=None, description="When Billing received it; the difference is lateness"
    )
    producer: str = ""
    sequence: int = 0
    payload_digest: str = ""
    scope: ScopeBody = Field(default_factory=ScopeBody)


class IngestBody(BaseModel):
    signal: MetricSignalBody
    period_start: float = Field(gt=0)
    period_end: float = Field(gt=0)
    ingest_run_id: str = ""


class DrainBody(BaseModel):
    period_start: float = Field(gt=0)
    period_end: float = Field(gt=0)
    limit: int = Field(default=500, ge=1, le=5000)


class ResolveQuarantineBody(BaseModel):
    meter_code: str = ""
    account_id: str = ""
    note: str = ""


class CorrectionBody(BaseModel):
    delta_units: int = Field(description="Signed; negative withdraws. Zero is rejected")
    reason: str = ""
    reason_code: str = ""
    period_start: float | None = None
    period_end: float | None = None
    sequence: int = Field(default=1, ge=1)


class RebuildBody(BaseModel):
    account_id: str
    period_start: float = Field(gt=0)
    period_end: float = Field(gt=0)
    granularity: WindowGranularity = WindowGranularity.PERIOD


# ── responses ───────────────────────────────────────────────────────────────────


class NormalizationResponse(BaseModel):
    numerator: int
    denominator: int
    rounding: str
    is_identity: bool

    @classmethod
    def from_view(cls, v) -> NormalizationResponse:
        return cls(
            numerator=v.numerator,
            denominator=v.denominator,
            rounding=v.rounding,
            is_identity=v.is_identity,
        )


class MeterResponse(BaseModel):
    id: str
    code: str
    display_name: str
    unit: str
    aggregation: str
    source: str
    normalization: NormalizationResponse
    late_arrival_window_seconds: float
    state: str
    version: int
    pin: str
    description: str
    accepted_event_types: list[str]
    is_ingestible: bool
    allowed_triggers: list[str]
    created_at: float
    updated_at: float
    activated_at: float | None
    retired_at: float | None

    @classmethod
    def from_view(cls, v) -> MeterResponse:
        return cls(
            id=v.id,
            code=v.code,
            display_name=v.display_name,
            unit=v.unit,
            aggregation=v.aggregation,
            source=v.source,
            normalization=NormalizationResponse.from_view(v.normalization),
            late_arrival_window_seconds=v.late_arrival_window_seconds,
            state=v.state,
            version=v.version,
            pin=v.pin,
            description=v.description,
            accepted_event_types=list(v.accepted_event_types),
            is_ingestible=v.is_ingestible,
            allowed_triggers=list(v.allowed_triggers),
            created_at=v.created_at,
            updated_at=v.updated_at,
            activated_at=v.activated_at,
            retired_at=v.retired_at,
        )


class IngestionReceiptResponse(BaseModel):
    """`duplicate: true` is a **success**: the event was already counted exactly once."""

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

    @classmethod
    def from_view(cls, v) -> IngestionReceiptResponse:
        return cls(
            outcome=v.outcome,
            duplicate=v.duplicate,
            quarantined=v.quarantined,
            usage_id=v.usage_id,
            event_id=v.event_id,
            meter_code=v.meter_code,
            units=v.units,
            disposition=v.disposition,
            reason=v.reason,
            detail=v.detail,
        )


class DrainReceiptResponse(BaseModel):
    signals_seen: int
    recorded: int
    adjusted: int
    duplicates: int
    quarantined: int
    ingest_run_id: str

    @classmethod
    def from_view(cls, v) -> DrainReceiptResponse:
        return cls(
            signals_seen=v.signals_seen,
            recorded=v.recorded,
            adjusted=v.adjusted,
            duplicates=v.duplicates,
            quarantined=v.quarantined,
            ingest_run_id=v.ingest_run_id,
        )


class SourceEventResponse(BaseModel):
    event_id: str
    event_type: str
    source: str
    observed_at: float
    received_at: float
    lateness: float
    producer: str
    sequence: int
    payload_digest: str


class ProvenanceResponse(BaseModel):
    meter_pin: str
    source_kind: str
    ingested_at: float
    ingest_run_id: str
    normalization: NormalizationResponse
    raw_units: int
    raw_unit: str
    disposition: str
    replayed: bool
    was_normalized: bool
    was_late: bool


class UsageResponse(BaseModel):
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
    source: SourceEventResponse
    provenance: ProvenanceResponse

    @classmethod
    def from_view(cls, v) -> UsageResponse:
        return cls(
            id=v.id,
            account_id=v.account_id,
            meter_code=v.meter_code,
            meter_version=v.meter_version,
            meter_pin=v.meter_pin,
            units=v.units,
            unit=v.unit,
            scope_id=v.scope_id,
            period_start=v.period_start,
            period_end=v.period_end,
            status=v.status,
            subscription_id=v.subscription_id,
            corrects_usage_id=v.corrects_usage_id,
            is_billable=v.is_billable,
            is_adjustment=v.is_adjustment,
            recorded_at=v.recorded_at,
            digest=v.digest,
            source=SourceEventResponse(
                event_id=v.source.event_id,
                event_type=v.source.event_type,
                source=v.source.source,
                observed_at=v.source.observed_at,
                received_at=v.source.received_at,
                lateness=v.source.lateness,
                producer=v.source.producer,
                sequence=v.source.sequence,
                payload_digest=v.source.payload_digest,
            ),
            provenance=ProvenanceResponse(
                meter_pin=v.provenance.meter_pin,
                source_kind=v.provenance.source_kind,
                ingested_at=v.provenance.ingested_at,
                ingest_run_id=v.provenance.ingest_run_id,
                normalization=NormalizationResponse.from_view(v.provenance.normalization),
                raw_units=v.provenance.raw_units,
                raw_unit=v.provenance.raw_unit,
                disposition=v.provenance.disposition,
                replayed=v.provenance.replayed,
                was_normalized=v.provenance.was_normalized,
                was_late=v.provenance.was_late,
            ),
        )


class UsageSearchResponse(BaseModel):
    usage: list[UsageResponse]
    total: int

    @classmethod
    def from_view(cls, v) -> UsageSearchResponse:
        return cls(usage=[UsageResponse.from_view(u) for u in v.usage], total=v.total)


class AggregateResponse(BaseModel):
    id: str
    meter_code: str
    scope_id: str
    granularity: str
    window_start: float
    window_end: float
    aggregation: str
    units: int
    unit: str
    record_count: int
    adjustment_count: int
    source_digest: str
    sealed: bool


class UsageSnapshotResponse(BaseModel):
    account_id: str
    period_start: float
    period_end: float
    at: float
    state: str
    is_sealed: bool
    aggregates: list[AggregateResponse]
    meter_codes: list[str]
    record_count: int
    adjustment_count: int
    quarantined_count: int
    digest: str

    @classmethod
    def from_view(cls, v) -> UsageSnapshotResponse:
        return cls(
            account_id=v.account_id,
            period_start=v.period_start,
            period_end=v.period_end,
            at=v.at,
            state=v.state,
            is_sealed=v.is_sealed,
            aggregates=[
                AggregateResponse(
                    id=a.id,
                    meter_code=a.meter_code,
                    scope_id=a.scope_id,
                    granularity=a.window.granularity,
                    window_start=a.window.start,
                    window_end=a.window.end,
                    aggregation=a.aggregation,
                    units=a.units,
                    unit=a.unit,
                    record_count=a.record_count,
                    adjustment_count=a.adjustment_count,
                    source_digest=a.source_digest,
                    sealed=a.sealed,
                )
                for a in v.aggregates
            ],
            meter_codes=list(v.meter_codes),
            record_count=v.record_count,
            adjustment_count=v.adjustment_count,
            quarantined_count=v.quarantined_count,
            digest=v.digest,
        )


class UsagePeriodResponse(BaseModel):
    id: str
    account_id: str
    period_start: float
    period_end: float
    state: str
    record_count: int
    adjustment_count: int
    quarantined_count: int
    seal_digest: str
    allowed_triggers: list[str]
    opened_at: float
    closing_at: float | None
    sealed_at: float | None
    sealed_by: str

    @classmethod
    def from_view(cls, v) -> UsagePeriodResponse:
        return cls(
            id=v.id,
            account_id=v.account_id,
            period_start=v.period_start,
            period_end=v.period_end,
            state=v.state,
            record_count=v.record_count,
            adjustment_count=v.adjustment_count,
            quarantined_count=v.quarantined_count,
            seal_digest=v.seal_digest,
            allowed_triggers=list(v.allowed_triggers),
            opened_at=v.opened_at,
            closing_at=v.closing_at,
            sealed_at=v.sealed_at,
            sealed_by=v.sealed_by,
        )


class QuarantineResponse(BaseModel):
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
    allowed_triggers: list[str]

    @classmethod
    def from_view(cls, v) -> QuarantineResponse:
        return cls(
            id=v.id,
            event_id=v.event_id,
            event_type=v.event_type,
            meter_code=v.meter_code,
            reason=v.reason,
            detail=v.detail,
            state=v.state,
            account_id=v.account_id,
            raw_units=v.raw_units,
            raw_unit=v.raw_unit,
            received_at=v.received_at,
            resolved_at=v.resolved_at,
            resolved_by=v.resolved_by,
            resolution_note=v.resolution_note,
            resulting_usage_id=v.resulting_usage_id,
            allowed_triggers=list(v.allowed_triggers),
        )


class DiscrepancyResponse(BaseModel):
    kind: str
    event_id: str
    meter_code: str
    usage_id: str
    expected: str
    actual: str
    detail: str


class ReconciliationResponse(BaseModel):
    """`duplicate_count` must always be zero. A non-zero value is a financial-integrity
    incident — the one metering failure re-running cannot fix."""

    account_id: str
    period_start: float
    period_end: float
    at: float
    events_examined: int
    usage_examined: int
    is_clean: bool
    duplicate_count: int
    discrepancies: list[DiscrepancyResponse]

    @classmethod
    def from_view(cls, v) -> ReconciliationResponse:
        return cls(
            account_id=v.account_id,
            period_start=v.period_start,
            period_end=v.period_end,
            at=v.at,
            events_examined=v.events_examined,
            usage_examined=v.usage_examined,
            is_clean=v.is_clean,
            duplicate_count=v.duplicate_count,
            discrepancies=[
                DiscrepancyResponse(
                    kind=d.kind,
                    event_id=d.event_id,
                    meter_code=d.meter_code,
                    usage_id=d.usage_id,
                    expected=d.expected,
                    actual=d.actual,
                    detail=d.detail,
                )
                for d in v.discrepancies
            ],
        )


class ReplayReceiptResponse(BaseModel):
    """`matches_seal` is the proof: the replay reproduced exactly what the period sealed."""

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

    @classmethod
    def from_view(cls, v) -> ReplayReceiptResponse:
        return cls(
            account_id=v.account_id,
            period_start=v.period_start,
            period_end=v.period_end,
            events_replayed=v.events_replayed,
            usage_produced=v.usage_produced,
            duplicates_skipped=v.duplicates_skipped,
            quarantined=v.quarantined,
            replay_digest=v.replay_digest,
            seal_digest=v.seal_digest,
            matches_seal=v.matches_seal,
        )


class UsageInspectionResponse(BaseModel):
    usage: UsageResponse
    explanation: list[str]
    corrections: list[str]

    @classmethod
    def from_view(cls, v) -> UsageInspectionResponse:
        return cls(
            usage=UsageResponse.from_view(v.usage),
            explanation=list(v.explanation),
            corrections=list(v.corrections),
        )


class DiagnosticResponse(BaseModel):
    path: str
    code: str
    severity: str
    message: str
    remediation: str


class DiagnosticsResponse(BaseModel):
    account_id: str
    period_start: float
    period_end: float
    findings: list[DiagnosticResponse]
    error_count: int
    warning_count: int
    is_valid: bool

    @classmethod
    def from_view(cls, v) -> DiagnosticsResponse:
        return cls(
            account_id=v.account_id,
            period_start=v.period_start,
            period_end=v.period_end,
            findings=[
                DiagnosticResponse(
                    path=f.path,
                    code=f.code,
                    severity=f.severity,
                    message=f.message,
                    remediation=f.remediation,
                )
                for f in v.findings
            ],
            error_count=v.error_count,
            warning_count=v.warning_count,
            is_valid=v.is_valid,
        )


class ExportLineResponse(BaseModel):
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


class ExportResponse(BaseModel):
    """Deterministically ordered and digested, so two exports of an unchanged sealed period are
    byte-identical."""

    account_id: str
    period_start: float
    period_end: float
    generated_at: float
    lines: list[ExportLineResponse]
    row_count: int
    total_units: int
    digest: str
    sealed: bool

    @classmethod
    def from_view(cls, v) -> ExportResponse:
        return cls(
            account_id=v.account_id,
            period_start=v.period_start,
            period_end=v.period_end,
            generated_at=v.generated_at,
            lines=[
                ExportLineResponse(
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
                for r in v.rows
            ],
            row_count=v.row_count,
            total_units=v.total_units,
            digest=v.digest,
            sealed=v.sealed,
        )


class ActivityResponse(BaseModel):
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

    @classmethod
    def from_view(cls, v) -> ActivityResponse:
        return cls(
            id=v.id,
            action=v.action,
            actor_id=v.actor_id,
            at=v.at,
            account_id=v.account_id,
            meter_code=v.meter_code,
            event_id=v.event_id,
            usage_id=v.usage_id,
            period_start=v.period_start,
            period_end=v.period_end,
            detail=v.detail,
        )


def build_usage_query(**kwargs):
    """Wire query parameters → the Foundation's `UsageSearchQuery`. Kept here so the controller
    never constructs a domain object field by field."""
    from billing.foundation import UsageSearchQuery

    return UsageSearchQuery(
        text=kwargs.get("text", ""),
        account_id=kwargs.get("account_id", ""),
        meter_codes=tuple(kwargs.get("meter_codes") or ()),
        statuses=tuple(kwargs.get("statuses") or ()),
        scope_id=kwargs.get("scope_id", ""),
        subscription_id=kwargs.get("subscription_id", ""),
        event_id=kwargs.get("event_id", ""),
        period_start=kwargs.get("period_start"),
        period_end=kwargs.get("period_end"),
        observed_from=kwargs.get("observed_from"),
        observed_until=kwargs.get("observed_until"),
        billable_only=kwargs.get("billable_only", False),
        adjustments_only=kwargs.get("adjustments_only", False),
        late_only=kwargs.get("late_only", False),
        limit=kwargs.get("limit", 100),
        offset=kwargs.get("offset", 0),
    )


__all__ = [
    "ActivityResponse",
    "AggregateResponse",
    "CorrectionBody",
    "DefineMeterBody",
    "DiagnosticsResponse",
    "DrainBody",
    "DrainReceiptResponse",
    "ExportResponse",
    "IngestBody",
    "IngestionReceiptResponse",
    "MeterResponse",
    "MetricSignalBody",
    "NormalizationBody",
    "UsagePeriodBody",
    "QuarantineResponse",
    "ReasonBody",
    "RebuildBody",
    "ReconciliationResponse",
    "ReplayReceiptResponse",
    "ResolveQuarantineBody",
    "ScopeBody",
    "UsageInspectionResponse",
    "UsagePeriodResponse",
    "UsageResponse",
    "UsageSearchResponse",
    "UsageSnapshotResponse",
    "UsageStatus",
    "VersionMeterBody",
    "build_usage_query",
]
