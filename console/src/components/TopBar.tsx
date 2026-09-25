import { useLocation, useNavigate } from "react-router";

import { useAuth, useMe } from "../app/auth";
import { useConsoleMeta } from "../app/meta";
import { StatusPill } from "./StatusPill";

/**
 * What the transport is, and nothing more (M12.12 defect 1).
 *
 * `/v1/console/meta` carries no provider information — deliberately, so that `api/**` never names
 * a vendor (`E11-3`) — so the console cannot know whether the speech providers behind a WebRTC
 * media path are the real ones or a test double. It must not imply that they are.
 */
export function transportLabel(transport: string): string {
  if (transport === "webrtc") return "WebRTC transport";
  if (transport === "local") return "Loopback transport · no audio path";
  return transport;
}

function OrganizationSwitcher() {
  const me = useMe();
  const { selectOrganization } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  if (me.organization === null) return null;
  const switchTo = async (organizationId: string) => {
    await selectOrganization(organizationId);
    // Filters in the URL (an agent id, say) belong to the previous organization.
    if (location.search) navigate(location.pathname, { replace: true });
  };
  if (me.memberships.length < 2) return <span className="topbar__org">{me.organization.name}</span>;
  return (
    <label className="topbar__org">
      <span className="visually-hidden">Organization</span>
      <select value={me.organization.id} onChange={(e) => void switchTo(e.target.value)}>
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
