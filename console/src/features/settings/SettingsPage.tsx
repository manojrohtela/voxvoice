import { useState, type FormEvent } from "react";

import { getJson, patchJson } from "../../api/client";
import type { MemberView, OrganizationView } from "../../api/types";
import { useResource } from "../../api/useResource";
import { useAuth, useMe } from "../../app/auth";
import { ErrorState } from "../../components/ErrorState";
import { FormError } from "../../components/FormError";
import { Loading } from "../../components/Loading";
import { useSubmit } from "../../components/useSubmit";

function loadMembers(signal: AbortSignal): Promise<MemberView[]> {
  return getJson<MemberView[]>("/v1/console/organization/members", signal);
}

function OrganizationCard({ organization }: { organization: OrganizationView }) {
  const { refresh } = useAuth();
  const [name, setName] = useState(organization.name);
  const { pending, error, run } = useSubmit();
  const isOwner = organization.role === "owner";

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      await patchJson<OrganizationView>("/v1/console/organization", { name });
      await refresh();
    });
  };

  return (
    <article className="card" aria-labelledby="org-heading">
      <h2 id="org-heading">Organization</h2>
      <dl className="facts">
        <div>
          <dt>ID</dt>
          <dd>
            <code>{organization.id}</code>
          </dd>
        </div>
        <div>
          <dt>Your role</dt>
          <dd>{organization.role}</dd>
        </div>
      </dl>
      {isOwner ? (
        <form className="form form--inline" onSubmit={onSubmit}>
          <label className="form__field">
            Name
            <input required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <FormError error={error} />
          <button type="submit" className="button button--primary" disabled={pending || name === organization.name}>
            Rename
          </button>
        </form>
      ) : (
        <p className="note">Only an owner can rename {organization.name}.</p>
      )}
    </article>
  );
}

function MembersCard() {
  const members = useResource(loadMembers);
  return (
    <article className="card" aria-labelledby="members-heading">
      <h2 id="members-heading">Members</h2>
      {members.status === "loading" ? <Loading /> : null}
      {members.status === "error" ? (
        <ErrorState title="Could not load members" error={members.error} onRetry={members.reload} />
      ) : null}
      {members.status === "ready" ? (
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Member</th>
              <th scope="col">Role</th>
            </tr>
          </thead>
          <tbody>
            {members.data.map((member) => (
              <tr key={member.user_id}>
                <td className="cell--break">
                  {member.display_name ? `${member.display_name} · ` : ""}
                  {member.email}
                </td>
                <td>{member.role}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <p className="note">Inviting teammates arrives in a later milestone.</p>
    </article>
  );
}

function NewOrganizationCard() {
  const { createOrganization } = useAuth();
  const [name, setName] = useState("");
  const { pending, error, run } = useSubmit();

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      await createOrganization(name);
      setName("");
    });
  };

  return (
    <article className="card" aria-labelledby="new-org-heading">
      <h2 id="new-org-heading">New organization</h2>
      <p className="note">You become its owner and the console switches to it.</p>
      <form className="form form--inline" onSubmit={onSubmit}>
        <label className="form__field">
          Name
          <input required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <FormError error={error} />
        <button type="submit" className="button button--secondary" disabled={pending}>
          Create
        </button>
      </form>
    </article>
  );
}

export function SettingsPage() {
  const me = useMe();
  const organization = me.organization;

  return (
    <section className="page">
      <header className="page__header">
        <h1>Settings</h1>
        <p className="page__lede">Your account, your organization and its members.</p>
      </header>
      <div className="grid">
        <article className="card" aria-labelledby="account-heading">
          <h2 id="account-heading">Account</h2>
          <dl className="facts">
            <div>
              <dt>Email</dt>
              <dd>{me.user.email}</dd>
            </div>
            <div>
              <dt>Name</dt>
              <dd>{me.user.display_name || "—"}</dd>
            </div>
          </dl>
        </article>
        {organization ? <OrganizationCard key={organization.id} organization={organization} /> : null}
        {organization ? <MembersCard key={`members-${organization.id}`} /> : null}
        <NewOrganizationCard />
      </div>
    </section>
  );
}
