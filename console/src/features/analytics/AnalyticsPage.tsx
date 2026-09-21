import { type ReactNode, useCallback } from "react";
import { Link, useSearchParams } from "react-router";

import { getJson } from "../../api/client";
import type {
  AgentReport,
  AgentView,
  AnalyticsSummary,
  LatencyFigure,
  LatencyReport,
  SeriesReport,
} from "../../api/types";
import { type Resource, useResource } from "../../api/useResource";
import { EmptyState } from "../../components/EmptyState";
import { ErrorState } from "../../components/ErrorState";
import { Loading } from "../../components/Loading";
import { StatusPill } from "../../components/StatusPill";
import { STATUS_TONE } from "../conversations/format";
import { analyticsQuery, formatDay, formatSeconds, formatValue, LATENCY_LABELS } from "./format";

const PRESETS = [
  { value: "today", label: "Today" },
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "custom", label: "Custom" },
];

function loadAgents(signal: AbortSignal): Promise<{ data: AgentView[] }> {
  return getJson<{ data: AgentView[] }>("/v1/console/agents", signal);
}

/** One panel's three non-data states. Returns null when the data is ready. */
function PanelState({
  resource,
  title,
}: {
  resource: Resource<unknown> & { reload: () => void };
  title: string;
}) {
  if (resource.status === "loading") return <Loading />;
  if (resource.status === "error") {
    return <ErrorState title={`Could not load ${title}`} error={resource.error} onRetry={resource.reload} />;
  }
  return null;
}

function Panel({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <article className="card" aria-labelledby={`${id}-heading`}>
      <h2 id={`${id}-heading`}>{title}</h2>
      {children}
    </article>
  );
}

function Stat({ label, value, note }: { label: string; value: string; note?: string | undefined }) {
  return (
    <div className="stat">
      <dt>{label}</dt>
      <dd>
        <span className="stat__value">{value}</span>
        {note ? <span className="stat__note">{note}</span> : null}
      </dd>
    </div>
  );
}

function SummaryPanel({ resource }: { resource: Resource<AnalyticsSummary> & { reload: () => void } }) {
  return (
    <Panel id="summary" title="Summary">
      <PanelState resource={resource} title="the summary" />
      {resource.status === "ready" ? (
        resource.data.conversations.total === 0 ? (
          <EmptyState title="No conversations in this range">
            <p>
              Talk to an agent in the <Link to="/playground">Playground</Link>, or widen the range.
            </p>
          </EmptyState>
        ) : (
          <dl className="stats">
            <Stat label="Conversations" value={String(resource.data.conversations.total)} />
            <Stat
              label="Turns"
              value={String(resource.data.turns.total)}
              note={
                resource.data.conversations.not_recorded > 0
                  ? `${resource.data.conversations.not_recorded} conversation(s) without recorded events: their turns are unknown`
                  : undefined
              }
            />
            <Stat
              label="Avg duration"
              value={formatSeconds(resource.data.duration.avg_s)}
              note={
                resource.data.duration.unmeasured > 0
                  ? `${resource.data.duration.unmeasured} conversation(s) not ended: no duration yet`
                  : undefined
              }
            />
            <Stat
              label="Avg turns / conversation"
              value={formatValue(resource.data.turns.avg_per_conversation)}
            />
          </dl>
        )
      ) : null}
    </Panel>
  );
}

function UsagePanel({ resource }: { resource: Resource<SeriesReport> & { reload: () => void } }) {
  const buckets = resource.status === "ready" ? resource.data.data : [];
  const peak = Math.max(0, ...buckets.map((b) => b.conversations));
  return (
    <Panel id="usage" title="Usage over time">
      <PanelState resource={resource} title="usage" />
      {resource.status === "ready" ? (
        peak === 0 ? (
          <EmptyState title="No conversations in this range" />
        ) : (
          <ol className="bars" aria-label="Conversations per day (UTC)">
            {buckets.map((b) => (
              <li key={b.date} className="bars__row">
                <span className="bars__label">{formatDay(b.date)}</span>
                <span className="bars__track" aria-hidden="true">
                  <span className="bars__fill" style={{ width: `${(b.conversations / peak) * 100}%` }} />
                </span>
                <span className="bars__value">
                  {b.conversations} <span className="muted">· {b.turns} turns</span>
                </span>
              </li>
            ))}
          </ol>
        )
      ) : null}
    </Panel>
  );
}

