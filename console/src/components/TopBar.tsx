import { useAuth, useMe } from "../app/auth";
import { useConsoleMeta } from "../app/meta";
import { StatusPill } from "./StatusPill";

export function transportLabel(transport: string): string {
  if (transport === "webrtc") return "WebRTC · real providers";
  if (transport === "local") return "Loopback · no real voice";
  return transport;
}

function OrganizationSwitcher() {
  const me = useMe();
  const { selectOrganization } = useAuth();
  if (me.organization === null) return null;
  if (me.memberships.length < 2) return <span className="topbar__org">{me.organization.name}</span>;
  return (
    <label className="topbar__org">
      <span className="visually-hidden">Organization</span>
      <select value={me.organization.id} onChange={(e) => void selectOrganization(e.target.value)}>
        {me.memberships.map((org) => (
          <option key={org.id} value={org.id}>
            {org.name}
          </option>
        ))}
      </select>
    </label>
  );
}

export function TopBar() {
  const meta = useConsoleMeta();
  const me = useMe();
  const { signOut } = useAuth();

  return (
    <header className="topbar">
      <OrganizationSwitcher />
      <span className="topbar__env">Local</span>
      <div className="topbar__spacer" />
      {meta.status === "ready" ? (
        <StatusPill tone={meta.data.runtime.transport === "webrtc" ? "ok" : "neutral"}>
          {transportLabel(meta.data.runtime.transport)}
        </StatusPill>
      ) : meta.status === "error" ? (
        <StatusPill tone="error">API unreachable</StatusPill>
      ) : null}
      <span className="topbar__user" title={me.user.email}>
        {me.user.display_name || me.user.email}
      </span>
      <button type="button" className="button button--secondary button--small" onClick={() => void signOut()}>
        Sign out
      </button>
    </header>
  );
}
