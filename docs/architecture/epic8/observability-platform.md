# VoxVoice — Observability Platform (Epic 8, M8.17)

| | |
|---|---|
| **Document** | `docs/architecture/epic8/observability-platform.md` |
| **Status** | 🟢 **Implemented (M8.17)** — the **Observability Platform**: a **passive read-model** over the durable Audit stream (M8.9). Metrics / logs / traces / health / usage / cost / dashboards are *projections*; only alert rules are durable config. |
| **Date** | 2026-08-01 |
| **Scope** | Metrics (registry/collector/service/snapshot/series/aggregation/report); structured logs (level/category/context/query/report); distributed traces (trace/span/context — **no OpenTelemetry**); alerting (rule/definition/state/event/history/manager/evaluator — **no notifications**); health (snapshot/history/aggregation — consumes Deployment/Runtime health, never probes); usage + estimated cost (**no billing**); performance; the eight dashboard read models; API; Studio. **No polling, no direct Runtime/Deployment access, no duplicate audit events, no autoscaling.** |
| **Authority** | Additive on the frozen Epics 5/6/7 and the M8.2–M8.16 stack. **No new ADR, no redesign.** Audit Platform (M8.9) / Conversation Runtime / Provider Binding / Knowledge Tool / Deployment Actuator / Playground / Product Runtime reused exactly and unchanged. All prior 1733 tests still green. |
| **Related** | [Audit & Activity Platform](audit-activity-platform.md) · [Deployment Actuator](deployment-actuator.md) · [Knowledge Tool](knowledge-tool.md) · [Provider Adapter Binding](provider-adapter-binding.md) · [Conversation Runtime](conversation-runtime.md) · Constitution §A5/§A9/§A11.2 · [INDEX](../../INDEX.md) |

## Problem & approach

Customers need to *see* what the platform is doing — conversations, deployments, provider/knowledge/
tool usage, traces, logs, metrics, health, alerts, usage & cost. Every one of those facts is
**already emitted** as a typed event on the durable Audit stream (§A5, M8.9). So Observability is
built as a **passive consumer**: a read-model that *projects* the append-only audit stream on read.
It **owns visibility and nothing else** — no execution, no orchestration, no deployment. Golden
rule: Runtime owns execution, Deployment owns deployment, Knowledge owns retrieval, Tool Framework
owns tools — **Observability owns observation**; no monitoring logic enters any of those layers.

**No new durable state except alert rules.** Metrics/logs/traces/health/usage/cost/dashboards hold
no rows — they are computed from `AuditReader.stream(org_id)` each read. The customer's **alert
rules** are the only durable Observability state (one table), and they are **evaluated on demand**
against the metric projection — there is no background poller (§no-polling).

## Metrics architecture

- **`MetricsRegistry`** — the catalog of `MetricDefinition`s (key/kind/category/unit).
- **Metric catalog (`_METRIC_CATALOG`)** — the pure derivation table: each metric key maps to the
  event types it is derived from + a value extractor (count, or a data field like `tokens`/
  `latency_ms`/`chunks`). 15 metrics across conversations / tokens / latency / tools / knowledge /
  deployments / providers.
- **`MetricsCollector`** — folds a stream of audit records into a `MetricSnapshot` (count/total/min/
  max/avg) or a time-bucketed `MetricSeries` (pure, deterministic).
- **`MetricsService`** — `report` / `aggregation` / `series` / `value` over the injected
  `AuditReader`, scoped by org/workspace/project + time window. `MetricReport`/`MetricAggregation`
  are the dashboard tile sources.

## Logging architecture

Each audit record **is** one structured, immutable `LogEntry` — the append-only audit store *is*
the log store (no duplication). `LoggingService` projects records to `LogEntry`s, deriving
`LogLevel` (an `*.failed`/`*.denied` event → ERROR; `*.archived`/`*.cancelled` → WARNING; else INFO)
and `LogCategory` (by event prefix: deployment/knowledge/tool/provider/runtime/security/platform).
`LogQuery` filters by level/category/actor/scope/time; `LogReport` paginates (most-recent-first).

