import type { ComponentType } from "react";
import { Navigate, Route, Routes } from "react-router";

import { AppShell } from "../components/AppShell";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { Loading } from "../components/Loading";
import { AgentEditorPage } from "../features/agents/AgentEditorPage";
import { AgentsPage } from "../features/agents/AgentsPage";
import { SignInPage } from "../features/auth/SignInPage";
import { SignUpPage } from "../features/auth/SignUpPage";
import { CreateOrganizationPage } from "../features/onboarding/CreateOrganizationPage";
import { ConversationDetailPage } from "../features/conversations/ConversationDetailPage";
import { ConversationsPage } from "../features/conversations/ConversationsPage";
import { OverviewPage } from "../features/overview/OverviewPage";
import { PlaygroundPage } from "../features/playground/PlaygroundPage";
import { SettingsPage } from "../features/settings/SettingsPage";
import { UnavailablePage } from "../features/unavailable/UnavailablePage";
import { AuthProvider, useAuth } from "./auth";
import { MetaProvider } from "./meta";
import { NAVIGATION } from "./navigation";

/** Implemented surfaces, by path. Everything else in NAVIGATION is "not yet available". */
const IMPLEMENTED: Record<string, ComponentType> = {
  "/agents": AgentsPage,
  "/conversations": ConversationsPage,
  "/playground": PlaygroundPage,
  "/settings": SettingsPage,
};

/** The console proper: only for a signed-in user who is working in an organization. */
function Protected() {
  const { state, refresh } = useAuth();
  if (state.status === "loading") return <Loading />;
  if (state.status === "error") {
    return <ErrorState title="Could not load your account" error={state.error} onRetry={() => void refresh()} />;
  }
  if (state.status === "signed_out") return <Navigate to="/login" replace />;
  if (state.me.organization === null) return <CreateOrganizationPage />;
  return <AppShell />;
}

export function App() {
  return (
    <MetaProvider>
      <AuthProvider>
        <Routes>
          <Route path="login" element={<SignInPage />} />
          <Route path="signup" element={<SignUpPage />} />
          <Route element={<Protected />}>
            <Route index element={<OverviewPage />} />
            <Route path="agents/new" element={<AgentEditorPage />} />
            <Route path="agents/:agentId" element={<AgentEditorPage />} />
            <Route path="conversations/:conversationId" element={<ConversationDetailPage />} />
            {NAVIGATION.filter((item) => item.path !== "/").map((item) => {
              const Page = IMPLEMENTED[item.path];
              return (
                <Route
                  key={item.path}
                  path={item.path.slice(1)}
                  element={Page ? <Page /> : <UnavailablePage item={item} />}
                />
              );
            })}
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
      </AuthProvider>
    </MetaProvider>
  );
}
