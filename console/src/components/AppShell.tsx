import { Outlet, useLocation } from "react-router";

import { ErrorBoundary } from "./ErrorBoundary";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

export function AppShell() {
  const location = useLocation();

  return (
    <div className="shell">
      <Sidebar />
      <div className="shell__main">
        <TopBar />
        <main className="content">
          {/* Keyed by path so a failed view recovers as soon as the user navigates away. */}
          <ErrorBoundary key={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}
