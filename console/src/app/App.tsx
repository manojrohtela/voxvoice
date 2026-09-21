import { Route, Routes } from "react-router";

import { AppShell } from "../components/AppShell";
import { EmptyState } from "../components/EmptyState";
import { OverviewPage } from "../features/overview/OverviewPage";
import { UnavailablePage } from "../features/unavailable/UnavailablePage";
import { MetaProvider } from "./meta";
import { NAVIGATION } from "./navigation";

export function App() {
  return (
    <MetaProvider>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<OverviewPage />} />
          {NAVIGATION.filter((item) => item.path !== "/").map((item) => (
            // Every surface is "not yet available" until its milestone replaces this route.
            <Route key={item.path} path={item.path.slice(1)} element={<UnavailablePage item={item} />} />
          ))}
          <Route
            path="*"
            element={
              <section className="page">
                <EmptyState title="Page not found">
                  <p>Use the navigation to find what you need.</p>
                </EmptyState>
              </section>
            }
          />
        </Route>
      </Routes>
    </MetaProvider>
  );
}