function LatencyRow({ figure }: { figure: LatencyFigure }) {
  return (
    <tr>
      <th scope="row">{LATENCY_LABELS[figure.metric] ?? figure.metric}</th>
      <td>{figure.samples}</td>
      <td>{figure.missing}</td>
      <td>{formatValue(figure.p50_ms, " ms")}</td>
      <td>{formatValue(figure.p95_ms, " ms")}</td>
      <td>{formatValue(figure.avg_ms, " ms")}</td>
      <td>{formatValue(figure.min_ms, " ms")}</td>
      <td>{formatValue(figure.max_ms, " ms")}</td>
    </tr>
  );
}

function LatencyPanel({ resource }: { resource: Resource<LatencyReport> & { reload: () => void } }) {
  const figures = resource.status === "ready" ? resource.data.data : [];
  const turns = figures[0]?.turns ?? 0;
  return (
    <Panel id="latency" title="Latency">
      <PanelState resource={resource} title="latency" />
      {resource.status === "ready" ? (
        turns === 0 ? (
          <EmptyState title="No turns in this range">
            <p>Latency is measured per turn, so it appears once a conversation has one.</p>
          </EmptyState>
        ) : (
          <>
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Measure</th>
                    <th scope="col">Samples</th>
                    <th scope="col">Not measured</th>
                    <th scope="col">P50</th>
                    <th scope="col">P95</th>
                    <th scope="col">Average</th>
                    <th scope="col">Min</th>
                    <th scope="col">Max</th>
                  </tr>
                </thead>
                <tbody>
                  {figures.map((f) => (
                    <LatencyRow key={f.metric} figure={f} />
                  ))}
                </tbody>
              </table>
            </div>
            <p className="note">
              Over {turns} turns. A turn the runtime could not measure is counted under “Not measured” and never
              as 0. Percentiles are nearest-rank over the samples.
            </p>
          </>
        )
      ) : null}
    </Panel>
  );
}

