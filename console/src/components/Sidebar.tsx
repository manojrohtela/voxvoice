import { NavLink } from "react-router";

import { hasCapability, useConsoleMeta } from "../app/meta";
import { NAVIGATION } from "../app/navigation";

export function Sidebar() {
  const meta = useConsoleMeta();
  // Until the capabilities are known, nothing is badged: "unknown" is not "unavailable".
  const settled = meta.status !== "loading";

  return (
    <nav className="sidebar" aria-label="Console">
      <div className="sidebar__brand">
        <span className="sidebar__mark" aria-hidden="true" />
        VoxVoice
      </div>
      <ul className="sidebar__list">
        {NAVIGATION.map((item) => {
          const available = !settled || hasCapability(meta, item.capability);
          return (
            <li key={item.path}>
              <NavLink
                to={item.path}
                end={item.path === "/"}
                className={({ isActive }) =>
                  ["sidebar__link", isActive ? "is-active" : "", available ? "" : "is-unavailable"]
                    .filter(Boolean)
                    .join(" ")
                }
              >
                <span>{item.label}</span>
                {available ? null : (
                  <span className="sidebar__soon" title={`Arrives in ${item.milestone}`}>
                    Soon
                  </span>
                )}
              </NavLink>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
