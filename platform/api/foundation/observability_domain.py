"""Observability Platform domain model (Epic 8, M8.17) — additive, read-model only.

Observability **owns visibility** and nothing else — no execution, no orchestration, no
deployment. Every type here is an immutable *projection shape* derived from the append-only Audit
stream (M8.9); the only authoritative state Observability owns is the customer's **alert rules**
(configuration). Pure, frozen, slotted data with `as_dict()` views for the transport layer.

Golden rules: Runtime owns execution, Deployment owns deployment, Knowledge owns retrieval, Tool
Framework owns tools — **Observability owns observation**. No monitoring logic enters those layers;
Observability is a *passive consumer* of the events they already emit (§A5).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

# ── metrics ────────────────────────────────────────────────────────────────────────


class MetricKind(StrEnum):
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    """A metric's identity + how it is derived from events (the derivation itself lives in the
    metric catalog). Pure metadata — no state."""

    key: str
    kind: MetricKind
    category: str  # LogCategory value (runtime/deployment/knowledge/tool/provider/platform)
    description: str = ""
    unit: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind.value,
            "category": self.category,
            "description": self.description,
            "unit": self.unit,
        }


@dataclass(frozen=True, slots=True)
class MetricPoint:
    ts: float
    value: float

    def as_dict(self) -> dict[str, Any]:
        return {"ts": self.ts, "value": round(self.value, 6)}


@dataclass(frozen=True, slots=True)
class MetricSnapshot:
    """The current aggregated value of one metric over a window."""

    key: str
    kind: MetricKind
    count: int
    total: float
    minimum: float
    maximum: float
    average: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind.value,
            "count": self.count,
            "total": round(self.total, 6),
            "min": round(self.minimum, 6),
            "max": round(self.maximum, 6),
            "average": round(self.average, 6),
        }


@dataclass(frozen=True, slots=True)
class MetricSeries:
    """A time-bucketed series of one metric (for charts)."""

    key: str
    points: tuple[MetricPoint, ...] = ()
    bucket_seconds: float = 60.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "bucket_seconds": self.bucket_seconds,
            "points": [p.as_dict() for p in self.points],
        }


@dataclass(frozen=True, slots=True)
class MetricAggregation:
    """A named aggregation across several metrics (a dashboard tile source)."""

    scope: str
    snapshots: tuple[MetricSnapshot, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"scope": self.scope, "metrics": {s.key: s.as_dict() for s in self.snapshots}}


@dataclass(frozen=True, slots=True)
class MetricReport:
    scope: str
    generated_at: float
    snapshots: tuple[MetricSnapshot, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "generated_at": self.generated_at,
            "metrics": [s.as_dict() for s in self.snapshots],
        }


# ── logging ────────────────────────────────────────────────────────────────────────


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class LogCategory(StrEnum):
    RUNTIME = "runtime"
    DEPLOYMENT = "deployment"
    KNOWLEDGE = "knowledge"
    TOOL = "tool"
    PROVIDER = "provider"
    PLATFORM = "platform"
    SECURITY = "security"


@dataclass(frozen=True, slots=True)
class LogContext:
    """The scope a log line pertains to (hoisted from the event for filtering)."""

    org_id: str
    workspace_id: str | None = None
    project_id: str | None = None
    actor: str = ""
    resource: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "org_id": self.org_id,
            "workspace_id": self.workspace_id,
            "project_id": self.project_id,
            "actor": self.actor,
            "resource": self.resource,
        }


@dataclass(frozen=True, slots=True)
class LogEntry:
    """One structured, immutable log line — a projection of one audit record (never a new fact)."""

    seq: int
    ts: float
    level: LogLevel
    category: LogCategory
    event_type: str
    message: str
    context: LogContext
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "level": self.level.value,
            "category": self.category.value,
            "event_type": self.event_type,
            "message": self.message,
            "context": self.context.as_dict(),
            "data": self.data,
        }


# `StructuredLog` is the immutable log line as emitted — an alias of `LogEntry` (one shape, one
# source of truth; the append-only audit stream is the store).
StructuredLog = LogEntry


@dataclass(frozen=True, slots=True)
class LogQuery:
    """An immutable filter over the log projection."""

    org_id: str
    workspace_id: str | None = None
    project_id: str | None = None
    level: LogLevel | None = None
    category: LogCategory | None = None
    actor: str | None = None
    since: float | None = None
    until: float | None = None
    limit: int = 100
    offset: int = 0


@dataclass(frozen=True, slots=True)
class LogReport:
    entries: tuple[LogEntry, ...]
    total: int
    limit: int
    offset: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "entries": [e.as_dict() for e in self.entries],
            "total": self.total,
            "limit": self.limit,
            "offset": self.offset,
            "has_more": self.offset + len(self.entries) < self.total,
        }


# ── tracing (distributed trace model — no OpenTelemetry) ───────────────────────────


@dataclass(frozen=True, slots=True)
class SpanContext:
    trace_id: str
    span_id: str
    parent_span_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
        }


@dataclass(frozen=True, slots=True)
class Span:
    context: SpanContext
    name: str
    start_ts: float
    end_ts: float
    status: str = "ok"  # "ok" | "error"
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        return max(0.0, (self.end_ts - self.start_ts) * 1000.0)

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.context.as_dict(),
            "name": self.name,
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "duration_ms": round(self.duration_ms, 3),
            "status": self.status,
            "attributes": self.attributes,
        }


@dataclass(frozen=True, slots=True)
class TraceContext:
    trace_id: str
    correlation_key: str  # the resource/session/deployment the trace follows

    def as_dict(self) -> dict[str, Any]:
        return {"trace_id": self.trace_id, "correlation_key": self.correlation_key}


@dataclass(frozen=True, slots=True)
class Trace:
    context: TraceContext
    spans: tuple[Span, ...]
    start_ts: float
    end_ts: float
    status: str = "ok"

    @property
    def duration_ms(self) -> float:
        return max(0.0, (self.end_ts - self.start_ts) * 1000.0)

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.context.as_dict(),
            "span_count": len(self.spans),
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "duration_ms": round(self.duration_ms, 3),
            "status": self.status,
            "spans": [s.as_dict() for s in self.spans],
        }

    def summary(self) -> dict[str, Any]:
        return {
            "trace_id": self.context.trace_id,
            "correlation_key": self.context.correlation_key,
            "span_count": len(self.spans),
            "duration_ms": round(self.duration_ms, 3),
            "status": self.status,
            "start_ts": self.start_ts,
        }


@dataclass(frozen=True, slots=True)
class TraceReport:
    traces: tuple[Trace, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"traces": [t.summary() for t in self.traces], "total": len(self.traces)}


# ── alerting ───────────────────────────────────────────────────────────────────────


class AlertComparator(StrEnum):
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertState(StrEnum):
    OK = "ok"
    FIRING = "firing"


@dataclass(frozen=True, slots=True)
class AlertRule:
    """A customer-configured alert (the only durable Observability state). Evaluated on demand
    against the metric projection — Observability never runs a background poller."""

    id: str
    org_id: str
    name: str
    metric_key: str
    comparator: AlertComparator
    threshold: float
    window_seconds: float = 3600.0
    severity: AlertSeverity = AlertSeverity.WARNING
    project_id: str | None = None
    enabled: bool = True
    created_by: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    def with_updates(self, **changes: Any) -> AlertRule:
        allowed = {
            "name",
            "metric_key",
            "comparator",
            "threshold",
            "window_seconds",
            "severity",
            "project_id",
            "enabled",
            "updated_at",
        }
        return replace(self, **{k: v for k, v in changes.items() if k in allowed and v is not None})

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "org_id": self.org_id,
            "name": self.name,
            "metric_key": self.metric_key,
            "comparator": self.comparator.value,
            "threshold": self.threshold,
            "window_seconds": self.window_seconds,
            "severity": self.severity.value,
            "project_id": self.project_id,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# `AlertDefinition` is the immutable spec portion of a rule (name + metric + comparator +
# threshold + window + severity) — the same shape as `AlertRule` minus identity/audit; the rule
# *is* the definition plus its id/scope, so `AlertRule` is the single source of truth.
AlertDefinition = AlertRule


@dataclass(frozen=True, slots=True)
class AlertEvent:
    """One evaluation outcome of a rule — computed, not stored (passive consumer)."""

    rule_id: str
    name: str
    state: AlertState
    value: float
    threshold: float
    comparator: AlertComparator
    severity: AlertSeverity
    at: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "state": self.state.value,
            "value": round(self.value, 6),
            "threshold": self.threshold,
            "comparator": self.comparator.value,
            "severity": self.severity.value,
            "at": self.at,
        }


@dataclass(frozen=True, slots=True)
class AlertHistory:
    """A window of evaluation outcomes for one rule (recomputed on read)."""

    rule_id: str
    events: tuple[AlertEvent, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"rule_id": self.rule_id, "events": [e.as_dict() for e in self.events]}


# ── health ─────────────────────────────────────────────────────────────────────────


class HealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    """The health of one component (runtime/deployment/knowledge/provider/tool), derived from the
    recent success/failure event ratio — Observability consumes health, it never probes."""

    component: str
    status: HealthStatus
    total: int
    failures: int
    failure_rate: float
    at: float
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "status": self.status.value,
            "total": self.total,
            "failures": self.failures,
            "failure_rate": round(self.failure_rate, 4),
            "at": self.at,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class HealthAggregation:
    """The overall platform health = the worst component status, with per-component snapshots."""

    status: HealthStatus
    components: tuple[HealthSnapshot, ...]
    at: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "at": self.at,
            "components": [c.as_dict() for c in self.components],
        }


HealthReport = HealthAggregation  # the report is the aggregation snapshot (one shape)


@dataclass(frozen=True, slots=True)
class HealthHistory:
    component: str
    snapshots: tuple[HealthSnapshot, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"component": self.component, "snapshots": [s.as_dict() for s in self.snapshots]}
