import { useCallback } from "react";
import { Link, useSearchParams } from "react-router";

import { getJson } from "../../api/client";
import { type AgentView, EXPECTED_CONSOLE_API_REVISION, type Health, type OverviewView } from "../../api/types";
import { useResource } from "../../api/useResource";
import { useMe } from "../../app/auth";
import { hasCapability, useConsoleMeta } from "../../app/meta";
import { NAVIGATION } from "../../app/navigation";
import { EmptyState } from "../../components/EmptyState";
import { ErrorState } from "../../components/ErrorState";
import { Loading } from "../../components/Loading";
import { StatusPill } from "../../components/StatusPill";
import { transportLabel } from "../../components/TopBar";
import { DailyBars } from "../analytics/DailyBars";
import { analyticsQuery, formatDay, formatSeconds, formatValue, LATENCY_LABELS } from "../analytics/format";
import { formatWhen, STATUS_TONE } from "../conversations/format";

function loadHealth(signal: AbortSignal): Promise<Health> {
  return getJson<Health>("/health", signal);
}

function loadAgents(signal: AbortSignal): Promise<{ data: AgentView[] }> {
  return getJson<{ data: AgentView[] }>("/v1/console/agents", signal);
}

// The journey from the product vision, in order. Each step names the surface that delivers it.
const JOURNEY: ReadonlyArray<{ step: string; path: string }> = [
  { step: "Create your account and organization", path: "/settings" },
  { step: "Create an agent", path: "/agents" },
  { step: "Talk to it in the Playground", path: "/playground" },
  { step: "Review the conversation and its latency", path: "/conversations" },
  { step: "Create an API key and integrate", path: "/developers" },
];

const PRESETS = [
  { value: "today", label: "Today" },
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
];

/** The latency measures the snapshot shows (all of them are in Analytics). */
const SNAPSHOT = ["voice_to_voice", "stt", "llm", "tts"];

/** Where each attention item can be acted on. */
const ACTIONS: Record<string, { to: string; label: string }> = {
  no_agents: { to: "/agents/new", label: "Create an agent" },
  unsupported_language: { to: "/agents", label: "Review agents" },
  no_conversations: { to: "/playground", label: "Open the Playground" },
};

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

