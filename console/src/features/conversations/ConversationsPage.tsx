import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { ApiError, getJson } from "../../api/client";
import type { AgentView, ConversationPage, ConversationSummary } from "../../api/types";
import { useResource } from "../../api/useResource";
import { EmptyState } from "../../components/EmptyState";
import { ErrorState } from "../../components/ErrorState";
import { Loading } from "../../components/Loading";
import { StatusPill } from "../../components/StatusPill";
import { formatDuration, formatWhen, STATUS_TONE } from "./format";

const STATUSES = ["ended", "active", "failed"];

function loadAgents(signal: AbortSignal): Promise<{ data: AgentView[] }> {
  return getJson<{ data: AgentView[] }>("/v1/console/agents", signal);
}

function query(agentId: string, status: string, cursor?: string): string {
  const params = new URLSearchParams();
  if (agentId) params.set("agent_id", agentId);
  if (status) params.set("status", status);
  if (cursor) params.set("cursor", cursor);
  const text = params.toString();
  return `/v1/console/conversations${text ? `?${text}` : ""}`;
}

export function ConversationsPage() {
  const [params, setParams] = useSearchParams();
  const agentId = params.get("agent") ?? "";
  const status = params.get("status") ?? "";
  const agents = useResource(loadAgents);
  const loadFirst = useCallback(
    (signal: AbortSignal) => getJson<ConversationPage>(query(agentId, status), signal),
    [agentId, status],
  );
  const first = useResource(loadFirst);
  const [more, setMore] = useState<{ rows: ConversationSummary[]; cursor: string | null } | null>(null);
  const [moreError, setMoreError] = useState<ApiError | null>(null);

  useEffect(() => setMore(null), [agentId, status]);

  const setFilter = (key: "agent" | "status", value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const rows = first.status === "ready" ? [...first.data.data, ...(more?.rows ?? [])] : [];
  const cursor = more ? more.cursor : first.status === "ready" ? first.data.next_cursor : null;

  const loadMore = async () => {
    if (!cursor) return;
    try {
      const page = await getJson<ConversationPage>(query(agentId, status, cursor));
      setMore({ rows: [...(more?.rows ?? []), ...page.data], cursor: page.next_cursor });
      setMoreError(null);
    } catch (error) {
      setMoreError(error instanceof ApiError ? error : new ApiError(0, "internal", "Could not load more"));
    }
  };

  return (
    <section className="page">
      <header className="page__header">
        <h1>Conversations</h1>
        <p className="page__lede">Every session with your agents: who called, how it went, and how fast it answered.</p>
      </header>

      <div className="filters">
        <div className="form__field">
          <label htmlFor="filter-agent">Agent</label>
          <select id="filter-agent" value={agentId} onChange={(e) => setFilter("agent", e.target.value)}>
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
          <label htmlFor="filter-status">Status</label>
          <select id="filter-status" value={status} onChange={(e) => setFilter("status", e.target.value)}>
            <option value="">Any status</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
      </div>

      {first.status === "loading" ? <Loading /> : null}
      {first.status === "error" ? (
        <ErrorState title="Could not load conversations" error={first.error} onRetry={first.reload} />
      ) : null}
      {first.status === "ready" && rows.length === 0 ? (
        <EmptyState title={agentId || status ? "No conversations match these filters" : "No conversations yet"}>
          <p>
            Start a call in the <Link to="/playground">Playground</Link>; it appears here as soon as it begins.
          </p>
        </EmptyState>
      ) : null}
      {rows.length > 0 ? (
        <div className="card table-card">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Started</th>
                <th scope="col">Agent</th>
                <th scope="col">Status</th>
                <th scope="col">Duration</th>
                <th scope="col">Messages</th>
                <th scope="col">Source</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id}>
                  <td>
                    <Link to={`/conversations/${c.id}`}>{formatWhen(c.started_at)}</Link>
                  </td>
                  <td>{c.agent.name ?? <span className="muted">deleted agent</span>}</td>
                  <td>
                    <StatusPill tone={STATUS_TONE[c.status] ?? "neutral"}>{c.status}</StatusPill>
                  </td>
                  <td>{formatDuration(c.duration_s)}</td>
                  <td>{c.message_count}</td>
                  <td>{c.source === "console_playground" ? "Playground" : "API"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {moreError ? <p className="form__error" role="alert">{moreError.message}</p> : null}
          {cursor ? (
            <button type="button" className="button button--secondary" onClick={() => void loadMore()}>
              Load more
            </button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