function AgentsPanel({ resource }: { resource: Resource<AgentReport> & { reload: () => void } }) {
  return (
    <Panel id="agents" title="Agents">
      <PanelState resource={resource} title="agent usage" />
      {resource.status === "ready" ? (
        resource.data.data.length === 0 ? (
          <EmptyState title="No agent had a conversation in this range" />
        ) : (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Agent</th>
                  <th scope="col">Conversations</th>
                  <th scope="col">Turns</th>
                  <th scope="col">Avg duration</th>
                  <th scope="col">Avg turns</th>
                  <th scope="col">Voice-to-voice P50</th>
                </tr>
              </thead>
              <tbody>
                {resource.data.data.map((a) => (
                  <tr key={a.id}>
                    <th scope="row">
                      <Link to={`/conversations?agent=${encodeURIComponent(a.id)}`}>
                        {a.name ?? "deleted agent"}
                      </Link>{" "}
                      {a.archived ? <StatusPill tone="neutral">archived</StatusPill> : null}
                    </th>
                    <td>{a.conversations}</td>
                    <td>{a.turns}</td>
                    <td>{formatSeconds(a.avg_duration_s)}</td>
                    <td>{formatValue(a.avg_turns_per_conversation)}</td>
                    <td>
                      {formatValue(a.voice_to_voice.p50_ms, " ms")}{" "}
                      <span className="muted">({a.voice_to_voice.samples} samples)</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : null}
    </Panel>
  );
}

function Breakdown({ label, counts }: { label: string; counts: Record<string, number> }) {
  return (
    <div>
      <h3 className="breakdown__title">{label}</h3>
      <ul className="breakdown" aria-label={label}>
        {Object.entries(counts).map(([key, count]) => (
          <li key={key}>
            <StatusPill tone={STATUS_TONE[key] ?? "neutral"}>{key}</StatusPill>
            <span className="breakdown__count">{count}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function OutcomesPanel({ resource }: { resource: Resource<AnalyticsSummary> & { reload: () => void } }) {
  return (
    <Panel id="outcomes" title="Outcomes">
      <PanelState resource={resource} title="outcomes" />
      {resource.status === "ready" ? (
        resource.data.conversations.total === 0 ? (
          <EmptyState title="No conversations in this range" />
        ) : (
          <>
            <div className="breakdowns">
              <Breakdown label="Conversations by status" counts={resource.data.conversations.by_status} />
              <Breakdown label="Turns by outcome" counts={resource.data.turns.by_status} />
            </div>
            <p className="note">
              The runtime records each conversation’s lifecycle state, not why it ended, so no end reason is shown.
            </p>
          </>
        )
      ) : null}
    </Panel>
  );
}

/** The four reads, made only once the query is complete. Each panel has its own states. */
function AnalyticsPanels({ query }: { query: string }) {
  const summary = useResource(
    useCallback(
      (signal: AbortSignal) => getJson<AnalyticsSummary>(`/v1/console/analytics/summary${query}`, signal),
      [query],
    ),
  );
  const latency = useResource(
    useCallback(
      (signal: AbortSignal) => getJson<LatencyReport>(`/v1/console/analytics/latency${query}`, signal),
      [query],
    ),
  );
  const series = useResource(
    useCallback(
      (signal: AbortSignal) => getJson<SeriesReport>(`/v1/console/analytics/timeseries${query}`, signal),
      [query],
    ),
  );
  const usage = useResource(
    useCallback(
      (signal: AbortSignal) => getJson<AgentReport>(`/v1/console/analytics/agents${query}`, signal),
      [query],
    ),
  );
  const range = summary.status === "ready" ? summary.data.query.range : null;

  return (
    <>
      <p className="note range-note">
        {range
          ? `${formatDay(range.first_day)} – ${formatDay(range.last_day)}: whole days in UTC, each conversation counted on the day it started.`
          : "Days are in UTC."}
      </p>
      <SummaryPanel resource={summary} />
      <UsagePanel resource={series} />
      <LatencyPanel resource={latency} />
      <div className="grid">
        <AgentsPanel resource={usage} />
        <OutcomesPanel resource={summary} />
      </div>
    </>
  );
}

export function AnalyticsPage() {
  const [params, setParams] = useSearchParams();
  const preset = params.get("range") ?? (params.get("from") || params.get("to") ? "custom" : "7d");
  const from = params.get("from") ?? "";
  const to = params.get("to") ?? "";
  const agentId = params.get("agent") ?? "";
  const source = params.get("source") ?? "";
  const custom = preset === "custom";
  // A custom range is only asked for once both days are chosen.
  const ready = !custom || (from !== "" && to !== "");
  const query = analyticsQuery({ preset, from, to, agentId, source });

  const agents = useResource(loadAgents);

  const set = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    setParams(next, { replace: true });
  };

  return (
    <section className="page">
      <header className="page__header">
        <h1>Analytics</h1>
        <p className="page__lede">
          Conversations, turns and latency, computed from what the runtime recorded. Anything it did not measure says
          so.
        </p>
      </header>

      <div className="filters">
        <div className="form__field">
          <label htmlFor="filter-range">Range</label>
          <select
            id="filter-range"
            value={preset}
            onChange={(e) => set(e.target.value === "custom" ? { range: "custom" } : { range: e.target.value, from: "", to: "" })}
          >
            {PRESETS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </div>
        {custom ? (
          <>
            <div className="form__field">
              <label htmlFor="filter-from">From (UTC)</label>
              <input id="filter-from" type="date" value={from} onChange={(e) => set({ from: e.target.value })} />
            </div>
            <div className="form__field">
              <label htmlFor="filter-to">To (UTC)</label>
              <input id="filter-to" type="date" value={to} onChange={(e) => set({ to: e.target.value })} />
            </div>
          </>
        ) : null}
        <div className="form__field">
          <label htmlFor="filter-agent">Agent</label>
          <select id="filter-agent" value={agentId} onChange={(e) => set({ agent: e.target.value })}>
            <option value="">All agents</option>
            {agents.status === "ready"
              ? agents.data.data.map((agent) => (
                  <option key={agent.id} value={agent.id}>
                    {agent.name}
                  </option>
                ))
              : null}
          </select>
        </div>
        <div className="form__field">
          <label htmlFor="filter-source">Source</label>
          <select id="filter-source" value={source} onChange={(e) => set({ source: e.target.value })}>
            <option value="">Any source</option>
            <option value="playground">Playground</option>
            <option value="api">API</option>
          </select>
        </div>
      </div>

      {!ready ? (
        <EmptyState title="Choose both days">
          <p>A custom range needs a first and a last day (UTC, both included).</p>
        </EmptyState>
      ) : (
        <AnalyticsPanels query={query} />
      )}
    </section>
  );
}
