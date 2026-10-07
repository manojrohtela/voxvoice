import { Link } from "react-router";

import { getJson } from "../../api/client";
import type { AgentView } from "../../api/types";
import { useResource } from "../../api/useResource";
import { useMe } from "../../app/auth";
import { EmptyState } from "../../components/EmptyState";
import { ErrorState } from "../../components/ErrorState";
import { Loading } from "../../components/Loading";
import { StatusPill } from "../../components/StatusPill";
import { canEditAgents } from "./roles";
import { TemplateGallery } from "./TemplateGallery";

function loadAgents(signal: AbortSignal): Promise<{ data: AgentView[] }> {
  return getJson<{ data: AgentView[] }>("/v1/console/agents", signal);
}

export function AgentsPage() {
  const me = useMe();
  const agents = useResource(loadAgents);
  const canEdit = me.organization !== null && canEditAgents(me.organization.role);

  return (
    <section className="page">
      <header className="page__header page__header--actions">
        <div>
          <h1>Agents</h1>
          <p className="page__lede">Voice agents in {me.organization?.name}. Each one is what a caller talks to.</p>
        </div>
        {canEdit ? (
          <Link className="button button--primary" to="/agents/new">
            New agent
          </Link>
        ) : null}
      </header>

      {agents.status === "loading" ? <Loading /> : null}
      {agents.status === "error" ? (
        <ErrorState title="Could not load agents" error={agents.error} onRetry={agents.reload} />
      ) : null}
      {agents.status === "ready" && agents.data.data.length === 0 ? (
        <EmptyState title="No agents yet">
          <p>
            {canEdit
              ? "Start from a template below, or create one from scratch: give it instructions and a greeting, then talk to it in the Playground."
              : "Nobody has created an agent in this organization yet."}
          </p>
        </EmptyState>
      ) : null}
      {agents.status === "ready" && agents.data.data.length > 0 ? (
        <ul className="agent-list">
          {agents.data.data.map((agent) => (
            <li key={agent.id} className="card agent-list__item">
              <div className="agent-list__main">
                <Link to={`/agents/${agent.id}`} className="agent-list__name">
                  {agent.name}
                </Link>
                <p className="agent-list__prompt">{agent.system_prompt}</p>
              </div>
              <div className="agent-list__meta">
                {agent.language_supported ? (
                  <StatusPill tone="neutral">{agent.language}</StatusPill>
                ) : (
                  <StatusPill tone="warn">{`${agent.language} · unsupported`}</StatusPill>
                )}
                <code className="agent-list__id">{agent.id}</code>
              </div>
            </li>
          ))}
        </ul>
      ) : null}

      <TemplateGallery />
    </section>
  );
}
