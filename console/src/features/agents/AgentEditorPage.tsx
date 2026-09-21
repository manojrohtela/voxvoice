import { useCallback, useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { getJson, patchJson, postJson, postNoContent } from "../../api/client";
import type { AgentInput, AgentOptions, AgentView } from "../../api/types";
import { useResource } from "../../api/useResource";
import { useMe } from "../../app/auth";
import { ErrorState } from "../../components/ErrorState";
import { FormError } from "../../components/FormError";
import { Loading } from "../../components/Loading";
import { useSubmit } from "../../components/useSubmit";
import { canEditAgents } from "./roles";

const EMPTY: AgentInput = {
  name: "",
  system_prompt: "",
  first_message: null,
  language: "",
  llm_model: null,
  tts_voice: null,
};

function loadOptions(signal: AbortSignal): Promise<AgentOptions> {
  return getJson<AgentOptions>("/v1/console/agents/options", signal);
}

function toInput(agent: AgentView): AgentInput {
  const { name, system_prompt, first_message, language, llm_model, tts_voice } = agent;
  return { name, system_prompt, first_message, language, llm_model, tts_voice };
}

/** Only what changed, so an untouched unsupported language is never re-sent (and refused). */
function changes(before: AgentInput, after: AgentInput): Partial<AgentInput> {
  const diff: Partial<AgentInput> = {};
  for (const key of Object.keys(after) as (keyof AgentInput)[]) {
    if (before[key] !== after[key]) (diff as Record<string, unknown>)[key] = after[key];
  }
  return diff;
}

/** A labelled control whose hint is announced as its description, not as part of its name. */
function Field({ id, label, hint, children }: { id: string; label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="form__field">
      <label htmlFor={id}>{label}</label>
      {children}
      {hint ? (
        <span id={`${id}-hint`} className="form__hint">
          {hint}
        </span>
      ) : null}
    </div>
  );
}

function orNull(value: string): string | null {
  return value.trim() === "" ? null : value;
}

function AgentForm({
  agent,
  options,
  readOnly,
}: {
  agent: AgentView | null;
  options: AgentOptions;
  readOnly: boolean;
}) {
  const navigate = useNavigate();
  const initial = agent ? toInput(agent) : { ...EMPTY, language: options.default_language };
  const [form, setForm] = useState<AgentInput>(initial);
  const [saved, setSaved] = useState<AgentInput>(initial);
  const [notice, setNotice] = useState<string | null>(null);
  const { pending, error, run } = useSubmit();
  const archive = useSubmit();

  const set = <K extends keyof AgentInput>(key: K, value: AgentInput[K]) => {
    setNotice(null);
    setForm((current) => ({ ...current, [key]: value }));
  };
  const languageSupported = options.languages.includes(form.language);
  const dirty = Object.keys(changes(saved, form)).length > 0;

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      if (agent === null) {
        const created = await postJson<AgentView>("/v1/console/agents", form);
        navigate(`/agents/${created.id}`, { replace: true });
        return;
      }
      const updated = await patchJson<AgentView>(`/v1/console/agents/${agent.id}`, changes(saved, form));
      const next = toInput(updated);
      setForm(next);
      setSaved(next);
      setNotice("Saved");
    });
  };

  const onArchive = () => {
    if (agent === null || !window.confirm(`Archive ${agent.name}? It disappears from the console; its conversations are kept.`)) {
      return;
    }
    void archive.run(async () => {
      await postNoContent(`/v1/console/agents/${agent.id}/archive`);
      navigate("/agents", { replace: true });
    });
  };

  return (
    <form className="form agent-form" onSubmit={onSubmit}>
      {!languageSupported ? (
        <div className="banner banner--warn" role="status">
          The speech recognizer does not support <code>{form.language}</code>, so calls to this agent would end
          after the greeting. Choose a supported language.
        </div>
      ) : null}
      <fieldset className="agent-form__fields" disabled={readOnly || pending}>
        <Field id="agent-name" label="Name">
          <input id="agent-name" required maxLength={100} value={form.name} onChange={(e) => set("name", e.target.value)} />
        </Field>
        <Field id="agent-instructions" label="Instructions" hint="The system prompt: who the agent is and how it should answer.">
          <textarea
            id="agent-instructions"
            aria-describedby="agent-instructions-hint"
            required
            rows={8}
            maxLength={20000}
            value={form.system_prompt}
            onChange={(e) => set("system_prompt", e.target.value)}
          />
        </Field>
        <Field id="agent-greeting" label="Greeting" hint="Spoken as soon as the call connects. Leave empty to wait for the caller.">
          <input
            id="agent-greeting"
            aria-describedby="agent-greeting-hint"
            maxLength={2000}
            value={form.first_message ?? ""}
            onChange={(e) => set("first_message", orNull(e.target.value))}
          />
        </Field>
        <Field id="agent-language" label="Language" hint="Only languages the configured speech recognizer declares.">
          <select
            id="agent-language"
            aria-describedby="agent-language-hint"
            value={form.language}
            onChange={(e) => set("language", e.target.value)}
          >
            {!languageSupported ? <option value={form.language}>{`${form.language} (not supported)`}</option> : null}
            {options.languages.map((language) => (
              <option key={language} value={language}>
                {language}
              </option>
            ))}
          </select>
        </Field>
        <Field
          id="agent-model"
          label="Model"
          hint="A model id your language-model account offers. Leave empty for the server default."
        >
          <input
            id="agent-model"
            aria-describedby="agent-model-hint"
            maxLength={200}
            placeholder="Server default"
            value={form.llm_model ?? ""}
            onChange={(e) => set("llm_model", orNull(e.target.value))}
          />
        </Field>
        <Field
          id="agent-voice"
          label="Voice"
          hint="A voice id from your speech-synthesis account. Leave empty for the server default."
        >
          <input
            id="agent-voice"
            aria-describedby="agent-voice-hint"
            maxLength={200}
            placeholder="Server default"
            value={form.tts_voice ?? ""}
            onChange={(e) => set("tts_voice", orNull(e.target.value))}
          />
        </Field>
      </fieldset>
      <FormError error={error ?? archive.error} />
      {readOnly ? (
        <p className="note">You have read-only access to agents in this organization.</p>
      ) : (
        <div className="form__actions">
          <button type="submit" className="button button--primary" disabled={pending || (agent !== null && !dirty)}>
            {agent === null ? "Create agent" : pending ? "Saving…" : "Save changes"}
          </button>
          {notice ? (
            <span className="form__notice" role="status">
              {notice}
            </span>
          ) : null}
          {agent !== null ? (
            <button type="button" className="button button--danger" onClick={onArchive} disabled={archive.pending}>
              Archive
            </button>
          ) : null}
        </div>
      )}
    </form>
  );
}

