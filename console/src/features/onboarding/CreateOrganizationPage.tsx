import { useState, type FormEvent } from "react";

import { useAuth, useMe } from "../../app/auth";
import { FormError } from "../../components/FormError";
import { useSubmit } from "../../components/useSubmit";
import { AuthLayout } from "../auth/AuthLayout";

/** Shown when the signed-in user has no active organization: everything else is scoped to one. */
export function CreateOrganizationPage() {
  const me = useMe();
  const { createOrganization, selectOrganization, signOut } = useAuth();
  const [name, setName] = useState("");
  const { pending, error, run } = useSubmit();

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void run(() => createOrganization(name));
  };

  return (
    <AuthLayout title="Choose an organization" lede={`Signed in as ${me.user.email}. Agents, sessions and keys all belong to an organization.`}>
      {me.memberships.length > 0 ? (
        <ul className="org-list">
          {me.memberships.map((org) => (
            <li key={org.id}>
              <button type="button" className="button button--secondary" onClick={() => void run(() => selectOrganization(org.id))}>
                {org.name} <span className="muted">· {org.role}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <form className="form" onSubmit={onSubmit}>
        <label className="form__field">
          New organization name
          <input required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <FormError error={error} />
        <button type="submit" className="button button--primary" disabled={pending}>
          Create organization
        </button>
      </form>
      <p className="auth__alt">
        <button type="button" className="link-button" onClick={() => void signOut()}>
          Sign out
        </button>
      </p>
    </AuthLayout>
  );
}
