import { type FormEvent, useCallback, useState } from "react";
import { Link } from "react-router";

import { getJson, postJson, postNoContent } from "../../api/client";
import type { ApiKeyCreated, ApiKeyView } from "../../api/types";
import { useResource } from "../../api/useResource";
import { useMe } from "../../app/auth";
import { EmptyState } from "../../components/EmptyState";
import { ErrorState } from "../../components/ErrorState";
import { FormError } from "../../components/FormError";
import { Loading } from "../../components/Loading";
import { useSubmit } from "../../components/useSubmit";
import { formatWhen } from "../conversations/format";
import { canManageKeys } from "./roles";

/**
 * The one place a developer API key is ever visible: the create response, held only in this
 * component's state until "Done". It is never written to storage, a URL, history or a log, and
 * nothing can fetch it again — the server keeps only a hash.
 */
function NewKey({ created, onDone }: { created: ApiKeyCreated; onDone: () => void }) {
  const [copied, setCopied] = useState<"idle" | "copied" | "failed">("idle");

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(created.key);
      setCopied("copied");
    } catch {
      setCopied("failed"); // the field stays selectable for a manual copy
    }
  };

  return (
    <article className="card new-key" aria-labelledby="new-key-heading">
      <h2 id="new-key-heading">Copy your new key now</h2>
      <p className="banner banner--warn" role="status">
        This key is shown only once. Store it in your secret manager or server environment. You cannot
        see it again; if it is lost, create a new key and revoke this one.
      </p>
      <label className="form__field" htmlFor="new-key-value">
        {created.label}
        <input
          id="new-key-value"
          className="new-key__value"
          readOnly
          value={created.key}
          autoComplete="off"
          spellCheck={false}
          onFocus={(e) => e.currentTarget.select()}
        />
      </label>
      <div className="form__actions">
        <button type="button" className="button button--primary" onClick={() => void copy()}>
          Copy key
        </button>
        {copied === "copied" ? <span className="form__notice">Copied</span> : null}
        {copied === "failed" ? (
          <span className="muted">Copying is not available here: select the key and copy it manually.</span>
        ) : null}
        <button type="button" className="button button--secondary" onClick={onDone}>
          Done
        </button>
      </div>
    </article>
  );
}

function loadKeys(signal: AbortSignal): Promise<{ data: ApiKeyView[] }> {
  return getJson<{ data: ApiKeyView[] }>("/v1/console/api-keys", signal);
}

function KeyManager() {
  const keys = useResource(useCallback(loadKeys, []));
  const [label, setLabel] = useState("");
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  const create = useSubmit();
  const revoke = useSubmit();

  const onCreate = (event: FormEvent) => {
    event.preventDefault();
    void create.run(async () => {
      const key = await postJson<ApiKeyCreated>("/v1/console/api-keys", { label });
      setCreated(key);
      setLabel("");
      keys.reload();
    });
  };

  const onRevoke = (key: ApiKeyView) => {
    if (!window.confirm(`Revoke "${key.label}"? Anything using it stops working immediately. This cannot be undone.`)) {
      return;
    }
    void revoke.run(async () => {
      await postNoContent(`/v1/console/api-keys/${encodeURIComponent(key.id)}/revoke`);
      keys.reload();
    });
  };

  return (
    <>
      {created ? <NewKey created={created} onDone={() => setCreated(null)} /> : null}

      <article className="card" aria-labelledby="create-key-heading">
        <h2 id="create-key-heading">Create a key</h2>
        <form className="form form--inline" onSubmit={onCreate}>
          <div className="form__field">
            <label htmlFor="key-label">Label</label>
            <input
              id="key-label"
              value={label}
              maxLength={100}
              required
              placeholder="e.g. billing-backend"
              aria-describedby="key-label-hint"
              onChange={(e) => setLabel(e.target.value)}
            />
            <span id="key-label-hint" className="form__hint">
              Where this key will be used, so you know what breaks if you revoke it.
            </span>
          </div>
          <FormError error={create.error} />
          <div className="form__actions">
            <button type="submit" className="button button--primary" disabled={create.pending || !label.trim()}>
              Create key
            </button>
          </div>
        </form>
      </article>

      <article className="card table-card" aria-labelledby="keys-heading">
        <h2 id="keys-heading">Active keys</h2>
        {keys.status === "loading" ? <Loading /> : null}
        {keys.status === "error" ? (
          <ErrorState title="Could not load API keys" error={keys.error} onRetry={keys.reload} />
        ) : null}
        {keys.status === "ready" && keys.data.data.length === 0 ? (
          <EmptyState title="No API keys yet">
            <p>Create one above to call the developer API from your server.</p>
          </EmptyState>
        ) : null}
        {keys.status === "ready" && keys.data.data.length > 0 ? (
          <>
            <FormError error={revoke.error} />
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Label</th>
                  <th scope="col">Key ID</th>
                  <th scope="col">Created</th>
                  <th scope="col">Last used</th>
                  <th scope="col">
                    <span className="visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {keys.data.data.map((key) => (
                  <tr key={key.id}>
                    <th scope="row">{key.label}</th>
                    <td>
                      <code>{key.id}</code>
                    </td>
                    <td>{formatWhen(key.created_at)}</td>
                    <td>{key.last_used_at ? formatWhen(key.last_used_at) : <span className="muted">never</span>}</td>
                    <td>
                      <button
                        type="button"
                        className="button button--small button--secondary"
                        disabled={revoke.pending}
                        onClick={() => onRevoke(key)}
                        aria-label={`Revoke ${key.label}`}
                      >
                        Revoke
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="note">
              The key ID identifies a key; it is not the key and cannot authenticate. Revoked keys are removed
              and stop working at once.
            </p>
          </>
        ) : null}
      </article>
    </>
  );
}

export function ApiKeysPage() {
  const me = useMe();
  const role = me.organization?.role;
  const manage = role !== undefined && canManageKeys(role);

  return (
    <section className="page">
      <header className="page__header">
        <h1>API keys</h1>
        <p className="page__lede">
          Keys let your server call the VoxVoice developer API for this organization. They are not provider keys,
          and they never belong in a browser. <Link to="/developers/docs">Read the developer docs</Link>.
        </p>
      </header>
      {manage ? (
        <KeyManager />
      ) : (
        <EmptyState title="Only an owner or admin can manage API keys">
          <p>
            Ask an owner or admin of {me.organization?.name ?? "this organization"} for a key. The{" "}
            <Link to="/developers/docs">developer docs</Link> explain how to use one.
          </p>
        </EmptyState>
      )}
    </section>
  );
}