## Tracing architecture

A **distributed trace model with no OpenTelemetry dependency**. `TracingService` groups correlated
events (by `session_id` / `deployment_id` / `collection_id` / resource) into a `Trace`; each event
becomes a `Span` (ordered by ts, parent = previous span, `error` status for failure events) under a
content-addressed `trace_id`. `Trace`/`Span` carry `SpanContext`/`TraceContext`; `TraceReport`
summarises. The Studio Trace Viewer renders these; a single conversation's execution + tool spans
group into one trace.

## Alerting architecture

The **only durable Observability state**. An `AlertRule` = {metric, comparator (gt/gte/lt/lte),
threshold, window, severity, optional project scope}. `AlertEvaluator` compares a metric value to
the threshold → an `AlertEvent` (`ok`/`firing`) — pure, verdict-only. `AlertManager` owns rule CRUD
(authorized via `ORG_MANAGE`, separation from reads) and **on-demand** `evaluate` (computes each
rule's metric over its window from the stream) + `history` (recomputed per window). **No email/SMS,
no background poller** — `AlertEvent`/`AlertHistory` are computed on read, never stored (passive).

## Health architecture

`HealthMonitor` derives per-component health (`runtime`/`deployment`/`provider`/`tool`/`knowledge`)
from the recent success/failure event ratio and the Deployment/Runtime health events it
**consumes** — it never probes or executes. Thresholds: <20% failures → HEALTHY, <50% → DEGRADED,
else UNHEALTHY; no signal → UNKNOWN. The overall `HealthAggregation` is the worst component **that
has signal** (a no-data component never drags a healthy platform to UNKNOWN). `HealthHistory`
buckets snapshots over time.

## Usage / cost / performance

- **`UsageService`** — conversation/provider/knowledge/tool/deployment counts + org/workspace/
  project/agent/user breakdowns (pure counting).
- **`CostService`** — an **estimated** (directional, **not billing**) cost from usage × a rate table
  (token/embedding/vector-search/tool/deployment) with a provider breakdown. No invoices, no ledger.
- **`PerformanceService`** — latency (avg/max), executions, success/failure rate, throughput,
  availability, retry/rollback counts.

## Dashboard architecture

`DashboardService` composes the read services into eight per-scope read models — **Organization /
Workspace / Project / Agent / Deployment / Knowledge / Provider / System** — each a dict Studio
renders. Studio owns none of the state (ADR-0021).

## Layer changes (every Foundation change justified)

- **Foundation (additive, required):** `observability_domain.py` (the projection value objects +
  enums), `observability.py` (the metric catalog + `MetricsRegistry`/`MetricsCollector`/
  `MetricsService`/`LoggingService`/`TracingService`/`HealthMonitor`/`UsageService`/`CostService`/
  `PerformanceService`/`AlertEvaluator`/`AlertManager`/`DashboardService` + the `ObservabilityService`
  facade); an `AlertRuleRepository` (ABC + in-memory); additive `FoundationEventType.observability.*`
  (alert-rule config facts only) + id prefix `alr`; the factory gains a **defaulted** alert-rule
  repo and builds the service over the existing `audit_reader`. **Why required:** the read services
  need the audit-stream seam + the metric catalog; alert rules need a durable seam. **Why safe:**
  additive/defaulted; **all read services are pure projections over the existing `AuditReader`** (no
  new writer, no new event on read); every prior service is untouched; **no frozen-Epic import** (the
  trace-id hash uses `hashlib`, not `product.ids` — boundary tests green). All 1733 prior tests pass.
- **Persistence (additive):** `cp_alert_rule` (the **only** Observability table) + repo + **migration
  0012**; composition threads the repo. Metrics/logs/traces/health add **no tables** (projections).
- **Application (additive):** `observability.py` — the read queries + the three alert-rule commands.
- **API (additive):** the `…/observability/…` router (org-scoped, with workspace/project narrowing).
- **Studio (additive):** the ten screens below.

## UI screens (Studio)

Metrics Dashboard · Trace Viewer · Log Explorer · Health Dashboard · Usage Dashboard · Cost
Dashboard · Alert Center · Provider Analytics · Knowledge Analytics · Deployment Analytics.

## API surface (all additive, org-scoped)

```
GET  …/observability/metrics                     metric report (scope + window)
GET  …/observability/metrics/definitions         metric catalog
GET  …/observability/metrics/{key}/series        time-bucketed series
GET  …/observability/logs                         structured log explorer (level/category filters)
GET  …/observability/traces[/{trace_id}]          distributed traces / one trace
GET  …/observability/health[/{component}/history] health snapshot / component history
GET  …/observability/usage | /cost | /performance usage / estimated cost / performance
GET  …/observability/dashboards/{kind}            org/workspace/project/agent/deployment/knowledge/provider/system
GET/POST/PATCH/DELETE …/observability/alerts[/{id}]  alert-rule config
GET  …/observability/alerts/evaluate              on-demand evaluation
GET  …/observability/alerts/{id}/history          recomputed alert history
```

## Testing

`tests/test_observability.py` — 14 tests: metric derivation + scope/series; log level/category
projection; distributed-trace grouping; health aggregation (ignores no-signal components); usage +
estimated cost; performance; the alert evaluator; the Control Plane API + Studio **Definition-of-
Done** (observe conversations/deployments/providers/knowledge/tools + view traces/logs/metrics/
health/alerts/usage/cost, all 8 dashboards); **audit-non-duplication** (reading every projection
adds zero events); alert-rule audited; viewer-can-read-not-configure; scope isolation; Studio
screens served. **14/14 pass; 1747/1747 repository-wide; ruff clean; frozen Epics 5/6/7 untouched;
every prior M8.2–M8.16 test green.**

## Architecture compliance

- **Passive consumer:** every metric/log/trace/health/usage/cost/dashboard is a projection computed
  on read from the durable audit stream — **no polling, no direct Runtime/Deployment access, no new
  event on read** (verified by `test_observability_does_not_duplicate_audit_events`). Observability
  reuses the Audit Platform; it does not duplicate audit events (§Audit).
- **Ownership boundary:** Observability owns observation; no monitoring logic entered Runtime,
  Deployment, Provider Binding, Knowledge, or the Tool Framework (all reused unchanged). Alert-rule
  config is the only write; metrics/logs/traces/health hold no state.
- **Boundary preserved:** the Foundation imports no frozen Epic (the trace hash uses `hashlib`); the
  read services sit behind the `AuditReader` seam; org+workspace+project isolation (§A11.2) on every
  query. Reads authorize by org scope (a viewer may observe); alert-rule writes require `ORG_MANAGE`.
- No new ADR, no redesign. Frozen Epics 5/6/7 byte-for-byte unchanged.

## Definition of Done

A customer can observe conversations, deployments, provider usage, knowledge usage, and tool usage,
and view traces, logs, metrics, health, alerts, usage analytics, and estimated costs — using only
the Studio UI + Control Plane API. Proven by `test_definition_of_done_end_to_end`.

## Known limitations / later hardening

- **Projections computed per read** over the org's audit stream (deterministic, offline). At scale
  this is where a materialised metric store / rollup + a streaming projector plug in behind the same
  read services — no domain change.
- **Estimated cost only** (a rate table) — **not billing**; a real pricing/billing engine is out of
  scope.
- **On-demand alert evaluation** (no background poller, no notifications) — a scheduler + a
  `Notification` sink are out of scope (passive consumer by design).
- **No OpenTelemetry / no autoscaling / no monitoring agents** — all out of scope.
