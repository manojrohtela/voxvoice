import { useCallback } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { getJson } from "../../api/client";
import type { AgentOptions } from "../../api/types";
import { useResource } from "../../api/useResource";
import { useMe } from "../../app/auth";
import { EmptyState } from "../../components/EmptyState";
import { ErrorState } from "../../components/ErrorState";
import { FormError } from "../../components/FormError";
import { Loading } from "../../components/Loading";
import { useSubmit } from "../../components/useSubmit";
import { canEditAgents } from "./roles";
import { createFromTemplate, findTemplate, templateAgentInput, type AgentTemplate } from "./templates";

function loadOptions(signal: AbortSignal): Promise<AgentOptions> {
  return getJson<AgentOptions>("/v1/console/agents/options", signal);
}

/**
 * What this template would create, and the one button that creates it.
 *
 * Everything shown here is the template's own data — no provider identifier, no server
 * configuration, nothing an unauthenticated reader could not be told. The instructions are shown
 * in full because they are exactly what the user is about to own and edit.
 */
function Preview({
  template,
  options,
  canUse,
}: {
  template: AgentTemplate;
  options: AgentOptions;
  canUse: boolean;
}) {
  const navigate = useNavigate();
  const { pending, error, run } = useSubmit();
  const input = templateAgentInput(template, options.languages, options.default_language);

  const use = () =>
    void run(async () => {
      const agent = await createFromTemplate(template, options.languages, options.default_language);
      navigate(`/agents/${agent.id}`);
    });

  return (
    <>
      <div className="grid">
        <article className="card" aria-labelledby="about-heading">
          <h2 id="about-heading">What it does</h2>
          <p>{template.purpose}</p>
          <ul className="template-behaviour">
            {template.behaviour.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <p className="note">{template.recommended_use}</p>
        </article>

        <article className="card" aria-labelledby="settings-heading">
          <h2 id="settings-heading">Starting settings</h2>
          <dl className="facts">
            <div>
              <dt>Agent name</dt>
              <dd>{input.name}</dd>
            </div>
            <div>
              <dt>Language</dt>
              <dd>
                {input.language}
                {input.language !== template.agent.language ? (
                  <span className="muted">
                    {" "}
                    · this server does not offer {template.agent.language}
                  </span>
                ) : null}
              </dd>
            </div>
            <div>
              <dt>Model</dt>
              <dd className="muted">Server default</dd>
            </div>
            <div>
              <dt>Voice</dt>
              <dd className="muted">Server default</dd>
            </div>
          </dl>
          <p className="note">Every one of these is editable once the agent exists.</p>
        </article>
      </div>

      <article className="card" aria-labelledby="greeting-heading">
        <h2 id="greeting-heading">Greeting</h2>
        <p className="template-quote">{input.first_message}</p>
      </article>

      <article className="card" aria-labelledby="instructions-heading">
        <h2 id="instructions-heading">Instructions</h2>
        <pre className="code template-prompt">{input.system_prompt}</pre>
      </article>

      {canUse ? (
        <>
          <FormError error={error} />
          <div className="form__actions">
            <button type="button" className="button button--primary" onClick={use} disabled={pending}>
              {pending ? "Creating…" : "Use this template"}
            </button>
            <Link className="button button--secondary" to="/agents">
              Cancel
            </Link>
          </div>
        </>
      ) : null}
    </>
  );
}

export function TemplatePreviewPage() {
  const { templateId } = useParams();
  const me = useMe();
  const template = findTemplate(templateId);
  const options = useResource(useCallback(loadOptions, []));
  const canUse = me.organization !== null && canEditAgents(me.organization.role);

  if (!template) {
    return (
      <section className="page">
        <header className="page__header">
          <p className="breadcrumb">
            <Link to="/agents">Agents</Link>
          </p>
          <h1>Template not found</h1>
        </header>
        <EmptyState title="No such template">
          <p>
            It may have been renamed or removed. <Link to="/agents">See the templates</Link>.
          </p>
        </EmptyState>
      </section>
    );
  }

  return (
    <section className="page">
      <header className="page__header">
        <p className="breadcrumb">
          <Link to="/agents">Agents</Link> / Starter templates
        </p>
        <h1>{template.name}</h1>
        <p className="page__lede">
          {template.category} · {template.short_description}
        </p>
      </header>
      {!canUse ? (
        <EmptyState title="Only a developer, admin or owner can create agents">
          <p>
            You can read what this template contains, but creating an agent from it needs one of
            those roles in {me.organization?.name ?? "this organization"}.
          </p>
        </EmptyState>
      ) : null}
      {options.status === "loading" ? <Loading /> : null}
      {options.status === "error" ? (
        <ErrorState title="Could not load agent options" error={options.error} onRetry={options.reload} />
      ) : null}
      {options.status === "ready" ? (
        <Preview template={template} options={options.data} canUse={canUse} />
      ) : null}
    </section>
  );
}
