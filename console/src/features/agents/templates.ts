// Starter agent templates (M12.13).
//
// A template is **product data, not a runtime concept**: a set of starting values for the fields
// an ordinary agent already has. "Use this template" calls the same `POST /v1/console/agents` the
// blank form calls, and what comes back is an ordinary agent — editable, archivable, callable in
// the Playground, counted by Analytics like any other. There is no template row, no template
// runtime, no second agent model, and no server change: the definitions are version-controlled
// JSON shipped with the console (`templates.json`), which is also what the platform tests read.
//
// Templates are immutable: `createFromTemplate` sends a copy of the values, so editing the agent
// afterwards cannot reach the template, and two agents made from one template are unrelated.

import { postJson } from "../../api/client";
import type { AgentInput, AgentView } from "../../api/types";

import data from "./templates.json";

export interface AgentTemplate {
  /** Stable identifier. Appears in the URL; never shown as a label. */
  id: string;
  /** Bumped when the starting values change. Agents already created are unaffected. */
  version: number;
  name: string;
  category: string;
  short_description: string;
  purpose: string;
  recommended_use: string;
  /** What the agent does, in the order it does it. Shown on the preview. */
  behaviour: string[];
  /** The starting values, in the vocabulary of the agent API itself. */
  agent: {
    name: string;
    language: string;
    first_message: string;
    system_prompt: string;
  };
}

export const TEMPLATES: readonly AgentTemplate[] = data.templates;

export function findTemplate(id: string | undefined): AgentTemplate | undefined {
  return TEMPLATES.find((template) => template.id === id);
}

/** The categories present, in the order the templates are defined. */
export function categories(): string[] {
  return [...new Set(TEMPLATES.map((template) => template.category))];
}

/**
 * The values this template would create an agent with, on a server declaring `languages`.
 *
 * A template names the language it was written for. If this deployment's recognizer does not
 * declare it, the server's own default is used instead rather than sending a value that would be
 * refused — the one thing that is resolved at creation time, because it is the one thing that is
 * deployment-dependent. Model and voice are deliberately left unset: what a provider account
 * offers is environment-specific, so the agent takes the server default and the editor can change
 * it (`m12-agent-templates.md` §4).
 */
export function templateAgentInput(
  template: AgentTemplate,
  languages: readonly string[],
  defaultLanguage: string,
): AgentInput {
  return {
    name: template.agent.name,
    system_prompt: template.agent.system_prompt,
    first_message: template.agent.first_message,
    language: languages.includes(template.agent.language) ? template.agent.language : defaultLanguage,
    llm_model: null,
    tts_voice: null,
  };
}

/** Creates an ordinary agent. The template is not consulted again after this call. */
export function createFromTemplate(
  template: AgentTemplate,
  languages: readonly string[],
  defaultLanguage: string,
): Promise<AgentView> {
  return postJson<AgentView>(
    "/v1/console/agents",
    templateAgentInput(template, languages, defaultLanguage),
  );
}
