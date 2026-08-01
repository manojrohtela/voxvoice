"""Observability Platform services (Epic 8, M8.17) — additive, read-model only.

Observability is a **passive consumer** of the append-only Audit stream (M8.9): every metric, log,
trace, health snapshot, usage/cost aggregate and dashboard is a *projection* computed on read from
the events the platform already emits. **No polling, no direct Runtime/Deployment access, no
duplicate audit events, no new durable state** — except the customer's **alert rules**
(configuration), which are evaluated on demand against the metric projection (never a background
poller). Golden rule: Observability owns observation; no monitoring logic enters Runtime,
Deployment, Provider Binding, Knowledge, or the Tool Framework.

Components: `MetricsRegistry`/`MetricsCollector`/`MetricsService` (metrics), `LoggingService`
(structured logs), `TracingService` (distributed traces — no OpenTelemetry), `HealthMonitor`
(health from success/failure ratios + consumed Deployment/Runtime health), `UsageService` +
`CostService` (usage/estimated cost — no billing), `AlertManager` + `AlertEvaluator` (alert rules —
no notifications), `DashboardService` (per-scope read models). All read the injected `AuditReader`.
"""

from __future__ import annotations

from collections.abc import Iterable

from .audit import AuditReader, AuditRecord
from .errors import ConflictError, NotFoundError
from .observability_domain import (
    AlertComparator,
    AlertEvent,
    AlertHistory,
    AlertRule,
    AlertSeverity,
    AlertState,
    HealthAggregation,
    HealthHistory,
    HealthSnapshot,
    HealthStatus,
    LogCategory,
    LogEntry,
    LogLevel,
    LogQuery,
    LogReport,
    MetricAggregation,
    MetricDefinition,
    MetricKind,
    MetricPoint,
    MetricReport,
    MetricSeries,
    MetricSnapshot,
    Span,
    SpanContext,
    Trace,
    TraceContext,
    TraceReport,
)
from .rbac import Permission
from .repositories import AlertRuleRepository
from .seams import PREFIX_ALERT_RULE, Clock
from .services import _Base

# ── metric catalog: event → metric derivation (pure) ───────────────────────────────
# key -> (event types, value extractor, kind, category, description, unit)
_C = LogCategory


def _count(_rec: AuditRecord) -> float:
    return 1.0


