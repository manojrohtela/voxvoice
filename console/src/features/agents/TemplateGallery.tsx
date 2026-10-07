import { Link } from "react-router";

import { TEMPLATES } from "./templates";

/**
 * The starter templates, on the Agents page below the organization's own agents (M12.13).
 *
 * Deliberately a different shape from an agent row: a template is not something this organization
 * owns, so it must not read as one. Each card leads to its preview, which is where the agent is
 * actually created — so nothing is created by a single stray click.
 */
export function TemplateGallery() {
  return (
    <section className="templates" aria-labelledby="templates-heading">
      <h2 id="templates-heading">Starter templates</h2>
      <p className="note">
        Ready-made starting points. Using one creates an ordinary agent that you can edit, archive
        and call like any other; the template itself never changes.
      </p>
      <ul className="template-grid">
        {TEMPLATES.map((template) => (
          <li key={template.id} className="card template-card">
            <p className="template-card__category">{template.category}</p>
            <h3 className="template-card__name">
              <Link to={`/agents/templates/${template.id}`}>{template.name}</Link>
            </h3>
            <p className="template-card__summary">{template.short_description}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}
