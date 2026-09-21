import { Outlet, useLocation } from "react-router";

import { useMe } from "../app/auth";

import { ErrorBoundary } from "./ErrorBoundary";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

export function AppShell() {
  const location = useLocation();
  const me = useMe();

  return (
    <div className="shell">
      <Sidebar />
      <div className="shell__main">
        <TopBar />
        <main className="content">
          {/* Keyed by path so a failed view recovers as soon as the user navigates away, and by
              organization so switching remounts the view: nothing of the previous organization
              stays on screen, and every view reloads for the new one. */}
          <ErrorBoundary key={`${me.organization?.id ?? "none"}:${location.pathname}`}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}