function Dashboard({ data, analyticsLink }: { data: OverviewView; analyticsLink: string }) {
  const { agents, conversations, duration, turns } = data;
  const latency = SNAPSHOT.map((metric) => data.latency.find((f) => f.metric === metric)).filter(
    (f) => f !== undefined,
  );
  return (
    <>
      {data.attention.length > 0 ? (
        <article className="card attention" aria-labelledby="attention-heading">
          <h2 id="attention-heading">Needs attention</h2>
          <ul className="attention__list">
            {data.attention.map((item) => (
              <li key={item.code}>
                <span>{item.message}</span>
                {ACTIONS[item.code] ? <Link to={ACTIONS[item.code]!.to}>{ACTIONS[item.code]!.label}</Link> : null}
              </li>
            ))}
          </ul>
        </article>
      ) : null}

      <article className="card" aria-labelledby="summary-heading">
        <h2 id="summary-heading">Summary</h2>
        <dl className="stats">
          <Stat
            label="Agents"
            value={String(agents.active)}
            note={agents.archived > 0 ? `${agents.archived} archived` : undefined}
          />
          <Stat
            label="Conversations"
            value={String(conversations.total)}
            note={
              conversations.by_status.active ? `${conversations.by_status.active} in progress` : undefined
            }
          />
          <Stat
            label="Turns"
            value={String(turns.total)}
            note={
              conversations.not_recorded > 0
                ? `${conversations.not_recorded} conversation(s) without recorded events: their turns are unknown`
                : undefined
            }
          />
          <Stat
            label="Avg duration"
            value={formatSeconds(duration.avg_s)}
            note={duration.unmeasured > 0 ? `${duration.unmeasured} not ended: no duration yet` : undefined}
          />
        </dl>
      </article>

      <div className="grid">
        <article className="card" aria-labelledby="usage-heading">
          <h2 id="usage-heading">Usage</h2>
          <DailyBars buckets={data.usage} />
        </article>

        <article className="card" aria-labelledby="latency-heading">
          <h2 id="latency-heading">Latency</h2>
          {turns.total === 0 ? (
            <EmptyState title="No turns in this range">
              <p>Latency is measured per turn.</p>
            </EmptyState>
          ) : (
            <>
              <dl className="facts">
                {latency.map((f) => (
                  <div key={f.metric}>
                    <dt>{LATENCY_LABELS[f.metric] ?? f.metric}</dt>
                    <dd>
                      {formatValue(f.p50_ms, " ms")}{" "}
                      <span className="muted">
                        median · {f.samples} of {f.turns} turns measured
                      </span>
                    </dd>
                  </div>
                ))}
              </dl>
              <p className="note">
                <Link to={analyticsLink}>All latency figures in Analytics</Link>
              </p>
            </>
          )}
        </article>
      </div>

      <div className="grid">
        <article className="card table-card" aria-labelledby="recent-heading">
          <h2 id="recent-heading">Recent conversations</h2>
          {data.recent_conversations.length === 0 ? (
            <EmptyState title="No conversations yet">
              <p>
                Start one in the <Link to="/playground">Playground</Link>.
              </p>
            </EmptyState>
          ) : (
            <>
              <table className="table">
                <caption className="visually-hidden">The latest conversations, any date</caption>
                <thead>
                  <tr>
                    <th scope="col">Started</th>
                    <th scope="col">Agent</th>
                    <th scope="col">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {data.recent_conversations.map((c) => (
                    <tr key={c.id}>
                      <td>
                        <Link to={`/conversations/${c.id}`}>{formatWhen(c.started_at)}</Link>
                      </td>
                      <td>{c.agent.name ?? <span className="muted">deleted agent</span>}</td>
                      <td>
                        <StatusPill tone={STATUS_TONE[c.status] ?? "neutral"}>{c.status}</StatusPill>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="note">
                Latest, of any date. <Link to="/conversations">All conversations</Link>
              </p>
            </>
          )}
        </article>

        <article className="card table-card" aria-labelledby="activity-heading">
          <h2 id="activity-heading">Agent activity</h2>
          {data.agent_activity.length === 0 ? (
            <EmptyState title="No agent had a conversation in this range" />
          ) : (
            <>
              <table className="table">
                <caption className="visually-hidden">Agents by conversations in this range</caption>
                <thead>
                  <tr>
                    <th scope="col">Agent</th>
                    <th scope="col">Conversations</th>
                    <th scope="col">Turns</th>
                    <th scope="col">Avg duration</th>
                  </tr>
                </thead>
                <tbody>
                  {data.agent_activity.map((a) => (
                    <tr key={a.id}>
                      <th scope="row">
                        {/* An archived agent has no editor page; its history is in Conversations. */}
                        <Link to={a.archived ? `/conversations?agent=${encodeURIComponent(a.id)}` : `/agents/${a.id}`}>
                          {a.name ?? "deleted agent"}
                        </Link>{" "}
                        {a.archived ? <StatusPill tone="neutral">archived</StatusPill> : null}
                      </th>
                      <td>{a.conversations}</td>
                      <td>{a.turns}</td>
                      <td>{formatSeconds(a.avg_duration_s)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {data.agent_activity_more > 0 ? (
                <p className="note">
                  <Link to={analyticsLink}>{data.agent_activity_more} more in Analytics</Link>
                </p>
              ) : null}
            </>
          )}
        </article>
      </div>
    </>
  );
}

/** One request for the whole dashboard, remade when the query (or, via the shell, the org) changes. */
function OverviewData({ query, analyticsLink }: { query: string; analyticsLink: string }) {
  const overview = useResource(
    useCallback((signal: AbortSignal) => getJson<OverviewView>(`/v1/console/overview${query}`, signal), [query]),
  );
  if (overview.status === "loading") return <Loading label="Loading the overview…" />;
  if (overview.status === "error") {
    return <ErrorState title="Unable to load the overview" error={overview.error} onRetry={overview.reload} />;
  }
  const range = overview.data.query.range;
  return (
    <>
      <p className="note range-note">
        {formatDay(range.first_day)} – {formatDay(range.last_day)} (UTC), from recorded conversations. The figures are
        the ones <Link to={analyticsLink}>Analytics</Link> shows for the same range.
      </p>
      <Dashboard data={overview.data} analyticsLink={analyticsLink} />
    </>
  );
}

export function OverviewPage() {
  const meta = useConsoleMeta();
  const me = useMe();
  const health = useResource(loadHealth);
  const agents = useResource(loadAgents);
  const [params, setParams] = useSearchParams();
  const preset = PRESETS.some((p) => p.value === params.get("range")) ? params.get("range")! : "7d";
  const agentId = params.get("agent") ?? "";
  const query = analyticsQuery({ preset, from: "", to: "", agentId, source: "" });
  const analyticsLink = `/analytics?range=${preset}${agentId ? `&agent=${encodeURIComponent(agentId)}` : ""}`;
  const dashboard = hasCapability(meta, "overview");

  const set = (key: "range" | "agent", value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  return (
    <section className="page">
      <header className="page__header">
        <h1>Overview</h1>
        <p className="page__lede">
          {me.organization ? `${me.organization.name} at a glance` : "Your organization at a glance"}: agents,
          conversations and latency, from what the runtime recorded.
        </p>
      </header>

      {meta.status === "ready" && meta.data.console_api_revision !== EXPECTED_CONSOLE_API_REVISION ? (
        <div className="banner banner--warn" role="status">
          This console was built for API revision <code>{EXPECTED_CONSOLE_API_REVISION}</code> but the
          server reports <code>{meta.data.console_api_revision}</code>. Rebuild the console.
        </div>
      ) : null}

      {dashboard ? (
        <>
          <div className="filters">
            <div className="form__field">
              <label htmlFor="overview-range">Range</label>
              <select id="overview-range" value={preset} onChange={(e) => set("range", e.target.value)}>
                {PRESETS.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="form__field">
              <label htmlFor="overview-agent">Agent</label>
              <select id="overview-agent" value={agentId} onChange={(e) => set("agent", e.target.value)}>
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
          </div>
          <OverviewData query={query} analyticsLink={analyticsLink} />
        </>
      ) : null}

      <div className="grid">
        <article className="card" aria-labelledby="runtime-heading">
          <h2 id="runtime-heading">Runtime</h2>
          {meta.status === "loading" || health.status === "loading" ? <Loading /> : null}
          {meta.status === "error" ? (
            <ErrorState title="Cannot load server details" error={meta.error} onRetry={meta.reload} />
          ) : null}
          {health.status === "error" ? (
            <ErrorState title="Health check failed" error={health.error} onRetry={health.reload} />
          ) : null}
          {meta.status === "ready" && health.status === "ready" ? (
            <dl className="facts">
              <div>
                <dt>Status</dt>
                <dd>
                  {health.data.ok ? (
                    <StatusPill tone="ok">Healthy</StatusPill>
                  ) : (
                    <StatusPill tone="error">Unhealthy</StatusPill>
                  )}
                </dd>
              </div>
              <div>
                <dt>Database</dt>
                <dd>{health.data.db ? "Reachable" : "Unreachable"}</dd>
              </div>
              <div>
                <dt>Transport</dt>
                <dd>{transportLabel(meta.data.runtime.transport)}</dd>
              </div>
              <div>
                <dt>WebRTC signalling</dt>
                <dd>{meta.data.runtime.webrtc_signalling ? "Enabled" : "Disabled"}</dd>
              </div>
              <div>
                <dt>ICE policy</dt>
                <dd>{meta.data.runtime.ice_policy}</dd>
              </div>
              <div>
                <dt>API</dt>
                <dd>
                  <code>{meta.data.api_version}</code> · console <code>{meta.data.console_api_revision}</code>
                </dd>
              </div>
            </dl>
          ) : null}
          {meta.status === "ready" && meta.data.runtime.transport !== "webrtc" ? (
            <p className="note">
              Real voice needs <code>RUNTIME_TRANSPORT=webrtc</code> and provider credentials in the
              server&apos;s environment. See <code>docs/runtime/local-run.md</code>.
            </p>
          ) : null}
          {meta.status === "ready" && meta.data.runtime.transport === "webrtc" ? (
            <p className="note">
              The transport is all this server reports; it says nothing about which speech
              providers are configured.
            </p>
          ) : null}
        </article>

        <article className="card" aria-labelledby="journey-heading">
          <h2 id="journey-heading">Get started</h2>
          <ol className="journey">
            {JOURNEY.map(({ step, path }) => {
              const item = NAVIGATION.find((candidate) => candidate.path === path);
              const available = item !== undefined && hasCapability(meta, item.capability);
              return (
                <li key={path} className={available ? "journey__step" : "journey__step is-pending"}>
                  <span>{step}</span>
                  {available ? (
                    <StatusPill tone="ok">Available</StatusPill>
                  ) : (
                    <StatusPill tone="neutral">{`Arrives in ${item?.milestone ?? "a later milestone"}`}</StatusPill>
                  )}
                </li>
              );
            })}
          </ol>
        </article>
      </div>
    </section>
  );
}