export function AgentEditorPage() {
  const { agentId } = useParams();
  const me = useMe();
  const readOnly = me.organization === null || !canEditAgents(me.organization.role);
  const options = useResource(loadOptions);
  const loadAgent = useCallback(
    (signal: AbortSignal) =>
      agentId ? getJson<AgentView>(`/v1/console/agents/${agentId}`, signal) : Promise.resolve(null),
    [agentId],
  );
  const agent = useResource(loadAgent);

  const title = agentId === undefined ? "New agent" : agent.status === "ready" && agent.data ? agent.data.name : "Agent";

  return (
    <section className="page">
      <header className="page__header">
        <p className="breadcrumb">
          <Link to="/agents">Agents</Link>
        </p>
        <h1>{title}</h1>
        {agent.status === "ready" && agent.data ? (
          <p className="page__lede">
            <code>{agent.data.id}</code>
          </p>
        ) : null}
      </header>
      {options.status === "loading" || agent.status === "loading" ? <Loading /> : null}
      {options.status === "error" ? (
        <ErrorState title="Could not load agent options" error={options.error} onRetry={options.reload} />
      ) : null}
      {agent.status === "error" ? (
        <ErrorState title="Could not load this agent" error={agent.error} onRetry={agent.reload} />
      ) : null}
      {options.status === "ready" && agent.status === "ready" ? (
        <div className="card">
          <AgentForm key={agent.data?.id ?? "new"} agent={agent.data} options={options.data} readOnly={readOnly} />
        </div>
      ) : null}
    </section>
  );
}