def _field(name: str):
    def fn(rec: AuditRecord) -> float:
        try:
            return float(rec.data.get(name, 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    return fn


_METRIC_CATALOG: dict[str, tuple] = {
    "conversations.started": (
        ("conversation.started", "session.created"),
        _count,
        MetricKind.COUNTER,
        _C.RUNTIME,
        "Conversations started",
        "count",
    ),
    "conversations.completed": (
        ("execution.completed",),
        _count,
        MetricKind.COUNTER,
        _C.RUNTIME,
        "Turns completed",
        "count",
    ),
    "conversations.failed": (
        ("execution.failed", "execution.failed_real"),
        _count,
        MetricKind.COUNTER,
        _C.RUNTIME,
        "Turns failed",
        "count",
    ),
    "tokens.total": (
        ("execution.completed",),
        _field("tokens"),
        MetricKind.COUNTER,
        _C.PROVIDER,
        "Total tokens",
        "tokens",
    ),
    "latency.ms": (
        ("execution.completed",),
        _field("latency_ms"),
        MetricKind.HISTOGRAM,
        _C.RUNTIME,
        "Execution latency",
        "ms",
    ),
    "tools.executed": (
        ("tool.executed",),
        _count,
        MetricKind.COUNTER,
        _C.TOOL,
        "Tool executions",
        "count",
    ),
    "tools.failed": (
        ("tool.failed",),
        _count,
        MetricKind.COUNTER,
        _C.TOOL,
        "Tool failures",
        "count",
    ),
    "knowledge.retrieved": (
        ("knowledge.retrieved",),
        _count,
        MetricKind.COUNTER,
        _C.KNOWLEDGE,
        "Knowledge retrievals",
        "count",
    ),
    "knowledge.indexed": (
        ("knowledge.indexed",),
        _field("chunks"),
        MetricKind.COUNTER,
        _C.KNOWLEDGE,
        "Chunks indexed",
        "chunks",
    ),
    "knowledge.embeddings": (
        ("knowledge.embedding_generated",),
        _field("chunks"),
        MetricKind.COUNTER,
        _C.KNOWLEDGE,
        "Embeddings generated",
        "chunks",
    ),
    "deployments.started": (
        ("deployment.started",),
        _count,
        MetricKind.COUNTER,
        _C.DEPLOYMENT,
        "Deployments started",
        "count",
    ),
    "deployments.completed": (
        ("deployment.completed",),
        _count,
        MetricKind.COUNTER,
        _C.DEPLOYMENT,
        "Deployments completed",
        "count",
    ),
    "deployments.failed": (
        ("deployment.failed",),
        _count,
        MetricKind.COUNTER,
        _C.DEPLOYMENT,
        "Deployments failed",
        "count",
    ),
    "deployments.rolled_back": (
        ("deployment.rolled_back",),
        _count,
        MetricKind.COUNTER,
        _C.DEPLOYMENT,
        "Deployments rolled back",
        "count",
    ),
    "providers.failed": (
        ("provider.failed",),
        _count,
        MetricKind.COUNTER,
        _C.PROVIDER,
        "Provider failures",
        "count",
    ),
}

# Estimated-cost rate table (USD; **not billing** — a directional estimate only).
_COST_RATES = {
    "token": 0.000002,  # per token
    "embedding": 0.0000001,  # per chunk embedded
    "vector_search": 0.00005,  # per retrieval
    "tool": 0.0001,  # per tool call
    "deployment": 0.01,  # per completed deployment
}

_ERROR_SUFFIXES = (".failed", ".denied", ".rejected", ".health_failed")
_WARN_SUFFIXES = (
    ".archived",
    ".suspended",
    ".cancelled",
    ".expired",
    ".unpublished",
    ".rolled_back",
    ".rolling_back",
    ".disabled",
    ".deleted",
    ".removed",
)

_CATEGORY_PREFIX = (
    ("deployment.", _C.DEPLOYMENT),
    ("knowledge.", _C.KNOWLEDGE),
    ("tool.", _C.TOOL),
    ("provider.", _C.PROVIDER),
    ("streaming.", _C.RUNTIME),
    ("execution.", _C.RUNTIME),
    ("session.", _C.RUNTIME),
    ("conversation.", _C.RUNTIME),
    ("access.", _C.SECURITY),
    ("role.", _C.SECURITY),
    ("member.", _C.SECURITY),
)


def _level_for(event_type: str) -> LogLevel:
    if any(event_type.endswith(s) for s in _ERROR_SUFFIXES):
        return LogLevel.ERROR
    if any(event_type.endswith(s) for s in _WARN_SUFFIXES):
        return LogLevel.WARNING
    return LogLevel.INFO


def _category_for(event_type: str) -> LogCategory:
    for prefix, cat in _CATEGORY_PREFIX:
        if event_type.startswith(prefix):
            return cat
    return LogCategory.PLATFORM


def _is_error(event_type: str) -> bool:
    return any(event_type.endswith(s) for s in _ERROR_SUFFIXES)


def _in_scope(rec: AuditRecord, workspace_id: str | None, project_id: str | None) -> bool:
    if workspace_id is not None and rec.workspace_id != workspace_id:
        return False
    if project_id is not None and rec.project_id != project_id:
        return False
    return True


def _in_window(rec: AuditRecord, since: float | None, until: float | None) -> bool:
    if since is not None and rec.ts < since:
        return False
    if until is not None and rec.ts > until:
        return False
    return True


def _humanize(rec: AuditRecord) -> str:
    noun = rec.type.replace(".", " ").replace("_", " ")
    return f"{noun}" + (f" · {rec.resource}" if rec.resource else "")


# ── metrics ─────────────────────────────────────────────────────────────────────────


class MetricsRegistry:
    """The catalog of known metric definitions (pure metadata)."""

    def __init__(self) -> None:
        self._defs = {
            key: MetricDefinition(
                key=key, kind=spec[2], category=spec[3].value, description=spec[4], unit=spec[5]
            )
            for key, spec in _METRIC_CATALOG.items()
        }

    def all(self) -> tuple[MetricDefinition, ...]:
        return tuple(self._defs.values())

    def keys(self) -> tuple[str, ...]:
        return tuple(self._defs.keys())

    def get(self, key: str) -> MetricDefinition | None:
        return self._defs.get(key)


class MetricsCollector:
    """Folds a stream of audit records into metric snapshots/series (pure, deterministic)."""

    def snapshot(self, key: str, records: Iterable[AuditRecord]) -> MetricSnapshot:
        types, value_fn, kind, *_ = _METRIC_CATALOG[key]
        values = [value_fn(r) for r in records if r.type in types]
        count = len(values)
        total = sum(values)
        return MetricSnapshot(
            key=key,
            kind=kind,
            count=count,
            total=total,
            minimum=min(values) if values else 0.0,
            maximum=max(values) if values else 0.0,
            average=(total / count) if count else 0.0,
        )

    def series(
        self, key: str, records: Iterable[AuditRecord], *, bucket_seconds: float
    ) -> MetricSeries:
        types, value_fn, *_ = _METRIC_CATALOG[key]
        buckets: dict[float, float] = {}
        for r in records:
            if r.type in types:
                b = (r.ts // bucket_seconds) * bucket_seconds
                buckets[b] = buckets.get(b, 0.0) + value_fn(r)
        points = tuple(MetricPoint(ts=b, value=v) for b, v in sorted(buckets.items()))
        return MetricSeries(key=key, points=points, bucket_seconds=bucket_seconds)


class _ReaderService:
    """Shared base: pulls the org's audit stream (append-order) and filters to scope/window."""

    def __init__(self, reader: AuditReader, clock: Clock) -> None:
        self._reader = reader
        self._clock = clock

    def _records(
        self, org_id, *, workspace_id=None, project_id=None, since=None, until=None
    ) -> list[AuditRecord]:
        return [
            r
            for r in self._reader.stream(org_id)
            if _in_scope(r, workspace_id, project_id) and _in_window(r, since, until)
        ]


class MetricsService(_ReaderService):
    def __init__(self, reader, clock) -> None:
        super().__init__(reader, clock)
        self.registry = MetricsRegistry()
        self.collector = MetricsCollector()

    def report(
        self, org_id, *, workspace_id=None, project_id=None, keys=None, since=None, until=None
    ) -> MetricReport:
        recs = self._records(
            org_id, workspace_id=workspace_id, project_id=project_id, since=since, until=until
        )
        keys = keys or self.registry.keys()
        snaps = tuple(self.collector.snapshot(k, recs) for k in keys if self.registry.get(k))
        return MetricReport(
            scope=project_id or workspace_id or org_id,
            generated_at=self._clock.now(),
            snapshots=snaps,
        )

    def aggregation(self, org_id, *, workspace_id=None, project_id=None) -> MetricAggregation:
        rep = self.report(org_id, workspace_id=workspace_id, project_id=project_id)
        return MetricAggregation(scope=rep.scope, snapshots=rep.snapshots)

    def series(
        self,
        org_id,
        metric_key,
        *,
        workspace_id=None,
        project_id=None,
        bucket_seconds=60.0,
        since=None,
        until=None,
    ) -> MetricSeries:
        if self.registry.get(metric_key) is None:
            raise NotFoundError(f"unknown metric {metric_key!r}")
        recs = self._records(
            org_id, workspace_id=workspace_id, project_id=project_id, since=since, until=until
        )
        return self.collector.series(metric_key, recs, bucket_seconds=bucket_seconds)

    def value(self, org_id, metric_key, *, workspace_id=None, project_id=None, since=None) -> float:
        return self.collector.snapshot(
            metric_key,
            self._records(org_id, workspace_id=workspace_id, project_id=project_id, since=since),
        ).total


# ── logging ─────────────────────────────────────────────────────────────────────────


class LoggingService(_ReaderService):
    """Structured, append-only, immutable logs — a projection of the audit stream (each record is
    one log line; the audit store *is* the log store — no duplication)."""

    def _entry(self, rec: AuditRecord) -> LogEntry:
        from .observability_domain import LogContext

        return LogEntry(
            seq=rec.seq,
            ts=rec.ts,
            level=_level_for(rec.type),
            category=_category_for(rec.type),
            event_type=rec.type,
            message=_humanize(rec),
            context=LogContext(
                org_id=rec.org_id,
                workspace_id=rec.workspace_id,
                project_id=rec.project_id,
                actor=rec.actor,
                resource=rec.resource,
            ),
            data=rec.data,
        )

    def query(self, q: LogQuery) -> LogReport:
        recs = [
            r
            for r in self._reader.stream(q.org_id)
            if _in_scope(r, q.workspace_id, q.project_id) and _in_window(r, q.since, q.until)
        ]
        entries = [self._entry(r) for r in recs]
        if q.level is not None:
            entries = [e for e in entries if e.level is q.level]
        if q.category is not None:
            entries = [e for e in entries if e.category is q.category]
        if q.actor is not None:
            entries = [e for e in entries if e.context.actor == q.actor]
        entries.sort(key=lambda e: e.seq, reverse=True)
        total = len(entries)
        window = entries[q.offset : q.offset + q.limit] if q.limit > 0 else entries[q.offset :]
        return LogReport(entries=tuple(window), total=total, limit=q.limit, offset=q.offset)


# ── tracing (distributed model — no OpenTelemetry) ─────────────────────────────────


class TracingService(_ReaderService):
    """Groups correlated events into distributed traces. Correlation key = the session/deployment/
    resource an event follows; each event becomes a span; the trace is the ordered span set."""

    @staticmethod
    def _correlation(rec: AuditRecord) -> str:
        return (
            rec.data.get("session_id")
            or rec.data.get("deployment_id")
            or rec.data.get("collection_id")
            or rec.resource
            or rec.id
        )

    @staticmethod
    def _trace_id(key: str) -> str:
        import hashlib

        return "tr_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]

    def _build(self, records: list[AuditRecord]) -> list[Trace]:
        groups: dict[str, list[AuditRecord]] = {}
        for r in records:
            groups.setdefault(self._correlation(r), []).append(r)
        traces: list[Trace] = []
        for key, recs in groups.items():
            recs.sort(key=lambda r: (r.ts, r.seq))
            trace_id = self._trace_id(key)
            spans: list[Span] = []
            parent: str | None = None
            for i, r in enumerate(recs):
                end_ts = recs[i + 1].ts if i + 1 < len(recs) else r.ts
                span = Span(
                    context=SpanContext(trace_id=trace_id, span_id=r.id, parent_span_id=parent),
                    name=r.type,
                    start_ts=r.ts,
                    end_ts=end_ts,
                    status="error" if _is_error(r.type) else "ok",
                    attributes={"actor": r.actor, "resource": r.resource},
                )
                spans.append(span)
                parent = r.id
            status = "error" if any(s.status == "error" for s in spans) else "ok"
            traces.append(
                Trace(
                    context=TraceContext(trace_id=trace_id, correlation_key=key),
                    spans=tuple(spans),
                    start_ts=recs[0].ts,
                    end_ts=recs[-1].ts,
                    status=status,
                )
            )
        traces.sort(key=lambda t: t.start_ts, reverse=True)
        return traces

    def traces(self, org_id, *, workspace_id=None, project_id=None, limit=50) -> TraceReport:
        recs = self._records(org_id, workspace_id=workspace_id, project_id=project_id)
        return TraceReport(traces=tuple(self._build(recs)[:limit]))

    def trace(self, org_id, trace_id, *, workspace_id=None, project_id=None) -> Trace:
        recs = self._records(org_id, workspace_id=workspace_id, project_id=project_id)
        for t in self._build(recs):
            if t.context.trace_id == trace_id:
                return t
        raise NotFoundError(f"trace {trace_id!r} not found")


# ── health ──────────────────────────────────────────────────────────────────────────

_HEALTH_COMPONENTS = {
    "runtime": (
        ("execution.completed", "conversation.turn_completed"),
        ("execution.failed", "execution.failed_real"),
    ),
    "deployment": (
        ("deployment.completed",),
        ("deployment.failed", "deployment.health_failed", "deployment.rolled_back"),
    ),
    "provider": (("provider.selected", "execution.completed"), ("provider.failed",)),
    "tool": (("tool.executed",), ("tool.failed",)),
    "knowledge": (("knowledge.retrieved", "knowledge.indexed"), ()),
}


def _status_for(total: int, failures: int) -> HealthStatus:
    if total == 0:
        return HealthStatus.UNKNOWN
    rate = failures / total
    if rate < 0.2:
        return HealthStatus.HEALTHY
    if rate < 0.5:
        return HealthStatus.DEGRADED
    return HealthStatus.UNHEALTHY


_STATUS_RANK = {
    HealthStatus.UNHEALTHY: 3,
    HealthStatus.DEGRADED: 2,
    HealthStatus.UNKNOWN: 1,
    HealthStatus.HEALTHY: 0,
}


class HealthMonitor(_ReaderService):
    """Health derived from recent success/failure ratios + the Deployment/Runtime health events it
    *consumes* — it never probes or executes anything."""

    def _snapshot(self, component, records, at) -> HealthSnapshot:
        ok_types, fail_types = _HEALTH_COMPONENTS[component]
        successes = sum(1 for r in records if r.type in ok_types)
        failures = sum(1 for r in records if r.type in fail_types)
        total = successes + failures
        return HealthSnapshot(
            component=component,
            status=_status_for(total, failures),
            total=total,
            failures=failures,
            failure_rate=(failures / total) if total else 0.0,
            at=at,
            detail=f"{successes} ok / {failures} failed",
        )

    def snapshot(
        self, org_id, *, workspace_id=None, project_id=None, since=None
    ) -> HealthAggregation:
        recs = self._records(org_id, workspace_id=workspace_id, project_id=project_id, since=since)
        at = self._clock.now()
        comps = tuple(self._snapshot(c, recs, at) for c in _HEALTH_COMPONENTS)
        # Overall = the worst component *that has signal*; a no-data (UNKNOWN) component never drags
        # a healthy platform down. All-unknown → UNKNOWN.
        with_signal = [s for s in comps if s.total > 0]
        if with_signal:
            worst = max(with_signal, key=lambda s: _STATUS_RANK[s.status]).status
        else:
            worst = HealthStatus.UNKNOWN
        return HealthAggregation(status=worst, components=comps, at=at)

    def history(
        self,
        org_id,
        component,
        *,
        workspace_id=None,
        project_id=None,
        buckets=6,
        bucket_seconds=3600.0,
    ) -> HealthHistory:
        if component not in _HEALTH_COMPONENTS:
            raise NotFoundError(f"unknown health component {component!r}")
        recs = self._records(org_id, workspace_id=workspace_id, project_id=project_id)
        now = self._clock.now()
        snaps = []
        for i in range(buckets):
            hi = now - i * bucket_seconds
            lo = hi - bucket_seconds
            window = [r for r in recs if lo <= r.ts <= hi]
            snaps.append(self._snapshot(component, window, hi))
        return HealthHistory(component=component, snapshots=tuple(reversed(snaps)))


# ── usage + cost + performance ─────────────────────────────────────────────────────


class UsageService(_ReaderService):
    """Usage aggregates across every platform surface (conversation/provider/knowledge/tool/
    deployment + org/workspace/project/agent/user breakdowns). Pure counting — no billing."""

    def summary(self, org_id, *, workspace_id=None, project_id=None, since=None) -> dict:
        recs = self._records(org_id, workspace_id=workspace_id, project_id=project_id, since=since)
        col = MetricsCollector()

        def total(key):
            return col.snapshot(key, recs).total

        by_provider: dict[str, int] = {}
        by_actor: dict[str, int] = {}
        by_project: dict[str, int] = {}
        for r in recs:
            if r.type == "execution.completed":
                by_provider[r.data.get("provider", "unknown")] = (
                    by_provider.get(r.data.get("provider", "unknown"), 0) + 1
                )
                by_actor[r.actor] = by_actor.get(r.actor, 0) + 1
                if r.project_id:
                    by_project[r.project_id] = by_project.get(r.project_id, 0) + 1
        return {
            "scope": project_id or workspace_id or org_id,
            "conversations": int(total("conversations.completed")),
            "tokens": int(total("tokens.total")),
            "tools": int(total("tools.executed")),
            "knowledge_retrievals": int(total("knowledge.retrieved")),
            "embeddings": int(total("knowledge.embeddings")),
            "deployments": int(total("deployments.completed")),
            "by_provider": by_provider,
            "by_user": by_actor,
            "by_project": by_project,
        }


class CostService(_ReaderService):
    """Estimated cost (directional, **not billing**) from usage × a rate table, with a provider
    breakdown. No invoices, no ledgers — an estimate the customer can see."""

    def estimate(self, org_id, *, workspace_id=None, project_id=None, since=None) -> dict:
        recs = self._records(org_id, workspace_id=workspace_id, project_id=project_id, since=since)
        col = MetricsCollector()
        tokens = col.snapshot("tokens.total", recs).total
        embeddings = col.snapshot("knowledge.embeddings", recs).total
        retrievals = col.snapshot("knowledge.retrieved", recs).total
        tools = col.snapshot("tools.executed", recs).total
        deployments = col.snapshot("deployments.completed", recs).total
        token_cost = tokens * _COST_RATES["token"]
        embedding_cost = embeddings * _COST_RATES["embedding"]
        vector_cost = retrievals * _COST_RATES["vector_search"]
        tool_cost = tools * _COST_RATES["tool"]
        deploy_cost = deployments * _COST_RATES["deployment"]
        provider_tokens: dict[str, float] = {}
        for r in recs:
            if r.type == "execution.completed":
                p = r.data.get("provider", "unknown")
                provider_tokens[p] = provider_tokens.get(p, 0.0) + float(r.data.get("tokens", 0))
        provider_breakdown = {
            p: round(t * _COST_RATES["token"], 6) for p, t in provider_tokens.items()
        }
        total = token_cost + embedding_cost + vector_cost + tool_cost + deploy_cost
        return {
            "scope": project_id or workspace_id or org_id,
            "token_cost": round(token_cost, 6),
            "embedding_cost": round(embedding_cost, 6),
            "vector_search_cost": round(vector_cost, 6),
            "tool_cost": round(tool_cost, 6),
            "deployment_cost": round(deploy_cost, 6),
            "estimated_total": round(total, 6),
            "currency": "USD",
            "provider_breakdown": provider_breakdown,
            "note": "directional estimate — not billing",
        }


class PerformanceService(_ReaderService):
    """Latency / duration / throughput / success-failure / availability from the event stream."""

    def summary(self, org_id, *, workspace_id=None, project_id=None, since=None) -> dict:
        recs = self._records(org_id, workspace_id=workspace_id, project_id=project_id, since=since)
        col = MetricsCollector()
        latency = col.snapshot("latency.ms", recs)
        completed = col.snapshot("conversations.completed", recs).total
        failed = col.snapshot("conversations.failed", recs).total
        dep_failed = col.snapshot("deployments.failed", recs).total
        rolled_back = col.snapshot("deployments.rolled_back", recs).total
        attempts = completed + failed
        success_rate = (completed / attempts) if attempts else 1.0
        return {
            "scope": project_id or workspace_id or org_id,
            "avg_latency_ms": round(latency.average, 3),
            "max_latency_ms": round(latency.maximum, 3),
            "executions": int(attempts),
            "success_rate": round(success_rate, 4),
            "failure_rate": round(1.0 - success_rate, 4),
            "throughput": int(attempts),
            "availability": round(success_rate, 4),
            "retry_count": int(rolled_back),
            "deployment_failures": int(dep_failed),
        }


# ── alerting ────────────────────────────────────────────────────────────────────────

_COMPARATORS = {
    AlertComparator.GT: lambda v, t: v > t,
    AlertComparator.GTE: lambda v, t: v >= t,
    AlertComparator.LT: lambda v, t: v < t,
    AlertComparator.LTE: lambda v, t: v <= t,
}


class AlertEvaluator:
    """Evaluates one rule against a metric value → an `AlertEvent` (pure; verdict-only)."""

    def evaluate(self, rule: AlertRule, value: float, at: float) -> AlertEvent:
        firing = _COMPARATORS[rule.comparator](value, rule.threshold)
        return AlertEvent(
            rule_id=rule.id,
            name=rule.name,
            state=AlertState.FIRING if firing else AlertState.OK,
            value=value,
            threshold=rule.threshold,
            comparator=rule.comparator,
            severity=rule.severity,
            at=at,
        )


class AlertManager(_Base):
    """CRUD of alert rules (the only durable Observability state) + on-demand evaluation against the
    metric projection. No notifications (no email/SMS), no background poller — evaluation is a
    read."""

    def __init__(self, *, rules: AlertRuleRepository, metrics: MetricsService, **base) -> None:
        super().__init__(**base)
        self._rules = rules
        self._metrics = metrics
        self._alert_evaluator = AlertEvaluator()

    def create_rule(
        self,
        actor,
        org_id,
        *,
        name,
        metric_key,
        comparator,
        threshold,
        window_seconds=3600.0,
        severity="warning",
        project_id=None,
    ) -> AlertRule:
        self._require(actor, Permission.ORG_MANAGE, org_id=org_id)
        if self._metrics.registry.get(metric_key) is None:
            raise NotFoundError(f"unknown metric {metric_key!r}")
        if not name.strip():
            raise ConflictError("alert rule name must be non-empty")
        rule = AlertRule(
            id=self._ids.new(PREFIX_ALERT_RULE),
            org_id=org_id,
            name=name,
            metric_key=metric_key,
            comparator=AlertComparator(comparator),
            threshold=float(threshold),
            window_seconds=float(window_seconds),
            severity=AlertSeverity(severity),
            project_id=project_id,
            created_by=actor,
            created_at=self._clock.now(),
            updated_at=self._clock.now(),
        )
        self._rules.add(rule)
        self._emit_rule("observability.alert_rule_created", actor, rule)
        return rule

    def update_rule(self, actor, org_id, rule_id, **changes) -> AlertRule:
        self._require(actor, Permission.ORG_MANAGE, org_id=org_id)
        rule = self._load(org_id, rule_id)
        if "comparator" in changes and changes["comparator"] is not None:
            changes["comparator"] = AlertComparator(changes["comparator"])
        if "severity" in changes and changes["severity"] is not None:
            changes["severity"] = AlertSeverity(changes["severity"])
        updated = self._rules.update(rule.with_updates(updated_at=self._clock.now(), **changes))
        self._emit_rule("observability.alert_rule_updated", actor, updated)
        return updated

    def delete_rule(self, actor, org_id, rule_id) -> AlertRule:
        self._require(actor, Permission.ORG_MANAGE, org_id=org_id)
        rule = self._load(org_id, rule_id)
        self._rules.delete(org_id, rule_id)
        self._emit_rule("observability.alert_rule_deleted", actor, rule)
        return rule

    def list_rules(self, org_id) -> tuple[AlertRule, ...]:
        return self._rules.list(org_id)

    def _load(self, org_id, rule_id) -> AlertRule:
        rule = self._rules.get(org_id, rule_id)
        if rule is None:
            raise NotFoundError(f"alert rule {rule_id!r} not found")
        return rule

    def evaluate(self, org_id, *, project_id=None) -> tuple[AlertEvent, ...]:
        """Evaluate every enabled rule against its metric over its window (on-demand read)."""
        now = self._clock.now()
        out = []
        for rule in self._rules.list(org_id):
            if not rule.enabled:
                continue
            value = self._metrics.value(
                org_id,
                rule.metric_key,
                project_id=rule.project_id or project_id,
                since=now - rule.window_seconds,
            )
            out.append(self._alert_evaluator.evaluate(rule, value, now))
        return tuple(out)

    def history(self, org_id, rule_id, *, buckets=6, bucket_seconds=3600.0) -> AlertHistory:
        rule = self._load(org_id, rule_id)
        now = self._clock.now()
        events = []
        for i in range(buckets):
            hi = now - i * bucket_seconds
            value = self._metrics.value(
                org_id,
                rule.metric_key,
                project_id=rule.project_id,
                since=hi - rule.window_seconds,
            )
            events.append(self._alert_evaluator.evaluate(rule, value, hi))
        return AlertHistory(rule_id=rule_id, events=tuple(reversed(events)))

    def _emit_rule(self, event_type, actor, rule: AlertRule) -> None:
        from .events import FoundationEventType

        self._emit(
            FoundationEventType(event_type),
            org_id=rule.org_id,
            actor=actor,
            resource=rule.id,
            project_id=rule.project_id,
            metric_key=rule.metric_key,
            name=rule.name,
        )


# ── dashboards ──────────────────────────────────────────────────────────────────────


class DashboardService:
    """Per-scope dashboard read models composing metrics + usage + cost + health + alerts. Pure
    composition over the other read services — Studio renders these; it owns none of the state."""

    def __init__(
        self,
        *,
        metrics: MetricsService,
        usage: UsageService,
        cost: CostService,
        performance: PerformanceService,
        health: HealthMonitor,
        alerts: AlertManager,
    ) -> None:
        self._metrics = metrics
        self._usage = usage
        self._cost = cost
        self._perf = performance
        self._health = health
        self._alerts = alerts

    def _base(self, org_id, *, workspace_id=None, project_id=None) -> dict:
        return {
            "scope": project_id or workspace_id or org_id,
            "usage": self._usage.summary(org_id, workspace_id=workspace_id, project_id=project_id),
            "cost": self._cost.estimate(org_id, workspace_id=workspace_id, project_id=project_id),
            "performance": self._perf.summary(
                org_id, workspace_id=workspace_id, project_id=project_id
            ),
            "health": self._health.snapshot(
                org_id, workspace_id=workspace_id, project_id=project_id
            ).as_dict(),
            "metrics": self._metrics.aggregation(
                org_id, workspace_id=workspace_id, project_id=project_id
            ).as_dict(),
        }

    def organization(self, org_id) -> dict:
        d = self._base(org_id)
        d["alerts"] = [e.as_dict() for e in self._alerts.evaluate(org_id)]
        d["kind"] = "organization"
        return d

    def workspace(self, org_id, workspace_id) -> dict:
        d = self._base(org_id, workspace_id=workspace_id)
        d["kind"] = "workspace"
        return d

    def project(self, org_id, project_id) -> dict:
        d = self._base(org_id, project_id=project_id)
        d["kind"] = "project"
        return d

    def agent(self, org_id, project_id, agent_id) -> dict:
        d = self._base(org_id, project_id=project_id)
        d["kind"] = "agent"
        d["agent_id"] = agent_id
        return d

    def deployment(self, org_id, project_id=None) -> dict:
        m = self._metrics.aggregation(org_id, project_id=project_id).as_dict()["metrics"]
        return {
            "kind": "deployment",
            "scope": project_id or org_id,
            "metrics": {k: v for k, v in m.items() if k.startswith("deployments.")},
            "health": self._health.snapshot(org_id, project_id=project_id).as_dict(),
        }

    def knowledge(self, org_id, project_id=None) -> dict:
        m = self._metrics.aggregation(org_id, project_id=project_id).as_dict()["metrics"]
        return {
            "kind": "knowledge",
            "scope": project_id or org_id,
            "metrics": {k: v for k, v in m.items() if k.startswith("knowledge.")},
        }

    def provider(self, org_id) -> dict:
        return {
            "kind": "provider",
            "scope": org_id,
            "usage": self._usage.summary(org_id)["by_provider"],
            "cost": self._cost.estimate(org_id)["provider_breakdown"],
            "failures": self._metrics.value(org_id, "providers.failed"),
        }

    def system(self, org_id) -> dict:
        return {
            "kind": "system",
            "scope": org_id,
            "health": self._health.snapshot(org_id).as_dict(),
            "performance": self._perf.summary(org_id),
            "alerts": [e.as_dict() for e in self._alerts.evaluate(org_id)],
        }


# ── facade ──────────────────────────────────────────────────────────────────────────


class ObservabilityService:
    """The assembled Observability read surface — the seam the control plane binds to. Groups the
    metric/log/trace/health/usage/cost/performance/dashboard read services (all over the injected
    `AuditReader`) plus the `AlertManager` (durable rules). A pure passive consumer of events."""

    def __init__(self, *, reader: AuditReader, alert_rules: AlertRuleRepository, **base) -> None:
        clock: Clock = base["clock"]
        self.metrics = MetricsService(reader, clock)
        self.logs = LoggingService(reader, clock)
        self.traces = TracingService(reader, clock)
        self.health = HealthMonitor(reader, clock)
        self.usage = UsageService(reader, clock)
        self.cost = CostService(reader, clock)
        self.performance = PerformanceService(reader, clock)
        self.alerts = AlertManager(rules=alert_rules, metrics=self.metrics, **base)
        self.dashboards = DashboardService(
            metrics=self.metrics,
            usage=self.usage,
            cost=self.cost,
            performance=self.performance,
            health=self.health,
            alerts=self.alerts,
        )

    def metric_definitions(self) -> tuple[MetricDefinition, ...]:
        return self.metrics.registry.all()
